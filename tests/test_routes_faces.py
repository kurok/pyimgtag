"""Tests for the extracted faces router at a non-root prefix."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_faces import build_faces_router  # noqa: E402


def test_faces_router_mounted_at_prefix(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base="/faces"), prefix="/faces")

    client = TestClient(app)
    r = client.get("/faces/")
    assert r.status_code == 200
    assert "/faces/api/persons" in r.text  # HTML uses prefixed URLs

    r = client.get("/faces/api/persons")
    assert r.status_code == 200
    assert r.json() == []


def test_faces_router_mounted_at_root(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))

    client = TestClient(app)
    r = client.get("/api/persons")
    assert r.status_code == 200
    assert r.json() == []


def test_with_faces_empty_db(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))

    with TestClient(app) as client:
        r = client.get("/api/persons/with-faces")
    assert r.status_code == 200
    body = r.json()
    assert body == {"total": 0, "items": []}


def test_person_detail_existing_renders_html(tmp_path):
    import sqlite3

    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base="/faces"), prefix="/faces")

    con = sqlite3.connect(str(db_path))
    cur = con.execute(
        "INSERT INTO persons (label, confirmed, source, trusted) VALUES ('Alice',1,'auto',1)"
    )
    pid = cur.lastrowid
    con.commit()
    con.close()

    client = TestClient(app)
    r = client.get(f"/faces/persons/{pid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_person_detail_missing_redirects_to_list(tmp_path):
    # A stale card link (person deleted or re-clustered away after the grid
    # rendered) must bounce back to the faces grid, not dump a raw
    # "Person not found" JSON body to the browser.
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base="/faces"), prefix="/faces")

    client = TestClient(app)
    r = client.get("/faces/persons/160765", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/faces/"

    # Following the redirect lands on the faces grid (200 HTML), not a 404.
    r2 = client.get("/faces/persons/160765")
    assert r2.status_code == 200
    assert "/faces/api/persons" in r2.text


def test_with_faces_pagination(tmp_path):
    """Offset and limit are respected; total reflects the full count."""
    import sqlite3

    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))

    # Seed 12 persons directly so we can test pagination without face thumbnails.
    con = sqlite3.connect(str(db_path))
    for i in range(12):
        con.execute(
            "INSERT INTO persons (label, confirmed, source, trusted) VALUES (?,0,'auto',1)",
            (f"Person {i}",),
        )
    con.commit()
    con.close()

    with TestClient(app) as client:
        r = client.get("/api/persons/with-faces?offset=0&limit=10")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 12
        assert len(body["items"]) == 10

        r2 = client.get("/api/persons/with-faces?offset=10&limit=10")
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["total"] == 12
        assert len(body2["items"]) == 2
