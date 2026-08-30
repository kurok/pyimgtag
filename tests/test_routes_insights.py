"""Tests for the insights dashboard router."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult, JudgeResult, JudgeScores  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_insights import (  # noqa: E402
    build_insights_router,
    render_insights_html,
)


def _seeded_db(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    for i in range(3):
        path = Path(f"/img/{i}.jpg")
        db.mark_done(
            path,
            ImageResult(
                file_path=str(path),
                file_name=path.name,
                source_type="directory",
                tags=["sea"],
                scene_summary="s",
                processing_status="ok",
            ),
        )
        db.save_judge_result(
            JudgeResult(
                file_path=str(path),
                file_name=path.name,
                weighted_score=3 + i,
                core_score=3 + i,
                visible_score=3 + i,
                scores=JudgeScores(score=3 + i, reason="r"),
            )
        )
    return db


def _client(db, api_base="", prefix=""):
    app = FastAPI()
    app.include_router(build_insights_router(db, api_base=api_base), prefix=prefix)
    return TestClient(app)


def test_html_at_root(tmp_path):
    client = _client(ProgressDB(db_path=tmp_path / "p.db"))
    r = client.get("/")
    assert r.status_code == 200
    assert "/api/insights" in r.text
    assert "<title>pyimgtag Insights</title>" in r.text


def test_html_at_prefix_and_nav_active(tmp_path):
    client = _client(
        ProgressDB(db_path=tmp_path / "p.db"), api_base="/insights", prefix="/insights"
    )
    r = client.get("/insights/")
    assert r.status_code == 200
    assert "'/insights/api/insights" in r.text or "/insights/api/insights" in r.text
    assert 'href="/insights"' in r.text
    assert "nav-link active" in r.text
    assert "/insights/export" in r.text


def test_page_template_markers():
    html = render_insights_html("/insights")
    assert ":root{--bg:" in html
    assert '<nav class="nav">' in html
    assert not re.findall(r"__[A-Z][A-Z0-9_]+__", html)


def test_api_insights_empty(tmp_path):
    client = _client(ProgressDB(db_path=tmp_path / "p.db"))
    r = client.get("/api/insights")
    assert r.status_code == 200
    doc = r.json()
    assert doc["empty"] is True and doc["overview"]["total"] == 0


def test_api_insights_seeded_and_top_param(tmp_path):
    client = _client(_seeded_db(tmp_path))
    doc = client.get("/api/insights").json()
    assert doc["overview"]["total"] == 3
    assert doc["quality"]["judged"] == 3
    assert [p["score"] for p in doc["quality"]["top_photos"]] == [5, 4, 3]
    limited = client.get("/api/insights", params={"top": 1}).json()
    assert len(limited["quality"]["top_photos"]) == 1
    assert client.get("/api/insights", params={"top": 0}).status_code == 422
    assert client.get("/api/insights", params={"top": 99}).status_code == 422


def test_export_delivers_standalone_html(tmp_path):
    client = _client(_seeded_db(tmp_path))
    r = client.get("/export", params={"thumbnails": 0})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'attachment; filename="pyimgtag-insights.html"' in r.headers["content-disposition"]
    assert r.text.startswith("<!DOCTYPE html>")
    assert "<title>pyimgtag library insights</title>" in r.text
    assert not re.search(r"""(?:src|href)\s*=\s*['"]?\s*(?:https?:)?//""", r.text)
    assert "<script" not in r.text


def test_export_matches_cli_renderer(tmp_path):
    from pyimgtag.insights_report import render_html

    db = _seeded_db(tmp_path)
    client = _client(db)
    exported = client.get("/export", params={"thumbnails": 0}).text
    expected = render_html(db.get_insights(top_n=10), thumb_loader=None)
    # Timestamps differ; everything else must be byte-identical.
    strip = lambda s: re.sub(r"Generated [^<]+", "Generated X", s)  # noqa: E731
    assert strip(exported) == strip(expected)


def test_export_with_thumbnails_uses_review_pipeline(tmp_path, monkeypatch):
    seen: list[str] = []

    def fake(path: str, size: int) -> bytes | None:
        seen.append(path)
        return b"jpeg"

    monkeypatch.setattr("pyimgtag.webapp.routes_review._make_thumbnail", fake)
    client = _client(_seeded_db(tmp_path))
    r = client.get("/export")
    assert r.status_code == 200
    # mark_done stores str(Path(...)), so compare against the same
    # OS-normalized form rather than a hardcoded forward-slash literal.
    assert seen == [str(Path("/img/2.jpg")), str(Path("/img/1.jpg")), str(Path("/img/0.jpg"))]
    assert r.text.count("data:image/jpeg;base64,") == 3


def test_missing_fastapi_raises_importerror(tmp_path):
    from unittest.mock import patch

    db = ProgressDB(db_path=tmp_path / "guard.db")
    with patch.dict("sys.modules", {"fastapi": None}):
        with pytest.raises(ImportError, match="fastapi is required"):
            build_insights_router(db)


def test_unified_app_mounts_insights(tmp_path):
    from pyimgtag.webapp.unified_app import create_unified_app

    client = TestClient(create_unified_app(db_path=tmp_path / "u.db"))
    r = client.get("/insights/")
    assert r.status_code == 200
    assert "/insights/api/insights" in r.text
    assert client.get("/insights/api/insights").json()["empty"] is True
    assert client.get("/insights/export").status_code == 200
    # Nav on every page links to the new section.
    assert 'href="/insights"' in client.get("/judge/").text
