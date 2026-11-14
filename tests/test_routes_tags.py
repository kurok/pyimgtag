"""Tests for the tags management router."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_tags import build_tags_router  # noqa: E402


def _seeded_db(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\x00")
    db.mark_done(
        img,
        ImageResult(file_path=str(img), file_name="a.jpg", tags=["sunset", "ocean"]),
    )
    return db


def test_tags_router_html_at_root(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_tags_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "/api/tags" in r.text


def test_tags_router_html_at_prefix(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_tags_router(db, api_base="/tags"), prefix="/tags")
    client = TestClient(app)
    r = client.get("/tags/")
    assert r.status_code == 200
    assert "/tags/api/tags" in r.text


def test_tags_router_html_includes_nav(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_tags_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/")
    assert 'href="/tags"' in r.text
    assert "nav-link active" in r.text


def test_list_tags_returns_tag_and_count(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_tags_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/tags")
    assert r.status_code == 200
    data = r.json()
    tags = {t["tag"]: t["count"] for t in data}
    assert "sunset" in tags
    assert "ocean" in tags
    assert tags["sunset"] == 1


def test_rename_tag_returns_count(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_tags_router(db, api_base=""))
    client = TestClient(app)
    r = client.post("/api/tags/rename", json={"old_tag": "sunset", "new_tag": "dusk"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "count": 1}
    tags_after = {t["tag"] for t in client.get("/api/tags").json()}
    assert "dusk" in tags_after
    assert "sunset" not in tags_after


def test_delete_tag_returns_count(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_tags_router(db, api_base=""))
    client = TestClient(app)
    r = client.post("/api/tags/delete", json={"tag": "sunset"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "count": 1}
    tags_after = {t["tag"] for t in client.get("/api/tags").json()}
    assert "sunset" not in tags_after


def test_merge_tag_returns_count(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_tags_router(db, api_base=""))
    client = TestClient(app)
    r = client.post("/api/tags/merge", json={"source_tag": "sunset", "target_tag": "ocean"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "count": 1}
    tags_after = {t["tag"] for t in client.get("/api/tags").json()}
    assert "sunset" not in tags_after
    assert "ocean" in tags_after


def test_build_tags_router_import_error():
    import sys

    with pytest.raises(ImportError, match="fastapi"):
        with pytest.MonkeyPatch().context() as mp:
            mp.setitem(sys.modules, "fastapi", None)
            build_tags_router(None)


# --- tag mutations run off the event loop (#390) -----------------------------


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("rename_tag", "/tags/api/tags/rename", {"old_tag": "a", "new_tag": "b"}),
        ("merge_tags", "/tags/api/tags/merge", {"source_tag": "a", "target_tag": "b"}),
        ("delete_tag", "/tags/api/tags/delete", {"tag": "a"}),
    ],
)
def test_tag_mutations_do_not_block_other_requests(tmp_path, method, url, body):
    import threading
    from unittest.mock import patch

    import requests

    from pyimgtag.db.image_db import ImageDB
    from pyimgtag.webapp.unified_app import create_unified_app
    from tests.e2e_support import assert_health_stays_responsive

    db_path = tmp_path / "progress.db"
    ProgressDB(db_path=db_path).close()
    entered, release = threading.Event(), threading.Event()

    def slow_mutation(self, *args):
        entered.set()
        release.wait(15.0)
        return 7

    with patch.object(ImageDB, method, slow_mutation):
        response = assert_health_stays_responsive(
            create_unified_app(db_path=db_path),
            lambda base: requests.post(f"{base}{url}", json=body, timeout=30),
            entered,
            release,
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "count": 7}
