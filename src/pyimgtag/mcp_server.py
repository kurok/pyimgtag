"""MCP (Model Context Protocol) server exposing the photo library to AI assistants.

The server speaks MCP over stdio and is read-only by default: it registers
query/inspection tools that return compact JSON (paths plus the key fields an
assistant needs to reason), never table text and never full-resolution image
bytes. Thumbnails are the only pixel data that leaves the machine, and only for
paths the database already knows about.

Write tools (``set_tags``, ``set_cleanup_class``, ``rename_person``,
``export_photos``) are registered only when writes are explicitly enabled via
``PYIMGTAG_MCP_ENABLE_WRITES=1`` or ``pyimgtag mcp --enable-writes``. Delete
operations of any kind are not exposed.

Path-safety rules (mirroring the webapp, see ``webapp/routes_review``):

* Only paths resolved through :meth:`ProgressDB.get_known_file_path` are ever
  opened — a client-supplied path is a lookup key, never a filesystem argument.
* ``export_photos`` copies only under the allow-listed ``--export-root``; the
  destination is resolved and checked with :meth:`pathlib.Path.is_relative_to`
  before anything is written.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyimgtag import __version__
from pyimgtag.progress_db import ProgressDB

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.mcpserver import Image, MCPServer

#: Message shown when the optional ``[mcp]`` extra is not installed.
MCP_INSTALL_HINT = (
    "The MCP server requires the 'mcp' extra. Install with: pip install 'pyimgtag[mcp]'"
)

#: Environment variable that enables the gated write tools.
WRITES_ENV_VAR = "PYIMGTAG_MCP_ENABLE_WRITES"

SERVER_NAME = "pyimgtag"

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
#: Same default as ``pyimgtag search --top``.
DEFAULT_SEARCH_LIMIT = 20
DEFAULT_THUMB_SIZE = 256
MAX_THUMB_SIZE = 512
MAX_TOP_N = 50

CLEANUP_CLASSES = ("keep", "review", "delete")

_NO_INDEX_HINT = (
    "No photos are in the semantic index yet. Build it with: "
    "pyimgtag index --input-dir <DIR>  (or use query_photos with tag, city, country "
    "or scene_category filters meanwhile)."
)
_NO_MODELS_HINT = (
    "The CLIP model files are not downloaded. The MCP server does not download them "
    "itself (about 153 MB); run 'pyimgtag search \"anything\"' once in a terminal to "
    "fetch them. If 'pyimgtag index' used --model-dir, start the server with the same "
    "--model-dir (or set PYIMGTAG_SEARCH_MODEL_DIR in the MCP client's env)."
)
_NO_EVENTS_HINT = "No events yet: run 'pyimgtag events detect' to group photos into events."


def writes_enabled_from_env(env: dict[str, str] | None = None) -> bool:
    """Return True when ``PYIMGTAG_MCP_ENABLE_WRITES`` opts into the write tools."""
    source = os.environ if env is None else env
    return source.get(WRITES_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _photo_summary(row: dict) -> dict:
    """Compact per-photo record: path plus the fields an assistant reasons over."""
    return {
        "path": row.get("file_path"),
        "file_name": row.get("file_name"),
        "tags": row.get("tags_list") or row.get("tags") or [],
        "scene_summary": row.get("scene_summary"),
        "scene_category": row.get("scene_category"),
        "cleanup_class": row.get("cleanup_class"),
        "city": row.get("nearest_city"),
        "country": row.get("nearest_country"),
        "date": row.get("image_date"),
        "judge_score": row.get("judge_score"),
    }


def _photo_detail(row: dict) -> dict:
    """Full metadata record for a single photo."""
    detail = _photo_summary(row)
    detail.update(
        {
            "status": row.get("status"),
            "region": row.get("nearest_region"),
            "emotional_tone": row.get("emotional_tone"),
            "event_hint": row.get("event_hint"),
            "significance": row.get("significance"),
            "processed_at": row.get("processed_at"),
            "error_message": row.get("error_message"),
            "judge_verdict": row.get("judge_verdict"),
            "judge_reason": row.get("judge_reason"),
        }
    )
    return detail


#: SQLite integers are signed 64-bit; anything outside cannot be an event id.
_SQLITE_INT_MAX = 2**63 - 1


def _valid_event_id(event_id: object) -> bool:
    return isinstance(event_id, int) and 0 < event_id <= _SQLITE_INT_MAX


def _event_exists(db: ProgressDB, event_id: object) -> bool:
    return _valid_event_id(event_id) and db.get_event(int(event_id)) is not None  # type: ignore[call-overload]


def _no_event(event_id: object) -> dict:
    """Uniform payload for an event id the database does not know."""
    return {
        "error": "no such event",
        "event_id": event_id,
        "hint": "Use list_events to see the event ids.",
    }


def _not_found(path: str) -> dict:
    """Uniform 404-style payload for a path the database does not know."""
    return {
        "error": "not found",
        "path": path,
        "hint": "No such photo in the database. Use query_photos to list known paths.",
    }


def _import_mcp() -> tuple[type[MCPServer], type[Image]]:
    """Import the MCP SDK, converting a missing extra into a friendly error.

    Raises:
        ImportError: With an install hint when the ``[mcp]`` extra is absent.
    """
    try:
        from mcp.server.mcpserver import Image, MCPServer
    except ImportError as exc:  # pragma: no cover - exercised via patched sys.modules
        raise ImportError(MCP_INSTALL_HINT) from exc
    return MCPServer, Image


def build_server(
    db_path: str | Path | None = None,
    *,
    enable_writes: bool = False,
    export_root: str | None = None,
    model_dir: str | Path | None = None,
) -> MCPServer:
    """Build the MCP server exposing the pyimgtag database.

    Args:
        db_path: Progress database to serve. ``None`` uses the default
            ``~/.cache/pyimgtag/progress.db``.
        enable_writes: Register the gated write tools. Read tools are always
            registered.
        export_root: Directory that ``export_photos`` may copy into. When
            ``None``, ``export_photos`` refuses every request.
        model_dir: Folder with the CLIP model files for ``search_photos``;
            ``None`` uses the search model cache.

    Returns:
        A configured MCP server ready for a transport.

    Raises:
        ImportError: If the ``[mcp]`` extra is not installed.
    """
    mcp_server_cls, image_cls = _import_mcp()
    db = ProgressDB(db_path=db_path)
    root = Path(export_root).expanduser().resolve() if export_root else None

    server = mcp_server_cls(
        name=SERVER_NAME,
        version=__version__,
        instructions=(
            "Read-only access to a pyimgtag photo library (tags, places, people, "
            "events, judge scores, thumbnails). Paths returned by these tools are the only "
            "valid input for get_photo and get_thumbnail."
        ),
    )

    _register_read_tools(server, db, image_cls, Path(model_dir) if model_dir else None)
    if enable_writes:
        _register_write_tools(server, db, root)
    return server


def _register_read_tools(
    server: MCPServer, db: ProgressDB, image_cls: type[Image], model_dir: Path | None = None
) -> None:
    """Register the always-available read tools on *server*."""

    @server.tool(
        description=(
            "Search the photo library by metadata filters. Mirrors 'pyimgtag query'. "
            "Returns compact records: path, tags, place, date, judge score. "
            "event_id (from list_events) keeps only that event's photos."
        )
    )
    def query_photos(
        tag: str | None = None,
        tags_any: list[str] | None = None,
        city: str | None = None,
        country: str | None = None,
        scene_category: str | None = None,
        cleanup_class: str | None = None,
        status: str | None = None,
        has_text: bool | None = None,
        min_score: int | None = None,
        max_score: int | None = None,
        judged: bool | None = None,
        event_id: int | None = None,
        sort: str = "path_asc",
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict:
        capped = _clamp(limit, 1, MAX_LIMIT)
        start = max(0, int(offset))
        if event_id is not None and not _event_exists(db, event_id):
            return _no_event(event_id)
        rows = db.query_images(
            tag=tag,
            has_text=has_text,
            cleanup_class=cleanup_class,
            scene_category=scene_category,
            city=city,
            country=country,
            status=status,
            limit=start + capped,
            min_judge_score=min_score,
            max_judge_score=max_score,
            judged=judged,
            sort=sort,
            tags_any=tags_any,
            event_id=event_id,  # filtered in SQL, so the page is always full
        )
        page = rows[start:]
        return {
            "count": len(page),
            "limit": capped,
            "offset": start,
            "photos": [_photo_summary(r) for r in page],
        }

    @server.tool(
        description=(
            "One event from list_events with its member photo paths: name, date range, "
            "place, trip_id and members. Same as 'pyimgtag events show <id> --format json' "
            f"for events up to {MAX_LIMIT} photos; larger ones are paged with limit/offset "
            "(member_count is the full size)."
        )
    )
    def get_event(event_id: int, limit: int = MAX_LIMIT, offset: int = 0) -> dict:
        if not _valid_event_id(event_id):
            return _no_event(event_id)
        event = db.get_event(int(event_id))
        if event is None:
            return _no_event(event_id)
        members = event["members"]
        capped = _clamp(limit, 1, MAX_LIMIT)
        start = max(0, int(offset))
        page = members[start : start + capped]
        if len(page) == len(members):
            return event  # small events: exactly 'events show --format json'
        return {
            **event,
            "members": page,
            "member_count": len(members),
            "offset": start,
            "truncated": start + len(page) < len(members),
        }

    @server.tool(description="Full metadata for one photo, addressed by its database path.")
    def get_photo(path: str) -> dict:
        row = db.get_image(path)
        if row is None:
            return _not_found(path)
        return _photo_detail(row)

    @server.tool(
        description=(
            "Small JPEG thumbnail of one photo as MCP image content, so the assistant "
            "can look at the result. Only photos already in the database can be read."
        ),
        # Returns MCP image content on success and a JSON error dict otherwise;
        # the SDK resolves annotations eagerly, so the union cannot name the
        # SDK's ``Image`` type (it is imported lazily) — hence ``Any`` plus an
        # explicit opt-out of structured output.
        structured_output=False,
    )
    def get_thumbnail(path: str, size: int = DEFAULT_THUMB_SIZE) -> Any:
        # Path safety: the client value is only a lookup key — the bytes are read
        # from the path the database stored, never from the client string.
        known = db.get_known_file_path(path)
        if known is None:
            return _not_found(path)
        from pyimgtag.webapp.routes_review import _make_thumbnail

        data = _make_thumbnail(known, _clamp(size, 16, MAX_THUMB_SIZE))
        if data is None:
            return {
                "error": "thumbnail unavailable",
                "path": path,
                "hint": "The file could not be decoded (missing, or an unsupported format).",
            }
        return image_cls(data=data, format="jpeg")

    @server.tool(description="Tag vocabulary in the library with per-tag photo counts.")
    def list_tags(limit: int | None = None) -> dict:
        counts = db.get_tag_counts()
        total = len(counts)
        if limit is not None:
            counts = counts[: _clamp(limit, 1, MAX_LIMIT)]
        return {
            "total": total,
            "tags": [{"tag": name, "count": count} for name, count in counts],
        }

    @server.tool(description="Named people in the library with their face counts.")
    def list_people() -> dict:
        named = sorted(
            (p for p in db.get_persons() if p.label),
            key=lambda p: (-len(p.face_ids), p.label),
        )
        people = [
            {
                "person_id": p.person_id,
                "label": p.label,
                "face_count": len(p.face_ids),
                "confirmed": p.confirmed,
                "source": p.source,
            }
            for p in named
        ]
        return {"count": len(people), "people": people}

    @server.tool(
        description=(
            "Events detected by 'pyimgtag events detect', newest first: id, name, "
            "date range (started_at/ended_at), place, trip_id and photo_count. "
            "Same rows as 'pyimgtag events list --format json'. Pass an id to get_event, "
            "or to query_photos(event_id=...) for its photos. Optional limit "
            f"(1-{MAX_LIMIT}); omit it, or pass 0, for every event. 'total' is the "
            "number of events before the limit."
        )
    )
    def list_events(limit: int | None = None) -> dict:
        everything = db.list_events()
        # As in the CLI, a limit of 0 (or less) means no limit.
        events = everything[: _clamp(limit, 1, MAX_LIMIT)] if limit and limit > 0 else everything
        payload: dict = {"total": len(everything), "count": len(events), "events": events}
        if not events:
            payload["hint"] = _NO_EVENTS_HINT
        return payload

    @server.tool(
        description=(
            "Best-of ranking over judged photos, highest weighted score first. "
            "Scores are 1-10; unjudged photos are not included."
        )
    )
    def judge_ranking(
        min_score: int | None = None,
        max_score: int | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        sort: str = "rating_desc",
    ) -> dict:
        result = db.query_judge_results(
            offset=max(0, int(offset)),
            limit=_clamp(limit, 1, MAX_LIMIT),
            sort=sort,
            min_rating=min_score,
            max_rating=max_score,
        )
        items = [
            {
                "path": item["file_path"],
                "file_name": item["file_name"],
                "score": item["weighted_score"],
                "verdict": item["verdict"],
                "reason": item["reason"],
                "scene_summary": item["scene_summary"],
                "city": item["nearest_city"],
                "country": item["nearest_country"],
                "cleanup_class": item["cleanup_class"],
                "date": item["image_date"],
            }
            for item in result["items"]
        ]
        return {"total": result["total"], "count": len(items), "photos": items}

    @server.tool(
        description=(
            "Library-wide aggregates: totals, date span, top places, top tags, "
            "people, judge-score distribution, cleanup candidates."
        )
    )
    def library_stats(top_n: int = 10) -> dict:
        return db.get_insights(top_n=_clamp(top_n, 1, MAX_TOP_N))

    embedder_cache: dict[str, Any] = {}
    embedder_lock = threading.Lock()

    def _embedder() -> Any:
        """Load the CLIP text embedder once, from local files only.

        The ONNX text session and tokenizer load lazily on the first
        ``embed_text``; that warm-up happens here, under the lock, so two
        first calls cannot each build a session. A failure is not cached:
        models downloaded later work without restarting the server.
        """
        with embedder_lock:
            if "embedder" not in embedder_cache:
                embedder = _load_local_embedder(model_dir)
                embedder.embed_text("photo")
                embedder_cache["embedder"] = embedder
            return embedder_cache["embedder"]

    @server.tool(
        description=(
            "Semantic (natural-language) photo search over the local CLIP index built "
            "by 'pyimgtag index': best matches first, as {file_path, score}, the same "
            "ranking as 'pyimgtag search <query> --format json'. Returns an error with "
            "the fix when no index has been built or the [search] extra is missing."
        )
    )
    def search_photos(query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> dict:
        query = query.strip()
        if not query:
            return {"error": "empty query", "hint": "Describe the photos you are looking for."}
        stats = db.embedding_stats()
        if not stats["count"]:
            return {"error": "no semantic index", "query": query, "hint": _NO_INDEX_HINT}
        try:
            embedder = _embedder()
        except ImportError:
            return {
                "error": "semantic search is not installed",
                "query": query,
                "hint": "Install it with: pip install 'pyimgtag[search]'",
            }
        except FileNotFoundError:
            return {"error": "search models missing", "query": query, "hint": _NO_MODELS_HINT}
        except Exception as exc:  # noqa: BLE001 — a broken model must not kill the tool
            return _search_failed(query, exc)
        try:
            hits = db.search_similar(embedder.embed_text(query), limit=_clamp(limit, 1, MAX_LIMIT))
        except Exception as exc:  # noqa: BLE001 — e.g. an index built with another model
            return _search_failed(query, exc)
        payload: dict = {
            "query": query,
            "count": len(hits),
            "results": [{"file_path": p, "score": round(s, 6)} for p, s in hits],
        }
        if stats["model"] and stats["model"] != embedder.model_id:
            payload["warning"] = (
                f"The index was built with {stats['model']!r} but this install uses "
                f"{embedder.model_id!r}; re-run 'pyimgtag index --rebuild'."
            )
        return payload


def _search_failed(query: str, exc: Exception) -> dict:
    """An actionable payload for a model or index that fails at search time."""
    return {
        "error": "semantic search failed",
        "query": query,
        "hint": (
            f"{type(exc).__name__}: {exc}. A model file may be corrupt (delete the model "
            "folder and run 'pyimgtag search \"anything\"' to fetch it again), or the "
            "index was built with another model ('pyimgtag index --rebuild')."
        ),
    }


def _load_local_embedder(model_dir: Path | None = None) -> Any:
    """Build the CLIP embedder from already-downloaded model files.

    The stdio server must not start a 153 MB download (nor print its progress
    on stdout, which carries the protocol), so missing files raise
    ``FileNotFoundError`` instead of being fetched.

    Raises:
        ImportError: The ``[search]`` extra is not installed.
        FileNotFoundError: A model file is not in the model cache.
    """
    from pyimgtag.search.embedder import _require, load_embedder
    from pyimgtag.search.model_cache import TEXT_MODEL, TOKENIZER, VISION_MODEL, cache_dir

    # The missing extra first: its fix comes before fetching models.
    _require("onnxruntime")
    _require("tokenizers")
    # load_embedder() wants all three files and would download a missing one.
    directory = model_dir or cache_dir()
    missing = [
        m.name for m in (VISION_MODEL, TEXT_MODEL, TOKENIZER) if not (directory / m.name).exists()
    ]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    return load_embedder(directory, progress=False)


def _register_write_tools(server: MCPServer, db: ProgressDB, export_root: Path | None) -> None:
    """Register the gated write tools on *server*.

    Only called when writes are explicitly enabled. No tool here deletes
    anything: ``export_photos`` copies, the others update database columns.
    """

    @server.tool(description="Replace the tag list of one photo (normalised: lowercase, unique).")
    def set_tags(path: str, tags: list[str]) -> dict:
        from pyimgtag.models import normalize_tags

        if db.get_image(path) is None:
            return _not_found(path)
        cleaned = normalize_tags(tags, max_tags=max(1, len(tags)))
        db.update_image_tags(path, cleaned)
        return {"ok": True, "path": path, "tags": cleaned}

    @server.tool(
        description=(
            "Set or clear the cleanup class of one photo. "
            "Allowed: keep, review, delete, or null to clear."
        )
    )
    def set_cleanup_class(path: str, cleanup_class: str | None = None) -> dict:
        value: str | None = cleanup_class
        if isinstance(value, str):
            value = value.strip().lower() or None
            if value == "null":
                value = None
        if value is not None and value not in CLEANUP_CLASSES:
            return {
                "error": "invalid cleanup_class",
                "value": cleanup_class,
                "hint": f"Use one of {', '.join(CLEANUP_CLASSES)} or null to clear.",
            }
        if db.get_image(path) is None:
            return _not_found(path)
        db.update_image_cleanup(path, value)
        return {"ok": True, "path": path, "cleanup_class": value}

    @server.tool(description="Rename a person cluster; a non-empty label also confirms it.")
    def rename_person(person_id: int, label: str) -> dict:
        known = {p.person_id for p in db.get_persons()}
        if person_id not in known:
            return {
                "error": "not found",
                "person_id": person_id,
                "hint": "Unknown person id. Use list_people to see the available clusters.",
            }
        db.update_person_label(person_id, label)
        return {"ok": True, "person_id": person_id, "label": label}

    @server.tool(
        description=(
            "Copy database-known photos into the allow-listed export root. "
            "Never deletes or moves originals; refuses any destination outside the root."
        )
    )
    def export_photos(paths: list[str], dest_subdir: str | None = None) -> dict:
        if export_root is None:
            return {
                "error": "no export root configured",
                "hint": "Start the server with 'pyimgtag mcp --export-root DIR' to allow exports.",
            }
        dest = (export_root / dest_subdir).resolve() if dest_subdir else export_root
        if not dest.is_relative_to(export_root):
            return {
                "error": "destination outside export root",
                "dest_subdir": dest_subdir,
                "export_root": str(export_root),
            }
        exported: list[dict] = []
        errors: list[dict] = []
        dest.mkdir(parents=True, exist_ok=True)
        for path in paths:
            known = db.get_known_file_path(path)
            if known is None:
                errors.append(_not_found(path))
                continue
            try:
                target = _unique_target(dest, Path(known).name)
                shutil.copy2(known, target)
            except OSError as exc:
                errors.append({"error": "copy failed", "path": path, "detail": str(exc)})
                continue
            exported.append({"path": path, "exported_to": str(target)})
        return {
            "ok": not errors,
            "export_root": str(export_root),
            "dest": str(dest),
            "exported": exported,
            "errors": errors,
        }


def _unique_target(dest: Path, name: str) -> Path:
    """Return a non-colliding path for *name* inside *dest*."""
    target = dest / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while (dest / f"{stem}_{counter}{suffix}").exists():
        counter += 1
    return dest / f"{stem}_{counter}{suffix}"


def serve_stdio(
    db_path: str | Path | None = None,
    *,
    enable_writes: bool = False,
    export_root: str | None = None,
    model_dir: str | Path | None = None,
) -> None:
    """Run the MCP server over stdio until the client disconnects.

    Args:
        db_path: Progress database to serve.
        enable_writes: Register the gated write tools.
        export_root: Directory ``export_photos`` may copy into.
        model_dir: Folder with the CLIP model files for ``search_photos``.

    Raises:
        ImportError: If the ``[mcp]`` extra is not installed.
    """
    server = build_server(
        db_path, enable_writes=enable_writes, export_root=export_root, model_dir=model_dir
    )
    server.run(transport="stdio")
