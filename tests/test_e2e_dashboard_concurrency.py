"""End to end: concurrent dashboard writes keep the shared database consistent.

Starts the real dashboard (``python -m pyimgtag.webapp``) on a seeded database
and fires person merges, tag renames and reads at it from several threads,
then checks that no request failed with a 5xx and no face points at a person
that no longer exists.

This is a regression smoke over the real server: none of these requests fails
mid-write, so it does not reproduce the rollback race itself. That race is
pinned deterministically in ``test_progress_db_concurrency.py``.
"""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests

from pyimgtag.db.progress_db import ProgressDB
from tests.e2e_support import dashboard

pytest.importorskip("uvicorn")
pytest.importorskip("fastapi")

_PAIRS = 12


def _seed(db_path: Path) -> None:
    with ProgressDB(db_path) as db:
        conn = db._conn
        for pid in range(1, 2 * _PAIRS + 1):
            conn.execute("INSERT INTO persons (id, label) VALUES (?, ?)", (pid, f"p{pid}"))
            conn.executemany(
                "INSERT INTO faces (image_path, person_id) VALUES (?, ?)",
                [(f"/lib/{pid}-{i}.jpg", pid) for i in range(5)],
            )
        conn.executemany(
            "INSERT INTO processed_images (file_path, tags, status) VALUES (?, ?, 'ok')",
            [(f"/lib/img{i}.jpg", json.dumps([f"tag{i % 6}", "common"])) for i in range(60)],
        )
        conn.commit()


def test_concurrent_merges_and_tag_edits_leave_no_orphans(tmp_path: Path) -> None:
    db_path = tmp_path / "progress.db"
    _seed(db_path)

    with dashboard(db_path, tmp_path) as base, ThreadPoolExecutor(max_workers=12) as pool:
        session_calls = []
        for k in range(_PAIRS):
            src, dst = 2 * k + 1, 2 * k + 2
            session_calls.append(("POST", f"{base}/faces/api/persons/{src}/merge/{dst}", None))
            session_calls.append(
                (
                    "POST",
                    f"{base}/tags/api/tags/rename",
                    {"old_tag": f"tag{k % 6}", "new_tag": f"r{k}"},
                )
            )
            session_calls.append(("GET", f"{base}/faces/api/persons", None))
            session_calls.append(("GET", f"{base}/tags/api/tags", None))

        def call(spec: tuple[str, str, dict | None]) -> int:
            method, url, body = spec
            return requests.request(method, url, json=body, timeout=30).status_code

        statuses = list(pool.map(call, session_calls))

    assert all(status < 500 for status in statuses), statuses
    conn = sqlite3.connect(db_path)
    try:
        orphans = conn.execute(
            "SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL"
            " AND person_id NOT IN (SELECT id FROM persons)"
        ).fetchone()[0]
        persons = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
        tagged = conn.execute(
            "SELECT COUNT(*) FROM processed_images WHERE tags LIKE '%common%'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert orphans == 0
    assert persons == _PAIRS
    assert tagged == 60
