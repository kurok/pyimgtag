"""Tests for the image query builder router."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_query import build_query_router  # noqa: E402


def _seeded_db(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\x00")
    db.mark_done(
        img,
        ImageResult(
            file_path=str(img),
            file_name="a.jpg",
            tags=["sunset"],
            cleanup_class="delete",
            scene_category="landscape",
        ),
    )
    img2 = tmp_path / "b.jpg"
    img2.write_bytes(b"\x01")
    db.mark_done(
        img2,
        ImageResult(file_path=str(img2), file_name="b.jpg", tags=["portrait"]),
    )
    return db


def test_query_router_html_at_root(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_query_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "/api/images" in r.text


def test_query_router_html_at_prefix(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_query_router(db, api_base="/query"), prefix="/query")
    client = TestClient(app)
    r = client.get("/query/")
    assert r.status_code == 200
    assert "/query/api/images" in r.text


def test_query_router_html_includes_nav(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_query_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/")
    assert 'href="/query"' in r.text
    assert "nav-link active" in r.text


def test_query_images_no_filter_returns_all(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_query_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/images")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_query_images_filter_by_tag(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_query_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/images?tag=sunset")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert "sunset" in data[0]["tags_list"]


def test_query_images_filter_by_tag_no_match(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_query_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/images?tag=nonexistent")
    assert r.json() == []


def test_query_images_filter_by_cleanup(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_query_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/images?cleanup=delete")
    assert r.status_code == 200
    assert len(r.json()) == 1
    r2 = client.get("/api/images?cleanup=review")
    assert r2.json() == []


def test_query_images_respects_limit(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_query_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/images?limit=1")
    assert r.status_code == 200
    assert len(r.json()) == 1
