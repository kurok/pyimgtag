"""Tests for the map page router and the vendored-static route."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_map import (  # noqa: E402
    DEFAULT_TILE_ATTRIBUTION,
    DEFAULT_TILE_URL,
    build_map_router,
    render_map_html,
    tile_config,
)
from pyimgtag.webapp.routes_static import STATIC_FILES, build_static_router  # noqa: E402


def _seeded_db(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    for idx, lat, lon in ((0, 39.36, -9.15), (1, 39.37, -9.16), (2, 48.85, 2.35), (3, None, None)):
        path = Path(f"/img/{idx}.jpg")
        db.mark_done(
            path,
            ImageResult(
                file_path=str(path),
                file_name=path.name,
                tags=["x"],
                gps_lat=lat,
                gps_lon=lon,
                image_date="2024-03-17T10:00:00",
                processing_status="ok",
            ),
        )
    return db


def _client(db, api_base="", prefix=""):
    app = FastAPI()
    app.include_router(build_map_router(db, api_base=api_base), prefix=prefix)
    app.include_router(build_static_router(), prefix="/static")
    return TestClient(app)


# --- page --------------------------------------------------------------------


def test_html_at_root(tmp_path):
    client = _client(ProgressDB(db_path=tmp_path / "p.db"))
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>pyimgtag Map</title>" in r.text
    assert "/api/clusters" in r.text


def test_html_at_prefix_and_nav_active(tmp_path):
    client = _client(ProgressDB(db_path=tmp_path / "p.db"), api_base="/map", prefix="/map")
    r = client.get("/map/")
    assert r.status_code == 200
    # The page builds its URLs from API_BASE at runtime, so the prefix
    # reaches the browser through that constant.
    assert "const API_BASE = '/map';" in r.text
    assert "/api/clusters" in r.text
    assert 'href="/map"' in r.text
    assert "nav-link active" in r.text


def test_page_template_markers():
    html = render_map_html("/map")
    assert ":root{--bg:" in html
    assert '<nav class="nav">' in html
    assert not re.findall(r"__[A-Z][A-Z0-9_]+__", html)


def test_page_references_only_vendored_assets():
    """The AC: no CDN anywhere. Only the tile URL may point off-host."""
    html = render_map_html("/map")
    assert "/static/leaflet/leaflet.js" in html
    assert "/static/leaflet/leaflet.css" in html
    for banned in ("unpkg", "cdn", "jsdelivr", "cdnjs"):
        assert banned not in html.lower(), banned
    external = re.findall(r"""(?:src|href)\s*=\s*['"](https?:)?//[^'"]+""", html)
    assert not external, external


def _tile_hosts(html: str) -> set[str]:
    """Exact hostnames of every absolute URL embedded in the page.

    Uses ``urlparse().hostname`` rather than substring containment so a
    lookalike host (e.g. ``evil-tile.openstreetmap.org.example.com``)
    cannot be mistaken for the real tile host.
    """
    return {urlparse(u).hostname for u in re.findall(r"""https?://[^\s"'<>]+""", html)}


def test_tile_url_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("PYIMGTAG_MAP_TILES", "http://tiles.lan/{z}/{x}/{y}.png")
    monkeypatch.setenv("PYIMGTAG_MAP_TILES_ATTRIBUTION", "local tiles")
    assert tile_config() == {
        "url": "http://tiles.lan/{z}/{x}/{y}.png",
        "attribution": "local tiles",
    }
    html = render_map_html("/map")
    hosts = _tile_hosts(html)
    assert "tiles.lan" in hosts
    assert "tile.openstreetmap.org" not in hosts


def test_tile_url_defaults_to_openstreetmap(monkeypatch):
    monkeypatch.delenv("PYIMGTAG_MAP_TILES", raising=False)
    monkeypatch.delenv("PYIMGTAG_MAP_TILES_ATTRIBUTION", raising=False)
    assert tile_config() == {"url": DEFAULT_TILE_URL, "attribution": DEFAULT_TILE_ATTRIBUTION}
    html = render_map_html("/map")
    # Exact hostname equality on the configured URL (CodeQL rejects substring /
    # containment checks against hostnames as incomplete sanitization).
    assert urlparse(DEFAULT_TILE_URL).hostname == "tile.openstreetmap.org"
    assert DEFAULT_TILE_URL in html
    assert "OpenStreetMap" in html


def test_tile_config_is_escaped_into_the_page(monkeypatch):
    """The tile URL is the one env-controlled value on the page."""
    monkeypatch.setenv("PYIMGTAG_MAP_TILES_ATTRIBUTION", "</script><script>bad()</script>")
    html = render_map_html("/map")
    assert "<script>bad()" not in html


# --- api ---------------------------------------------------------------------


def test_api_coverage(tmp_path):
    client = _client(_seeded_db(tmp_path))
    assert client.get("/api/coverage").json() == {"with_gps": 3, "without_gps": 1}


def test_api_clusters(tmp_path):
    client = _client(_seeded_db(tmp_path))
    body = client.get("/api/clusters", params={"zoom": 6}).json()
    assert body["zoom"] == 6
    assert body["leaf_zoom"] >= 1
    assert sorted(c["count"] for c in body["clusters"]) == [1, 2]


def test_api_clusters_with_bbox(tmp_path):
    client = _client(_seeded_db(tmp_path))
    body = client.get("/api/clusters", params={"zoom": 6, "bbox": "39,-10,40,-9"}).json()
    assert [c["count"] for c in body["clusters"]] == [2]


def test_api_clusters_rejects_a_bad_bbox(tmp_path):
    client = _client(_seeded_db(tmp_path))
    r = client.get("/api/clusters", params={"bbox": "1,2,3"})
    assert r.status_code == 400
    assert "4 comma-separated" in r.json()["detail"]


def test_api_clusters_validates_zoom(tmp_path):
    client = _client(_seeded_db(tmp_path))
    assert client.get("/api/clusters", params={"zoom": -1}).status_code == 422
    assert client.get("/api/clusters", params={"zoom": 99}).status_code == 422


def test_api_photos_lists_leaf_photos(tmp_path):
    client = _client(_seeded_db(tmp_path))
    rows = client.get("/api/photos", params={"bbox": "39,-10,40,-9"}).json()
    assert sorted(r["file_name"] for r in rows) == ["0.jpg", "1.jpg"]
    assert all(r["gps_lat"] is not None for r in rows)


def test_empty_db_renders_and_reports_zero(tmp_path):
    client = _client(ProgressDB(db_path=tmp_path / "e.db"))
    assert client.get("/").status_code == 200
    assert client.get("/api/coverage").json() == {"with_gps": 0, "without_gps": 0}
    assert client.get("/api/clusters").json()["clusters"] == []


# --- vendored static ---------------------------------------------------------


@pytest.mark.parametrize("asset,content_type", sorted(STATIC_FILES.items()))
def test_static_assets_are_served(tmp_path, asset, content_type):
    client = _client(ProgressDB(db_path=tmp_path / "p.db"))
    r = client.get("/static/" + asset)
    assert r.status_code == 200
    assert r.headers["content-type"] == content_type
    assert r.content


def test_static_leaflet_js_is_the_real_library(tmp_path):
    client = _client(ProgressDB(db_path=tmp_path / "p.db"))
    body = client.get("/static/leaflet/leaflet.js").text
    assert "Leaflet" in body
    # Source maps are deliberately not vendored, so nothing may reference one.
    assert "sourceMappingURL" not in body


def test_static_unknown_asset_is_404(tmp_path):
    client = _client(ProgressDB(db_path=tmp_path / "p.db"))
    assert client.get("/static/leaflet/nope.js").status_code == 404
    assert client.get("/static/../pyproject.toml").status_code == 404


def test_missing_fastapi_raises_importerror(tmp_path):
    from unittest.mock import patch

    db = ProgressDB(db_path=tmp_path / "guard.db")
    with patch.dict("sys.modules", {"fastapi": None}):
        with pytest.raises(ImportError, match="fastapi is required"):
            build_map_router(db)
        with pytest.raises(ImportError, match="fastapi is required"):
            build_static_router()


def test_unified_app_mounts_map(tmp_path):
    from pyimgtag.webapp.unified_app import create_unified_app

    client = TestClient(create_unified_app(db_path=tmp_path / "u.db"))
    assert client.get("/map/").status_code == 200
    assert client.get("/map/api/coverage").json()["with_gps"] == 0
    assert client.get("/map/api/clusters").status_code == 200
    assert client.get("/static/leaflet/leaflet.css").status_code == 200
    # Nav on every page links to the new section.
    assert 'href="/map"' in client.get("/judge/").text
