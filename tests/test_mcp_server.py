"""Tests for the stdio MCP server (``pyimgtag mcp``)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("mcp")

import anyio  # noqa: E402
from mcp import Client  # noqa: E402

from pyimgtag import mcp_server  # noqa: E402
from pyimgtag.commands.query import cmd_query  # noqa: E402
from pyimgtag.main import build_parser  # noqa: E402
from pyimgtag.models import (  # noqa: E402
    FaceDetection,
    ImageResult,
    JudgeResult,
    JudgeScores,
)
from pyimgtag.progress_db import ProgressDB  # noqa: E402

READ_TOOLS = {
    "query_photos",
    "get_photo",
    "get_thumbnail",
    "list_tags",
    "list_people",
    "list_events",
    "get_event",
    "judge_ranking",
    "library_stats",
    "search_photos",
}
WRITE_TOOLS = {"set_tags", "set_cleanup_class", "rename_person", "export_photos"}


# --- helpers ---------------------------------------------------------------


def _write_jpeg(path: Path, size: tuple[int, int] = (64, 48)) -> Path:
    from PIL import Image as PILImage

    PILImage.new("RGB", size, "red").save(path, format="JPEG")
    return path


def _seed_db(tmp_path: Path) -> Path:
    """Create a small fixture library and return the database path."""
    db_path = tmp_path / "progress.db"
    real_jpeg = _write_jpeg(tmp_path / "beach.jpg")
    rows = [
        ImageResult(
            file_path=str(real_jpeg),
            file_name=real_jpeg.name,
            source_type="directory",
            tags=["sea", "sunset"],
            scene_summary="Sunset over the bay",
            scene_category="landscape",
            cleanup_class="keep",
            nearest_city="Lisbon",
            nearest_country="Portugal",
            image_date="2026-05-01",
            has_text=False,
        ),
        ImageResult(
            file_path=str(tmp_path / "city.jpg"),
            file_name="city.jpg",
            source_type="directory",
            tags=["street", "sign"],
            scene_summary="Street sign",
            scene_category="urban",
            cleanup_class="review",
            nearest_city="Porto",
            nearest_country="Portugal",
            image_date="2026-06-02",
            has_text=True,
            text_summary="STOP",
        ),
        ImageResult(
            file_path=str(tmp_path / "blur.jpg"),
            file_name="blur.jpg",
            source_type="directory",
            tags=["sea"],
            scene_summary="Blurry water",
            scene_category="landscape",
            cleanup_class="delete",
            nearest_city="Faro",
            nearest_country="Portugal",
            image_date="2026-07-03",
        ),
    ]
    db = ProgressDB(db_path=db_path)
    for result in rows:
        db.mark_done(Path(result.file_path), result)
    for index, result in enumerate(rows[:2]):
        db.save_judge_result(
            JudgeResult(
                file_path=result.file_path,
                file_name=result.file_name,
                weighted_score=6 + index,
                core_score=6 + index,
                visible_score=6 + index,
                scores=JudgeScores(score=6 + index, verdict="keep", reason="nice light"),
            )
        )
    face_id = db.insert_face(rows[0].file_path, FaceDetection(image_path=rows[0].file_path))
    person_id = db.create_person(label="Alice", confirmed=True)
    db.set_person_id(face_id, person_id)
    db.create_person(label="")  # unnamed cluster — must not show up in list_people
    db.close()
    return db_path


def _server(db_path: Path, **kwargs):
    return mcp_server.build_server(db_path, **kwargs)


def _tool_names(server) -> set[str]:
    async def _run() -> set[str]:
        async with Client(server) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    return anyio.run(_run)


def _call(server, name: str, arguments: dict | None = None):
    async def _run():
        async with Client(server) as client:
            return await client.call_tool(name, arguments or {})

    return anyio.run(_run)


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


# --- protocol round-trip ---------------------------------------------------


def test_initialize_and_list_tools_read_only(tmp_path):
    server = _server(_seed_db(tmp_path))
    names = _tool_names(server)
    assert READ_TOOLS <= names
    assert not (WRITE_TOOLS & names)


def test_server_metadata(tmp_path):
    server = _server(_seed_db(tmp_path))

    async def _run():
        async with Client(server) as client:
            return client.server_info

    assert anyio.run(_run).name == "pyimgtag"


def test_write_tools_listed_when_enabled(tmp_path):
    server = _server(_seed_db(tmp_path), enable_writes=True)
    names = _tool_names(server)
    assert WRITE_TOOLS <= names
    assert READ_TOOLS <= names


# --- read tools ------------------------------------------------------------


def test_query_photos_returns_compact_records(tmp_path):
    server = _server(_seed_db(tmp_path))
    data = _payload(_call(server, "query_photos", {"tag": "sea"}))
    assert data["count"] == 2
    assert data["limit"] == 50
    assert data["offset"] == 0
    first = data["photos"][0]
    assert set(first) == {
        "path",
        "file_name",
        "tags",
        "scene_summary",
        "scene_category",
        "cleanup_class",
        "city",
        "country",
        "date",
        "judge_score",
    }
    assert all("sea" in photo["tags"] for photo in data["photos"])


def test_query_photos_filters_and_pagination(tmp_path):
    server = _server(_seed_db(tmp_path))
    assert _payload(_call(server, "query_photos", {"city": "porto"}))["count"] == 1
    assert _payload(_call(server, "query_photos", {"has_text": True}))["count"] == 1
    assert _payload(_call(server, "query_photos", {"cleanup_class": "delete"}))["count"] == 1
    assert _payload(_call(server, "query_photos", {"judged": True}))["count"] == 2
    assert _payload(_call(server, "query_photos", {"min_score": 7}))["count"] == 1
    assert _payload(_call(server, "query_photos", {"tags_any": ["street"]}))["count"] == 1
    assert _payload(_call(server, "query_photos", {"tags_any": []}))["count"] == 0

    page = _payload(_call(server, "query_photos", {"limit": 1, "offset": 1}))
    assert page["count"] == 1
    assert page["offset"] == 1
    everything = _payload(_call(server, "query_photos", {}))
    assert page["photos"][0]["path"] == everything["photos"][1]["path"]


def test_query_photos_limit_is_capped(tmp_path):
    server = _server(_seed_db(tmp_path))
    assert _payload(_call(server, "query_photos", {"limit": 10_000}))["limit"] == 500
    assert _payload(_call(server, "query_photos", {"limit": 0}))["limit"] == 1


def test_query_photos_matches_cli_query(tmp_path, capsys):
    """Parity: the tool and ``pyimgtag query`` must select the same photos."""
    db_path = _seed_db(tmp_path)
    server = _server(db_path)
    parser = build_parser()
    filters = [
        ["--tag", "sea"],
        ["--city", "porto"],
        ["--country", "portugal"],
        ["--scene-category", "landscape"],
        ["--cleanup", "delete"],
        ["--status", "ok"],
        ["--has-text"],
        ["--no-text"],
        [],
    ]
    tool_kwargs: list[dict] = [
        {"tag": "sea"},
        {"city": "porto"},
        {"country": "portugal"},
        {"scene_category": "landscape"},
        {"cleanup_class": "delete"},
        {"status": "ok"},
        {"has_text": True},
        {"has_text": False},
        {},
    ]
    for cli_flags, kwargs in zip(filters, tool_kwargs, strict=True):
        args = parser.parse_args(
            ["query", "--db", str(db_path), "--format", "paths", *cli_flags],
        )
        assert cmd_query(args) == 0
        cli_paths = [line for line in capsys.readouterr().out.splitlines() if line]
        tool_paths = [p["path"] for p in _payload(_call(server, "query_photos", kwargs))["photos"]]
        assert tool_paths == cli_paths, f"parity mismatch for {cli_flags}"


def test_get_photo_full_row(tmp_path):
    server = _server(_seed_db(tmp_path))
    known = _payload(_call(server, "query_photos", {"tag": "street"}))["photos"][0]["path"]
    data = _payload(_call(server, "get_photo", {"path": known}))
    assert data["path"] == known
    assert data["tags"] == ["street", "sign"]
    assert data["scene_category"] == "urban"
    assert data["status"] == "ok"
    assert "processed_at" in data


def test_get_photo_unknown_path(tmp_path):
    server = _server(_seed_db(tmp_path))
    data = _payload(_call(server, "get_photo", {"path": "/nope/missing.jpg"}))
    assert data["error"] == "not found"
    assert data["path"] == "/nope/missing.jpg"
    assert "hint" in data


def test_get_thumbnail_returns_image_content(tmp_path, monkeypatch):
    monkeypatch.setattr("pyimgtag.webapp.routes_review._THUMB_DIR", tmp_path / "thumbs")
    db_path = _seed_db(tmp_path)
    server = _server(db_path)
    result = _call(server, "get_thumbnail", {"path": str(tmp_path / "beach.jpg")})
    block = result.content[0]
    assert block.type == "image"
    assert block.mime_type == "image/jpeg"
    assert len(block.data) > 0


def test_get_thumbnail_unknown_path_never_opens_file(tmp_path, monkeypatch):
    monkeypatch.setattr("pyimgtag.webapp.routes_review._THUMB_DIR", tmp_path / "thumbs")
    outsider = _write_jpeg(tmp_path / "outsider.jpg")
    server = _server(_seed_db(tmp_path))
    data = _payload(_call(server, "get_thumbnail", {"path": str(outsider)}))
    assert data["error"] == "not found"


def test_get_thumbnail_reports_undecodable_file(tmp_path, monkeypatch):
    monkeypatch.setattr("pyimgtag.webapp.routes_review._THUMB_DIR", tmp_path / "thumbs")
    server = _server(_seed_db(tmp_path))
    # city.jpg is a DB row with no file behind it — path is known, decode fails.
    data = _payload(_call(server, "get_thumbnail", {"path": str(tmp_path / "city.jpg")}))
    assert data["error"] == "thumbnail unavailable"


def test_list_tags(tmp_path):
    server = _server(_seed_db(tmp_path))
    data = _payload(_call(server, "list_tags", {}))
    assert data["total"] == 4
    assert data["tags"][0] == {"tag": "sea", "count": 2}
    assert _payload(_call(server, "list_tags", {"limit": 2}))["tags"] == data["tags"][:2]


def test_list_people_only_named(tmp_path):
    server = _server(_seed_db(tmp_path))
    data = _payload(_call(server, "list_people", {}))
    assert data["count"] == 1
    person = data["people"][0]
    assert person["label"] == "Alice"
    assert person["face_count"] == 1
    assert person["confirmed"] is True


def _seed_events(db_path: Path) -> None:
    db = ProgressDB(db_path=db_path)
    db.reconcile_events(
        [
            {
                "members": ["/p/a.jpg", "/p/b.jpg"],
                "started_at": "2025-06-01T10:00:00",
                "ended_at": "2025-06-03T18:00:00",
                "place": "Óbidos",
                "trip": None,
                "fallback_name": "Óbidos, June 2025",
            },
            {
                "members": ["/p/c.jpg"],
                "started_at": "2025-08-10T09:00:00",
                "ended_at": "2025-08-10T20:00:00",
                "place": "Lisbon",
                "trip": None,
                "fallback_name": "Lisbon, August 2025",
            },
        ]
    )
    db.close()


def test_list_events_returns_the_stored_events(tmp_path):
    """#430: the tool reads the events tables, like 'events list --format json'."""
    db_path = _seed_db(tmp_path)
    _seed_events(db_path)
    server = _server(db_path)
    data = _payload(_call(server, "list_events", {}))
    assert (data["total"], data["count"]) == (2, 2)
    assert [e["name"] for e in data["events"]] == ["Lisbon, August 2025", "Óbidos, June 2025"]
    assert [e["photo_count"] for e in data["events"]] == [1, 2]
    assert {"id", "started_at", "ended_at", "place", "trip_id"} <= set(data["events"][0])
    assert "not implemented" not in json.dumps(data)
    assert "hint" not in data

    db = ProgressDB(db_path=db_path)
    assert data["events"] == db.list_events()
    db.close()

    limited = _payload(_call(server, "list_events", {"limit": 1}))
    assert [e["name"] for e in limited["events"]] == ["Lisbon, August 2025"]
    assert (limited["total"], limited["count"]) == (2, 1)
    # As in 'events list --limit 0': zero means every event.
    assert _payload(_call(server, "list_events", {"limit": 0}))["count"] == 2


def test_list_events_on_a_library_without_events_says_how_to_make_them(tmp_path):
    server = _server(_seed_db(tmp_path))
    data = _payload(_call(server, "list_events", {}))
    assert data == {
        "total": 0,
        "count": 0,
        "events": [],
        "hint": "No events yet: run 'pyimgtag events detect' to group photos into events.",
    }


def test_list_events_description_no_longer_says_roadmap(tmp_path):
    async def _run():
        async with Client(_server(_seed_db(tmp_path))) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            return tools["list_events"].description

    description = anyio.run(_run)
    assert "roadmap" not in description.lower()
    assert "events detect" in description


def test_judge_ranking(tmp_path):
    server = _server(_seed_db(tmp_path))
    data = _payload(_call(server, "judge_ranking", {}))
    assert data["total"] == 2
    assert [p["score"] for p in data["photos"]] == [7, 6]
    assert data["photos"][0]["verdict"] == "keep"
    filtered = _payload(_call(server, "judge_ranking", {"min_score": 7}))
    assert filtered["total"] == 1


def test_library_stats(tmp_path):
    server = _server(_seed_db(tmp_path))
    data = _payload(_call(server, "library_stats", {"top_n": 3}))
    assert data["overview"]["total"] == 3
    assert data["overview"]["ok"] == 3
    assert data["places"]["countries"][0]["value"] == "Portugal"


def test_search_photos_without_an_index_says_how_to_build_one(tmp_path):
    server = _server(_seed_db(tmp_path))
    data = _payload(_call(server, "search_photos", {"query": "alice at the beach"}))
    assert data["error"] == "no semantic index"
    assert data["query"] == "alice at the beach"
    assert "pyimgtag index --input-dir" in data["hint"]
    assert "not implemented" not in json.dumps(data)


class _FakeEmbedder:
    model_id = "fake-clip"

    def embed_text(self, text: str):
        import numpy as np

        return np.array([1.0, 0.0, 0.0], dtype=np.float32)


def _seed_index(db_path: Path) -> None:
    import numpy as np

    db = ProgressDB(db_path=db_path)
    for name, vec in (
        ("beach.jpg", [0.9, 0.1, 0.0]),
        ("city.jpg", [0.1, 0.9, 0.0]),
        ("dog.jpg", [0.6, 0.0, 0.8]),
    ):
        v = np.array(vec, dtype=np.float32)
        db.upsert_embedding(Path("/lib") / name, "fake-clip", v / np.linalg.norm(v))
    db.close()


def test_search_photos_ranks_the_index_like_the_cli(tmp_path):
    """#431: the tool searches the CLIP index, best match first."""
    db_path = _seed_db(tmp_path)
    _seed_index(db_path)
    with patch.object(mcp_server, "_load_local_embedder", return_value=_FakeEmbedder()):
        server = _server(db_path)
        data = _payload(_call(server, "search_photos", {"query": "sunny beach", "limit": 2}))
    assert data["query"] == "sunny beach"
    lib = Path("/lib")
    assert [r["file_path"] for r in data["results"]] == [
        str(lib / "beach.jpg"),
        str(lib / "dog.jpg"),
    ]
    assert data["count"] == 2
    assert data["results"][0]["score"] > data["results"][1]["score"]
    assert "warning" not in data

    # Same ranking the CLI's --format json path computes.
    db = ProgressDB(db_path=db_path)
    expected = db.search_similar(_FakeEmbedder().embed_text("x"), limit=2)
    db.close()
    assert data["results"] == [{"file_path": p, "score": round(s, 6)} for p, s in expected]


def test_search_photos_warns_about_a_model_mismatch(tmp_path):
    db_path = _seed_db(tmp_path)
    _seed_index(db_path)
    other = _FakeEmbedder()
    other.model_id = "other-clip"
    with patch.object(mcp_server, "_load_local_embedder", return_value=other):
        data = _payload(_call(_server(db_path), "search_photos", {"query": "beach"}))
    assert "index --rebuild" in data["warning"]


@pytest.mark.parametrize(
    ("raised", "error", "hint_part"),
    [
        (ImportError("onnxruntime"), "semantic search is not installed", "pyimgtag[search]"),
        (FileNotFoundError("clip.onnx"), "search models missing", "pyimgtag search"),
    ],
)
def test_search_photos_degrades_without_the_extra_or_the_models(tmp_path, raised, error, hint_part):
    db_path = _seed_db(tmp_path)
    _seed_index(db_path)
    with patch.object(mcp_server, "_load_local_embedder", side_effect=raised):
        data = _payload(_call(_server(db_path), "search_photos", {"query": "beach"}))
    assert data["error"] == error
    assert hint_part in data["hint"]


def test_the_local_embedder_never_downloads(tmp_path, monkeypatch):
    monkeypatch.setenv("PYIMGTAG_SEARCH_MODEL_DIR", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    with (
        patch("pyimgtag.search.embedder._require"),  # the [search] extra may be absent in CI
        patch("pyimgtag.search.embedder.load_embedder") as load,
    ):
        with pytest.raises(FileNotFoundError):
            mcp_server._load_local_embedder()
    load.assert_not_called()


def test_the_local_embedder_uses_the_given_model_dir(tmp_path):
    from pyimgtag.search.model_cache import TEXT_MODEL, TOKENIZER, VISION_MODEL

    for model in (VISION_MODEL, TEXT_MODEL, TOKENIZER):
        (tmp_path / model.name).write_bytes(b"x")
    with (
        patch("pyimgtag.search.embedder._require"),
        patch("pyimgtag.search.embedder.load_embedder") as load,
    ):
        mcp_server._load_local_embedder(tmp_path)
    load.assert_called_once_with(tmp_path, progress=False)


def test_a_missing_extra_is_reported_before_missing_models(tmp_path, monkeypatch):
    monkeypatch.setenv("PYIMGTAG_SEARCH_MODEL_DIR", str(tmp_path))
    with patch("pyimgtag.search.embedder._require", side_effect=ImportError("no onnxruntime")):
        with pytest.raises(ImportError):
            mcp_server._load_local_embedder()


def test_a_model_that_fails_at_search_time_is_an_actionable_error(tmp_path):
    db_path = _seed_db(tmp_path)
    _seed_index(db_path)
    broken = _FakeEmbedder()
    broken.embed_text = MagicMock(side_effect=RuntimeError("[ONNXRuntimeError] INVALID_PROTOBUF"))
    with patch.object(mcp_server, "_load_local_embedder", return_value=broken):
        data = _payload(_call(_server(db_path), "search_photos", {"query": "beach"}))
    assert data["error"] == "semantic search failed"
    assert "INVALID_PROTOBUF" in data["hint"]
    assert "--rebuild" in data["hint"]


def test_search_photos_equals_the_cli_json(tmp_path, capsys):
    """The tool's rows are the CLI's 'search --format json' rows, same default top."""
    from pyimgtag.main import main

    db_path = _seed_db(tmp_path)
    _seed_index(db_path)
    with patch("pyimgtag.search.embedder.load_embedder", return_value=_FakeEmbedder()):
        assert main(["search", "sunny beach", "--format", "json", "--db", str(db_path)]) == 0
    cli_rows = json.loads(capsys.readouterr().out)
    with patch.object(mcp_server, "_load_local_embedder", return_value=_FakeEmbedder()):
        data = _payload(_call(_server(db_path), "search_photos", {"query": "sunny beach"}))
    assert data["results"] == cli_rows


def test_search_photos_description_names_the_index(tmp_path):
    async def _run():
        async with Client(_server(_seed_db(tmp_path))) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            return tools["search_photos"].description

    description = anyio.run(_run)
    assert "Not available yet" not in description
    assert "pyimgtag index" in description


# --- write tools -----------------------------------------------------------


def test_set_tags_updates_the_database(tmp_path):
    db_path = _seed_db(tmp_path)
    server = _server(db_path, enable_writes=True)
    target = str(tmp_path / "city.jpg")
    data = _payload(_call(server, "set_tags", {"path": target, "tags": ["Neon", "neon", "night"]}))
    assert data == {"ok": True, "path": target, "tags": ["neon", "night"]}
    with ProgressDB(db_path=db_path) as db:
        assert db.get_image(target)["tags"] == ["neon", "night"]


def test_set_tags_unknown_path(tmp_path):
    server = _server(_seed_db(tmp_path), enable_writes=True)
    data = _payload(_call(server, "set_tags", {"path": "/nope.jpg", "tags": ["x"]}))
    assert data["error"] == "not found"


def test_set_cleanup_class(tmp_path):
    db_path = _seed_db(tmp_path)
    server = _server(db_path, enable_writes=True)
    target = str(tmp_path / "city.jpg")
    assert (
        _payload(_call(server, "set_cleanup_class", {"path": target, "cleanup_class": "keep"}))[
            "cleanup_class"
        ]
        == "keep"
    )
    cleared = _payload(
        _call(server, "set_cleanup_class", {"path": target, "cleanup_class": "null"})
    )
    assert cleared["cleanup_class"] is None
    with ProgressDB(db_path=db_path) as db:
        assert db.get_image(target)["cleanup_class"] is None


def test_set_cleanup_class_rejects_unknown_value(tmp_path):
    server = _server(_seed_db(tmp_path), enable_writes=True)
    data = _payload(
        _call(
            server,
            "set_cleanup_class",
            {"path": str(tmp_path / "city.jpg"), "cleanup_class": "burn"},
        )
    )
    assert data["error"] == "invalid cleanup_class"


def test_rename_person(tmp_path):
    db_path = _seed_db(tmp_path)
    server = _server(db_path, enable_writes=True)
    person_id = _payload(_call(server, "list_people", {}))["people"][0]["person_id"]
    data = _payload(_call(server, "rename_person", {"person_id": person_id, "label": "Alicia"}))
    assert data["ok"] is True
    with ProgressDB(db_path=db_path) as db:
        assert {p.label for p in db.get_persons()} == {"Alicia", ""}


def test_rename_person_unknown_id(tmp_path):
    server = _server(_seed_db(tmp_path), enable_writes=True)
    data = _payload(_call(server, "rename_person", {"person_id": 999, "label": "Bob"}))
    assert data["error"] == "not found"


def test_export_photos_copies_inside_root(tmp_path):
    db_path = _seed_db(tmp_path)
    root = tmp_path / "export"
    server = _server(db_path, enable_writes=True, export_root=str(root))
    source = str(tmp_path / "beach.jpg")
    data = _payload(
        _call(server, "export_photos", {"paths": [source], "dest_subdir": "trip/italy"})
    )
    assert data["ok"] is True
    copied = Path(data["exported"][0]["exported_to"])
    assert copied.exists()
    assert copied.is_relative_to(root)
    assert copied.read_bytes() == Path(source).read_bytes()
    # A second export of the same name must not clobber the first.
    again = _payload(
        _call(server, "export_photos", {"paths": [source], "dest_subdir": "trip/italy"})
    )
    assert Path(again["exported"][0]["exported_to"]) != copied


def test_export_photos_refuses_escaping_subdir(tmp_path):
    root = tmp_path / "export"
    server = _server(_seed_db(tmp_path), enable_writes=True, export_root=str(root))
    for escape in ("../outside", "../../etc", str(tmp_path / "elsewhere")):
        data = _payload(
            _call(
                server,
                "export_photos",
                {"paths": [str(tmp_path / "beach.jpg")], "dest_subdir": escape},
            )
        )
        assert data["error"] == "destination outside export root", escape
    assert not (tmp_path / "outside").exists()


def test_export_photos_skips_unknown_paths(tmp_path):
    root = tmp_path / "export"
    server = _server(_seed_db(tmp_path), enable_writes=True, export_root=str(root))
    outsider = _write_jpeg(tmp_path / "outsider.jpg")
    data = _payload(_call(server, "export_photos", {"paths": [str(outsider)]}))
    assert data["ok"] is False
    assert data["exported"] == []
    assert data["errors"][0]["error"] == "not found"


def test_export_photos_refused_without_export_root(tmp_path):
    server = _server(_seed_db(tmp_path), enable_writes=True)
    data = _payload(_call(server, "export_photos", {"paths": [str(tmp_path / "beach.jpg")]}))
    assert data["error"] == "no export root configured"
    assert "--export-root" in data["hint"]


# --- environment gating and the missing extra ------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("ON", True), ("0", False), ("", False), ("no", False)],
)
def test_writes_enabled_from_env(value, expected):
    assert mcp_server.writes_enabled_from_env({mcp_server.WRITES_ENV_VAR: value}) is expected


def test_writes_enabled_from_env_reads_process_env(monkeypatch):
    monkeypatch.setenv(mcp_server.WRITES_ENV_VAR, "1")
    assert mcp_server.writes_enabled_from_env() is True
    monkeypatch.delenv(mcp_server.WRITES_ENV_VAR)
    assert mcp_server.writes_enabled_from_env() is False


def test_missing_extra_raises_friendly_import_error(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "mcp", None)
    monkeypatch.setitem(__import__("sys").modules, "mcp.server", None)
    monkeypatch.setitem(__import__("sys").modules, "mcp.server.mcpserver", None)
    with pytest.raises(ImportError, match=r"pip install 'pyimgtag\[mcp\]'"):
        mcp_server.build_server(tmp_path / "progress.db")


def test_cmd_mcp_reports_missing_extra(tmp_path, monkeypatch, capsys):
    from pyimgtag.commands.mcp_cmd import cmd_mcp

    monkeypatch.setattr(
        mcp_server,
        "build_server",
        lambda *a, **k: (_ for _ in ()).throw(ImportError(mcp_server.MCP_INSTALL_HINT)),
    )
    args = build_parser().parse_args(["mcp", "--db", str(tmp_path / "p.db")])
    assert cmd_mcp(args) == 1
    assert "pyimgtag[mcp]" in capsys.readouterr().err


def test_cmd_mcp_runs_stdio_server(tmp_path, monkeypatch):
    from pyimgtag.commands import mcp_cmd

    captured: dict = {}

    def _fake_serve(db_path=None, *, enable_writes=False, export_root=None, model_dir=None):
        captured.update(
            db_path=db_path,
            enable_writes=enable_writes,
            export_root=export_root,
            model_dir=model_dir,
        )

    monkeypatch.setattr(mcp_server, "serve_stdio", _fake_serve)
    monkeypatch.setenv(mcp_server.WRITES_ENV_VAR, "1")
    argv = ["mcp", "--db", str(tmp_path / "p.db"), "--model-dir", str(tmp_path / "models")]
    assert mcp_cmd.cmd_mcp(build_parser().parse_args(argv)) == 0
    assert captured["enable_writes"] is True
    assert captured["db_path"] == str(tmp_path / "p.db")
    assert captured["model_dir"] == str(tmp_path / "models")


def test_serve_stdio_runs_the_answer_before_eof_loop(tmp_path, monkeypatch):
    """serve_stdio serves the built server through the #468 stdio loop."""
    built = object()
    served: list[object] = []

    async def _fake_loop(server):
        served.append(server)

    monkeypatch.setattr(mcp_server, "build_server", lambda *a, **k: built)
    monkeypatch.setattr(mcp_server, "_serve_stdio_until_answered", _fake_loop)
    mcp_server.serve_stdio(tmp_path / "p.db")
    assert served == [built]


# --- CLI parser ------------------------------------------------------------


def test_parser_mcp_defaults():
    args = build_parser().parse_args(["mcp"])
    assert args.subcommand == "mcp"
    assert args.db is None
    assert args.enable_writes is False
    assert args.export_root is None


def test_parser_mcp_flags(tmp_path):
    args = build_parser().parse_args(
        ["mcp", "--db", str(tmp_path / "p.db"), "--enable-writes", "--export-root", str(tmp_path)]
    )
    assert args.enable_writes is True
    assert args.export_root == str(tmp_path)


def test_mcp_help_documents_client_config(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["mcp", "--help"])
    out = capsys.readouterr().out
    assert "mcpServers" in out
    assert "PYIMGTAG_MCP_ENABLE_WRITES" in out


# --- #461: event ids lead somewhere ---------------------------------------


def _seed_two_events(tmp_path: Path) -> tuple[Path, int, int, list[str]]:
    db_path = _seed_db(tmp_path)
    db = ProgressDB(db_path=db_path)
    paths = sorted(r["file_path"] for r in db.query_images())
    db.reconcile_events(
        [
            {
                "members": paths[:2],
                "started_at": "2025-06-01T10:00:00",
                "ended_at": "2025-06-02T10:00:00",
                "place": "Porto",
                "trip": None,
                "fallback_name": "Porto, June 2025",
            },
            {
                "members": paths[2:],
                "started_at": "2025-08-01T10:00:00",
                "ended_at": "2025-08-01T18:00:00",
                "place": "Lisbon",
                "trip": None,
                "fallback_name": "Lisbon, August 2025",
            },
        ]
    )
    by_name = {e["name"]: e["id"] for e in db.list_events()}
    db.close()
    return db_path, by_name["Porto, June 2025"], by_name["Lisbon, August 2025"], paths


def test_query_photos_filters_by_event(tmp_path):
    db_path, porto, lisbon, paths = _seed_two_events(tmp_path)
    server = _server(db_path)
    data = _payload(_call(server, "query_photos", {"event_id": porto}))
    assert sorted(p["path"] for p in data["photos"]) == paths[:2]
    other = _payload(_call(server, "query_photos", {"event_id": lisbon}))
    assert sorted(p["path"] for p in other["photos"]) == paths[2:]


def test_query_photos_event_filter_pages_after_filtering(tmp_path):
    """The Lisbon photos are the *last* paths: a limit applied before the
    event filter would return nothing on the first page."""
    db_path, _, lisbon, paths = _seed_two_events(tmp_path)
    server = _server(db_path)
    first = _payload(_call(server, "query_photos", {"event_id": lisbon, "limit": 1}))
    second = _payload(_call(server, "query_photos", {"event_id": lisbon, "limit": 1, "offset": 1}))
    assert [p["path"] for p in first["photos"] + second["photos"]] == paths[2:]


def test_query_photos_event_filter_keeps_the_sort(tmp_path):
    db_path, porto, _, paths = _seed_two_events(tmp_path)
    data = _payload(
        _call(_server(db_path), "query_photos", {"event_id": porto, "sort": "path_desc"})
    )
    assert [p["path"] for p in data["photos"]] == sorted(paths[:2], reverse=True)


@pytest.mark.parametrize("bad", [0, -1, 2**70])
def test_out_of_range_event_ids_are_a_clear_error(tmp_path, bad):
    server = _server(_seed_db(tmp_path))
    for tool in ("get_event", "query_photos"):
        data = _payload(_call(server, tool, {"event_id": bad}))
        assert data["error"] == "no such event", tool


def test_get_event_pages_a_large_event(tmp_path):
    db_path, porto, _, paths = _seed_two_events(tmp_path)
    server = _server(db_path)
    page = _payload(_call(server, "get_event", {"event_id": porto, "limit": 1}))
    assert page["member_count"] == 2
    assert len(page["members"]) == 1
    assert page["truncated"] is True
    rest = _payload(_call(server, "get_event", {"event_id": porto, "limit": 1, "offset": 1}))
    assert rest["truncated"] is False
    assert sorted(page["members"] + rest["members"]) == paths[:2]


def test_query_photos_with_an_unknown_event_is_a_clear_error(tmp_path):
    data = _payload(_call(_server(_seed_db(tmp_path)), "query_photos", {"event_id": 999}))
    assert data["error"] == "no such event"
    assert "list_events" in data["hint"]


def test_get_event_matches_events_show_json(tmp_path):
    db_path, porto, _, paths = _seed_two_events(tmp_path)
    data = _payload(_call(_server(db_path), "get_event", {"event_id": porto}))
    db = ProgressDB(db_path=db_path)
    expected = db.get_event(porto)
    db.close()
    assert data == expected
    assert sorted(data["members"]) == paths[:2]
    assert data["name"] == "Porto, June 2025"


def test_get_event_with_an_unknown_id_is_a_clear_error(tmp_path):
    data = _payload(_call(_server(_seed_db(tmp_path)), "get_event", {"event_id": 999}))
    assert data == {
        "error": "no such event",
        "event_id": 999,
        "hint": "Use list_events to see the event ids.",
    }
