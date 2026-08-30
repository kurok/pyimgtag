"""Tests for the stdio MCP server (``pyimgtag mcp``)."""

from __future__ import annotations

import json
from pathlib import Path

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


def test_list_events_is_a_documented_stub(tmp_path):
    server = _server(_seed_db(tmp_path))
    data = _payload(_call(server, "list_events", {}))
    assert data["events"] == []
    assert "roadmap" in data["note"]


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


def test_search_photos_degrades_with_actionable_error(tmp_path):
    server = _server(_seed_db(tmp_path))
    data = _payload(_call(server, "search_photos", {"query": "alice at the beach"}))
    assert data["error"] == "no semantic index"
    assert data["query"] == "alice at the beach"
    assert "query_photos" in data["hint"]


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

    def _fake_serve(db_path=None, *, enable_writes=False, export_root=None):
        captured.update(db_path=db_path, enable_writes=enable_writes, export_root=export_root)

    monkeypatch.setattr(mcp_server, "serve_stdio", _fake_serve)
    monkeypatch.setenv(mcp_server.WRITES_ENV_VAR, "1")
    args = build_parser().parse_args(["mcp", "--db", str(tmp_path / "p.db")])
    assert mcp_cmd.cmd_mcp(args) == 0
    assert captured["enable_writes"] is True
    assert captured["db_path"] == str(tmp_path / "p.db")


def test_serve_stdio_uses_the_stdio_transport(tmp_path, monkeypatch):
    calls: list[str] = []

    class _FakeServer:
        def run(self, transport: str = "stdio") -> None:
            calls.append(transport)

    monkeypatch.setattr(mcp_server, "build_server", lambda *a, **k: _FakeServer())
    mcp_server.serve_stdio(tmp_path / "p.db")
    assert calls == ["stdio"]


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
