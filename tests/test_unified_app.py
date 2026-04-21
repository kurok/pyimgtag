"""End-to-end tests for the unified webapp (dashboard + review + faces)."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.unified_app import create_unified_app  # noqa: E402


def _seed(tmp_path):
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\x00" * 10)
    db.mark_done(
        img,
        ImageResult(file_path=str(img), file_name="a.jpg", tags=["sun"]),
    )
    db.close()
    return db_path


def test_unified_app_serves_dashboard_at_root(tmp_path):
    app = create_unified_app(db_path=_seed(tmp_path))
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    # Nav bar present with all three sections
    assert 'href="/"' in body
    assert 'href="/review"' in body
    assert 'href="/faces"' in body
    # Dashboard markers
    assert 'id="state"' in body
    assert "/api/run/current" in body


def test_unified_app_dashboard_api_still_works(tmp_path):
    app = create_unified_app(db_path=_seed(tmp_path))
    client = TestClient(app)
    r = client.get("/api/run/current")
    assert r.status_code == 200
    assert r.json() == {"active": False}


def test_unified_app_review_at_prefix(tmp_path):
    app = create_unified_app(db_path=_seed(tmp_path))
    client = TestClient(app)
    r = client.get("/review/")
    assert r.status_code == 200
    assert "/review/api/stats" in r.text  # HTML has prefixed URLs
    r = client.get("/review/api/stats")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    r = client.get("/review/api/images")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_unified_app_faces_at_prefix(tmp_path):
    app = create_unified_app(db_path=_seed(tmp_path))
    client = TestClient(app)
    r = client.get("/faces/")
    assert r.status_code == 200
    assert "/faces/api/persons" in r.text
    r = client.get("/faces/api/persons")
    assert r.status_code == 200
    assert r.json() == []
