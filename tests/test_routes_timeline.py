"""Tests for the timeline page router."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult, JudgeResult, JudgeScores  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_timeline import (  # noqa: E402
    build_timeline_router,
    render_timeline_html,
)

_DATES = [
    "2024-03-17T10:00:00",
    "2024-03-17T11:00:00",
    "2024-03-19T09:00:00",
    "2024-04-02T09:00:00",
    "2023-12-25T09:00:00",
]


def _seeded_db(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    for idx, date in enumerate(_DATES):
        path = Path(f"/img/{idx}.jpg")
        db.mark_done(
            path,
            ImageResult(
                file_path=str(path),
                file_name=path.name,
                tags=["x"],
                image_date=date,
                cleanup_class="delete" if idx == 0 else None,
                processing_status="ok",
            ),
        )
    db.save_judge_result(
        JudgeResult(
            file_path=str(Path("/img/0.jpg")),
            file_name="0.jpg",
            weighted_score=8,
            core_score=8,
            visible_score=8,
            scores=JudgeScores(score=8, reason="r"),
        )
    )
    return db


def _client(db, api_base="", prefix=""):
    app = FastAPI()
    app.include_router(build_timeline_router(db, api_base=api_base), prefix=prefix)
    return TestClient(app)


def test_html_at_root(tmp_path):
    client = _client(ProgressDB(db_path=tmp_path / "p.db"))
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>pyimgtag Timeline</title>" in r.text
    assert "/api/months" in r.text


def test_html_at_prefix_and_nav_active(tmp_path):
    client = _client(
        ProgressDB(db_path=tmp_path / "p.db"), api_base="/timeline", prefix="/timeline"
    )
    r = client.get("/timeline/")
    assert r.status_code == 200
    assert "const API_BASE = '/timeline';" in r.text
    assert 'href="/timeline"' in r.text
    assert "nav-link active" in r.text


def test_page_template_markers():
    html = render_timeline_html("/timeline")
    assert ":root{--bg:" in html
    assert '<nav class="nav">' in html
    assert not re.findall(r"__[A-Z][A-Z0-9_]+__", html)
    # No CDN: the page is plain DOM work, no charting library.
    assert not re.findall(r"""(?:src|href)\s*=\s*['"](https?:)?//""", html)


def test_page_offers_the_colour_toggles():
    html = render_timeline_html("/timeline")
    for value in ('value="count"', 'value="judge"', 'value="cleanup"'):
        assert value in html


def test_api_months_golden_counts(tmp_path):
    client = _client(_seeded_db(tmp_path))
    body = client.get("/api/months").json()
    assert body["total"] == len(_DATES)
    assert [(m["period"], m["count"]) for m in body["months"]] == [
        ("2023-12", 1),
        ("2024-03", 3),
        ("2024-04", 1),
    ]


def test_api_months_include_the_colour_metrics(tmp_path):
    client = _client(_seeded_db(tmp_path))
    months = {m["period"]: m for m in client.get("/api/months").json()["months"]}
    assert months["2024-03"]["avg_judge_score"] == 8.0
    assert months["2024-03"]["cleanup"] == {"delete": 1, "review": 0, "keep": 0}
    assert months["2024-04"]["avg_judge_score"] is None


def test_api_days_golden_counts(tmp_path):
    client = _client(_seeded_db(tmp_path))
    body = client.get("/api/days", params={"month": "2024-03"}).json()
    assert body["month"] == "2024-03"
    assert body["total"] == 3
    assert [(d["period"], d["count"]) for d in body["days"]] == [
        ("2024-03-17", 2),
        ("2024-03-19", 1),
    ]


def test_api_days_rejects_a_bad_month(tmp_path):
    client = _client(_seeded_db(tmp_path))
    r = client.get("/api/days", params={"month": "2024-13"})
    assert r.status_code == 400
    assert "YYYY-MM" in r.json()["detail"]


def test_api_days_requires_the_month_param(tmp_path):
    client = _client(_seeded_db(tmp_path))
    assert client.get("/api/days").status_code == 422


def test_empty_db_renders_and_reports_nothing(tmp_path):
    client = _client(ProgressDB(db_path=tmp_path / "e.db"))
    assert client.get("/").status_code == 200
    assert client.get("/api/months").json() == {"months": [], "total": 0}
    assert client.get("/api/days", params={"month": "2024-03"}).json()["days"] == []


def test_missing_fastapi_raises_importerror(tmp_path):
    from unittest.mock import patch

    db = ProgressDB(db_path=tmp_path / "guard.db")
    with patch.dict("sys.modules", {"fastapi": None}):
        with pytest.raises(ImportError, match="fastapi is required"):
            build_timeline_router(db)


def test_unified_app_mounts_timeline(tmp_path):
    from pyimgtag.webapp.unified_app import create_unified_app

    client = TestClient(create_unified_app(db_path=tmp_path / "u.db"))
    assert client.get("/timeline/").status_code == 200
    assert client.get("/timeline/api/months").json()["total"] == 0
    assert client.get("/timeline/api/days", params={"month": "2024-03"}).status_code == 200
    assert 'href="/timeline"' in client.get("/judge/").text


def test_query_page_reads_bbox_and_day_from_the_url(tmp_path):
    """The Map's "Show as grid" and the Timeline's day link both land on /query."""
    from pyimgtag.webapp.routes_query import render_query_html

    html = render_query_html("/query")
    assert 'id="f_bbox"' in html
    assert "['bbox', 'f_bbox']" in html
    assert "['day', 'f_day']" in html


def test_query_api_accepts_bbox_and_day(tmp_path):
    from pyimgtag.webapp.unified_app import create_unified_app

    db = _seeded_db(tmp_path / "seed")
    db.close()
    client = TestClient(create_unified_app(db_path=tmp_path / "seed" / "progress.db"))
    rows = client.get("/query/api/images", params={"day": "2024-03-17"}).json()
    assert sorted(r["file_name"] for r in rows) == ["0.jpg", "1.jpg"]
    assert (
        client.get("/query/api/images", params={"month": "2024-04"}).json()[0]["file_name"]
        == "3.jpg"
    )
    assert len(client.get("/query/api/images", params={"year": "2024"}).json()) == 4
    assert client.get("/query/api/images", params={"day": "2024-3-7"}).status_code == 400
    assert client.get("/query/api/images", params={"bbox": "boom"}).status_code == 400
