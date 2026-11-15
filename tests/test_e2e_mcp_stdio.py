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


def _burst(db_path: Path, home: Path, messages: list[dict]) -> list[dict]:
    """Write every message at once, close stdin, read every answer."""
    import subprocess

    payload = "".join(json.dumps(m) + "\n" for m in messages)
    proc = subprocess.run(  # nosec B603 — fixed interpreter and module
        [sys.executable, "-m", "pyimgtag", "mcp", "--db", str(db_path)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_env(home),
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "burst", "version": "1"},
    },
}
_INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _tool_call(request_id: int, name: str, arguments: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


def test_a_burst_then_eof_gets_every_answer(tmp_path: Path) -> None:
    """#468: requests written in one burst before stdin closes are all answered."""
    db_path = tmp_path / "p.db"
    ProgressDB(db_path).close()
    answers = _burst(
        db_path,
        tmp_path,
        [
            _INIT,
            _INITIALIZED,
            _tool_call(3, "list_tags"),
            _tool_call(4, "list_events"),
            _tool_call(5, "search_photos", {"query": "beach"}),
            {"jsonrpc": "2.0", "id": 6, "method": "tools/list"},
        ],
    )
    assert sorted(a["id"] for a in answers) == [1, 3, 4, 5, 6]
    by_id = {a["id"]: a for a in answers}
    assert json.loads(by_id[3]["result"]["content"][0]["text"])["total"] == 0
    assert json.loads(by_id[5]["result"]["content"][0]["text"])["error"] == "no semantic index"


def test_a_burst_with_only_notifications_exits_promptly(tmp_path: Path) -> None:
    """Nothing pending at EOF: the server must not wait for the drain timeout."""
    import time

    db_path = tmp_path / "p.db"
    ProgressDB(db_path).close()
    started = time.monotonic()
    answers = _burst(db_path, tmp_path, [_INIT, _INITIALIZED])
    assert [a["id"] for a in answers] == [1]
    assert time.monotonic() - started < 30


def test_a_cancelled_request_does_not_hold_the_exit(tmp_path: Path) -> None:
    """A request the client cancels is never answered; EOF must not wait for it."""
    import time

    db_path = tmp_path / "p.db"
    ProgressDB(db_path).close()
    started = time.monotonic()
    answers = _burst(
        db_path,
        tmp_path,
        [
            _INIT,
            _INITIALIZED,
            _tool_call(3, "list_tags"),
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 3, "reason": "no longer needed"},
            },
            _tool_call(4, "list_events"),
        ],
    )
    assert 1 in {a["id"] for a in answers} and 4 in {a["id"] for a in answers}
    assert time.monotonic() - started < 30
