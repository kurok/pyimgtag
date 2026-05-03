"""Tests for the judge ranking router."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult, JudgeResult, JudgeScores  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_judge import build_judge_router  # noqa: E402


def _seeded_db(tmp_path):
    """Seed two judged images so sort / filter assertions have something to bite on.

    Both rows also get a matching ``processed_images`` entry so the LEFT
    JOIN exposes the scene-summary / location columns the new API returns.
    """
    db = ProgressDB(db_path=tmp_path / "progress.db")
    samples = [
        ("/img/low.jpg", 3, "low summary"),
        ("/img/high.jpg", 9, "high summary"),
    ]
    from pathlib import Path as _P

    for path, score, summary in samples:
        db.mark_done(
            _P(path),
            ImageResult(
                file_path=path,
                file_name=path.split("/")[-1],
                source_type="directory",
                tags=[],
                scene_summary=summary,
                processing_status="ok",
            ),
        )
        db.save_judge_result(
            JudgeResult(
                file_path=path,
                file_name=path.split("/")[-1],
                weighted_score=score,
                core_score=score,
                visible_score=score,
                scores=JudgeScores(verdict="test verdict", reason=f"because {score}"),
            )
        )
    return db


def test_judge_router_html_at_root(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "/api/scores" in r.text


def test_judge_router_html_at_prefix(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base="/judge"), prefix="/judge")
    client = TestClient(app)
    r = client.get("/judge/")
    assert r.status_code == 200
    assert "/judge/api/scores" in r.text


def test_judge_router_html_includes_nav(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/")
    assert 'href="/judge"' in r.text
    assert "nav-link active" in r.text


def test_list_scores_empty(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/scores")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


def test_list_scores_paginated_shape(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/scores")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)
    assert data["total"] == 2
    items = data["items"]
    assert len(items) == 2
    # Default sort is rating_desc — high.jpg sorts first.
    assert items[0]["file_name"] == "high.jpg"
    assert items[0]["weighted_score"] == 9
    assert items[1]["file_name"] == "low.jpg"


def test_list_scores_includes_required_fields(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/scores")
    item = r.json()["items"][0]
    for key in (
        "file_path",
        "file_name",
        "weighted_score",
        "reason",
        "verdict",
        "scene_summary",
        "image_date",
        "nearest_city",
        "nearest_country",
        "cleanup_class",
    ):
        assert key in item, f"missing key: {key}"


def test_list_scores_limit_and_offset(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    first = client.get("/api/scores", params={"limit": 1, "offset": 0}).json()
    second = client.get("/api/scores", params={"limit": 1, "offset": 1}).json()
    assert first["total"] == 2 and second["total"] == 2
    assert len(first["items"]) == 1 and len(second["items"]) == 1
    assert first["items"][0]["file_name"] != second["items"][0]["file_name"]


def test_list_scores_sort_rating_asc(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/scores", params={"sort": "rating_asc"})
    items = r.json()["items"]
    assert items[0]["file_name"] == "low.jpg"
    assert items[1]["file_name"] == "high.jpg"


def test_list_scores_min_max_rating_filter(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    only_high = client.get("/api/scores", params={"min_rating": 8}).json()
    assert only_high["total"] == 1
    assert only_high["items"][0]["file_name"] == "high.jpg"
    only_low = client.get("/api/scores", params={"max_rating": 4}).json()
    assert only_low["total"] == 1
    assert only_low["items"][0]["file_name"] == "low.jpg"
    # Out-of-range bounds clamp to [1, 10] silently.
    clamped = client.get("/api/scores", params={"min_rating": 999}).json()
    assert clamped["total"] == 0


def test_list_scores_unknown_sort_falls_back(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/scores", params={"sort": "garbage"})
    # Whitelist falls back to rating_desc.
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["file_name"] == "high.jpg"
