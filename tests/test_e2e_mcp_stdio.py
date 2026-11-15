"""End to end: the real ``pyimgtag mcp`` stdio server answers read tools.

Starts ``python -m pyimgtag mcp --db <tmp>`` as a child process and talks to
it with the MCP SDK's stdio client, the way Claude Desktop or any other MCP
host would.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
import anyio  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

from pyimgtag.models import ImageResult  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from tests.e2e_support import child_env  # noqa: E402


def _call(db: Path, home: Path, tool: str, arguments: dict | None = None) -> dict:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "pyimgtag", "mcp", "--db", str(db)],
        env=child_env(home),
    )

    async def _run() -> dict:
        with anyio.fail_after(60):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments or {})
                    assert not result.is_error, result.content
                    return json.loads(result.content[0].text)

    return anyio.run(_run)


def test_list_events_over_stdio(tmp_path: Path) -> None:
    db_path = tmp_path / "p.db"
    with ProgressDB(db_path) as db:
        db.reconcile_events(
            [
                {
                    "members": ["/p/a.jpg", "/p/b.jpg"],
                    "started_at": "2025-06-01T10:00:00",
                    "ended_at": "2025-06-02T10:00:00",
                    "place": "Porto",
                    "trip": None,
                    "fallback_name": "Porto, June 2025",
                }
            ]
        )
    data = _call(db_path, tmp_path, "list_events")
    assert data["count"] == 1
    assert data["events"][0]["name"] == "Porto, June 2025"
    assert data["events"][0]["photo_count"] == 2


def test_search_photos_over_stdio_without_an_index(tmp_path: Path) -> None:
    """#431: no claim that search is unimplemented; it says how to build the index."""
    db_path = tmp_path / "p.db"
    ProgressDB(db_path).close()
    data = _call(db_path, tmp_path, "search_photos", {"query": "beach at sunset"})
    assert data["error"] == "no semantic index"
    assert "pyimgtag index --input-dir" in data["hint"]
    assert "not implemented" not in json.dumps(data)


def test_an_event_id_from_list_events_leads_to_its_photos(tmp_path: Path) -> None:
    """#461: list_events -> get_event / query_photos(event_id) over stdio."""
    db_path = tmp_path / "p.db"
    paths = [str(tmp_path / n) for n in ("a.jpg", "b.jpg", "c.jpg")]
    with ProgressDB(db_path) as db:
        for p in paths:
            Path(p).write_bytes(b"x")
            db.mark_done(Path(p), ImageResult(file_path=p, file_name=Path(p).name, tags=["sea"]))
        db.reconcile_events(
            [
                {
                    "members": paths[:2],
                    "started_at": "2025-06-01T10:00:00",
                    "ended_at": "2025-06-02T10:00:00",
                    "place": "Porto",
                    "trip": None,
                    "fallback_name": "Porto, June 2025",
                }
            ]
        )
    (event,) = _call(db_path, tmp_path, "list_events")["events"]
    shown = _call(db_path, tmp_path, "get_event", {"event_id": event["id"]})
    assert sorted(shown["members"]) == paths[:2]
    queried = _call(db_path, tmp_path, "query_photos", {"event_id": event["id"]})
    assert sorted(p["path"] for p in queried["photos"]) == paths[:2]
