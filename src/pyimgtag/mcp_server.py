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
DEFAULT_THUMB_SIZE = 256
MAX_THUMB_SIZE = 512
MAX_TOP_N = 50

CLEANUP_CLASSES = ("keep", "review", "delete")

_SEARCH_HINT = (
    "Semantic search is not implemented yet (roadmap issue #323). "
    "Use query_photos with tag/tags_any, city, country or scene_category filters instead."
)
_EVENTS_NOTE = (
    "Event grouping is not implemented yet — 'pyimgtag events' is a roadmap item. "
    "Use query_photos with a date-bearing filter, or list_tags, to approximate events."
)


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
) -> MCPServer:
    """Build the MCP server exposing the pyimgtag database.

    Args:
        db_path: Progress database to serve. ``None`` uses the default
            ``~/.cache/pyimgtag/progress.db``.
        enable_writes: Register the gated write tools. Read tools are always
            registered.
        export_root: Directory that ``export_photos`` may copy into. When
            ``None``, ``export_photos`` refuses every request.

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
            "judge scores, thumbnails). Paths returned by these tools are the only "
            "valid input for get_photo and get_thumbnail."
        ),
    )

    _register_read_tools(server, db, image_cls)
    if enable_writes:
        _register_write_tools(server, db, root)
    return server


def _register_read_tools(server: MCPServer, db: ProgressDB, image_cls: type[Image]) -> None:
    """Register the always-available read tools on *server*."""

    @server.tool(
        description=(
            "Search the photo library by metadata filters. Mirrors 'pyimgtag query'. "
            "Returns compact records: path, tags, place, date, judge score."
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
        sort: str = "path_asc",
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict:
        capped = _clamp(limit, 1, MAX_LIMIT)
        start = max(0, int(offset))
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
        )
        page = rows[start:]
        return {
            "count": len(page),
            "limit": capped,
            "offset": start,
            "photos": [_photo_summary(r) for r in page],
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

    @server.tool(description="Event groupings (roadmap feature — currently always empty).")
    def list_events() -> dict:
        return {"events": [], "note": _EVENTS_NOTE}

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

    @server.tool(
        description=(
            "Semantic (natural-language) photo search. Not available yet — returns an "
            "actionable error explaining which metadata filters to use instead."
        )
    )
    def search_photos(query: str, limit: int = DEFAULT_LIMIT) -> dict:
        return {
            "error": "no semantic index",
            "query": query,
            "hint": _SEARCH_HINT,
        }


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
) -> None:
    """Run the MCP server over stdio until the client disconnects.

    Args:
        db_path: Progress database to serve.
        enable_writes: Register the gated write tools.
        export_root: Directory ``export_photos`` may copy into.

    Raises:
        ImportError: If the ``[mcp]`` extra is not installed.
    """
    server = build_server(db_path, enable_writes=enable_writes, export_root=export_root)
    server.run(transport="stdio")
