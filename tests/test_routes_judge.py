"""Tests for the judge scores ranking router."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import JudgeResult, JudgeScores  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_judge import build_judge_router  # noqa: E402


def _seeded_db(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    for path, score in [("/img/low.jpg", 3.5), ("/img/high.jpg", 9.0)]:
        db.save_judge_result(
            JudgeResult(
                file_path=path,
                file_name=path.split("/")[-1],
                weighted_score=score,
                core_score=score,
                visible_score=score,
                scores=JudgeScores(verdict="test verdict"),
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
    assert r.json() == []


def test_list_scores_returns_all_ordered(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/scores")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert data[0]["file_name"] == "high.jpg"
    assert data[0]["weighted_score"] == pytest.approx(9.0)
    assert data[1]["file_name"] == "low.jpg"


def test_list_scores_includes_required_fields(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/scores")
    item = r.json()[0]
    for key in (
        "file_path",
        "file_name",
        "weighted_score",
        "core_score",
        "visible_score",
        "verdict",
        "scored_at",
    ):
        assert key in item, f"missing key: {key}"


def test_list_scores_limit_param(tmp_path):
    db = _seeded_db(tmp_path)
    app = FastAPI()
    app.include_router(build_judge_router(db, api_base=""))
    client = TestClient(app)
    r = client.get("/api/scores?limit=1")
    assert r.status_code == 200
    assert len(r.json()) == 1
