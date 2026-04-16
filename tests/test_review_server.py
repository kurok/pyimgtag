"""Tests for the review UI FastAPI server."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.review_server import create_app  # noqa: E402


def _make_db(tmp_path):
    """Create a ProgressDB with a few test images."""
    db_path = tmp_path / "test.db"
    db = ProgressDB(db_path=db_path)
    imgs = [
        ("a.jpg", ["sunset", "beach"], "Warm sunset.", "delete"),
        ("b.jpg", ["dog", "park"], "Dog in park.", "review"),
        ("c.jpg", ["mountain"], "Snowy peaks.", None),
    ]
    for name, tags, summary, cleanup in imgs:
        img = tmp_path / name
        img.write_bytes(b"\x00" * 10)
        db.mark_done(
            img,
            ImageResult(
                file_path=str(img),
                file_name=name,
                tags=tags,
                scene_summary=summary,
                cleanup_class=cleanup,
            ),
        )
    db.close()
    return db_path


class TestReviewServerRoutes:
    def test_index_returns_html(self, tmp_path):
        app = create_app(db_path=_make_db(tmp_path))
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "pyimgtag" in r.text

    def test_stats_returns_counts(self, tmp_path):
        app = create_app(db_path=_make_db(tmp_path))
        client = TestClient(app)
        r = client.get("/api/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert "ok" in data
        assert "error" in data

    def test_images_returns_all(self, tmp_path):
        app = create_app(db_path=_make_db(tmp_path))
        client = TestClient(app)
        r = client.get("/api/images")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    def test_images_filter_cleanup(self, tmp_path):
        app = create_app(db_path=_make_db(tmp_path))
        client = TestClient(app)
        r = client.get("/api/images?cleanup=delete")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["cleanup_class"] == "delete"

    def test_images_pagination(self, tmp_path):
        app = create_app(db_path=_make_db(tmp_path))
        client = TestClient(app)
        r1 = client.get("/api/images?limit=2&offset=0")
        r2 = client.get("/api/images?limit=2&offset=2")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert len(r1.json()["items"]) == 2
        assert len(r2.json()["items"]) == 1

    def test_images_items_have_tags_list(self, tmp_path):
        app = create_app(db_path=_make_db(tmp_path))
        client = TestClient(app)
        data = client.get("/api/images").json()
        for item in data["items"]:
            assert "tags_list" in item
            assert isinstance(item["tags_list"], list)

    def test_thumbnail_missing_returns_404(self, tmp_path):
        app = create_app(db_path=_make_db(tmp_path))
        client = TestClient(app)
        r = client.get("/thumbnail?path=/nonexistent/file.jpg")
        assert r.status_code == 404

    def test_patch_tags_updates(self, tmp_path):
        db_path = _make_db(tmp_path)
        app = create_app(db_path=db_path)
        client = TestClient(app)
        # Get the path of the first image
        items = client.get("/api/images").json()["items"]
        fp = items[0]["file_path"]
        r = client.patch(
            "/api/images/tags",
            json={"file_path": fp, "tags": ["new", "tag"]},
        )
        assert r.status_code == 200
        updated = r.json()
        assert updated["tags_list"] == ["new", "tag"]

    def test_patch_tags_normalizes(self, tmp_path):
        db_path = _make_db(tmp_path)
        app = create_app(db_path=db_path)
        client = TestClient(app)
        items = client.get("/api/images").json()["items"]
        fp = items[0]["file_path"]
        r = client.patch(
            "/api/images/tags",
            json={"file_path": fp, "tags": ["  UPPER  ", "dupe", "dupe"]},
        )
        assert r.status_code == 200
        assert r.json()["tags_list"] == ["upper", "dupe"]

    def test_patch_tags_unknown_path_returns_404(self, tmp_path):
        app = create_app(db_path=_make_db(tmp_path))
        client = TestClient(app)
        r = client.patch(
            "/api/images/tags",
            json={"file_path": "/no/such/image.jpg", "tags": ["a"]},
        )
        assert r.status_code == 404

    def test_patch_cleanup_set(self, tmp_path):
        db_path = _make_db(tmp_path)
        app = create_app(db_path=db_path)
        client = TestClient(app)
        items = client.get("/api/images").json()["items"]
        # Find the image with no cleanup_class
        fp = next(i["file_path"] for i in items if i["cleanup_class"] is None)
        r = client.patch(
            "/api/images/cleanup",
            json={"file_path": fp, "cleanup_class": "delete"},
        )
        assert r.status_code == 200
        assert r.json()["cleanup_class"] == "delete"

    def test_patch_cleanup_clear(self, tmp_path):
        db_path = _make_db(tmp_path)
        app = create_app(db_path=db_path)
        client = TestClient(app)
        items = client.get("/api/images").json()["items"]
        fp = next(i["file_path"] for i in items if i["cleanup_class"] == "delete")
        r = client.patch(
            "/api/images/cleanup",
            json={"file_path": fp, "cleanup_class": None},
        )
        assert r.status_code == 200
        assert r.json()["cleanup_class"] is None

    def test_patch_cleanup_unknown_path_returns_404(self, tmp_path):
        app = create_app(db_path=_make_db(tmp_path))
        client = TestClient(app)
        r = client.patch(
            "/api/images/cleanup",
            json={"file_path": "/no/such/image.jpg", "cleanup_class": "delete"},
        )
        assert r.status_code == 404
