"""End to end: tag changes that would do nothing are refused (#399).

Drives the real CLI (``python -m pyimgtag``) and the real dashboard
(``python -m pyimgtag.webapp``) as child processes against a database in
``tmp_path`` (see ``tests/e2e_support.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB
from tests.e2e_support import dashboard, run_cli


def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "x.db"
    with ProgressDB(db_path) as db:
        img = tmp_path / "a.jpg"
        db.mark_done(img, ImageResult(file_path=str(img), file_name="a.jpg", tags=["beach"]))
    return db_path


def test_cli_reports_an_unknown_tag_and_a_no_op(tmp_path: Path) -> None:
    db = str(_db(tmp_path))

    unknown = run_cli("tags", "delete", "nosuchtag", "--db", db, home=tmp_path)
    assert unknown.returncode == 1
    assert "Error: no tag 'nosuchtag' (see 'pyimgtag tags list')" in unknown.stderr

    same = run_cli("tags", "merge", "beach", "beach", "--db", db, home=tmp_path)
    assert same.returncode == 2
    assert "Error: source and target are the same tag" in same.stderr

    real = run_cli("tags", "rename", "beach", "shore", "--db", db, home=tmp_path)
    assert real.returncode == 0
    assert "Renamed 'beach' → 'shore' in 1 image(s)." in real.stderr


def test_dashboard_api_answers_404_and_422(tmp_path: Path) -> None:
    pytest.importorskip("uvicorn")
    db = _db(tmp_path)
    with dashboard(db, tmp_path) as base:
        unknown = requests.post(f"{base}/tags/api/tags/delete", json={"tag": "nosuch"}, timeout=10)
        empty = requests.post(
            f"{base}/tags/api/tags/rename", json={"old_tag": "beach", "new_tag": ""}, timeout=10
        )
        ok = requests.post(
            f"{base}/tags/api/tags/rename",
            json={"old_tag": "beach", "new_tag": "shore"},
            timeout=10,
        )

    assert (unknown.status_code, unknown.json()) == (404, {"detail": "no tag 'nosuch'"})
    assert (empty.status_code, empty.json()) == (422, {"detail": "new tag name is empty"})
    assert (ok.status_code, ok.json()) == (200, {"ok": True, "count": 1})
