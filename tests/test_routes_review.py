"""Tests for the extracted review router at a non-root prefix."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_review import build_review_router  # noqa: E402


def _seed_db(tmp_path):
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\x00" * 10)
    db.mark_done(
        img,
        ImageResult(
            file_path=str(img),
            file_name="a.jpg",
            tags=["sun"],
            scene_summary="sunny",
            cleanup_class="review",
        ),
    )
    return db_path


def test_review_router_mounted_at_prefix_serves_prefixed_paths(tmp_path):
    db = ProgressDB(db_path=_seed_db(tmp_path))
    app = FastAPI()
    app.include_router(build_review_router(db, api_base="/review"), prefix="/review")

    client = TestClient(app)
    r = client.get("/review/")
    assert r.status_code == 200
    assert "/review/api/stats" in r.text  # HTML uses prefixed URLs

    r = client.get("/review/api/stats")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = client.get("/review/api/images")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_review_router_mounted_at_root_serves_unprefixed_paths(tmp_path):
    db = ProgressDB(db_path=_seed_db(tmp_path))
    app = FastAPI()
    app.include_router(build_review_router(db, api_base=""))

    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert r.json()["total"] == 1
