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
