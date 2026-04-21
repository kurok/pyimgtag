"""Tests for the live run dashboard FastAPI app."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag import run_registry  # noqa: E402
from pyimgtag.run_session import RunSession  # noqa: E402
from pyimgtag.webapp.dashboard_server import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_registry():
    run_registry.set_current(None)
    yield
    run_registry.set_current(None)


def test_index_serves_html():
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "pyimgtag" in r.text.lower()


def test_current_run_returns_inactive_when_no_session():
    client = TestClient(create_app())
    r = client.get("/api/run/current")
    assert r.status_code == 200
    assert r.json() == {"active": False}


def test_current_run_returns_snapshot_when_active():
    s = RunSession(command="run")
    s.mark_running()
    s.set_counter("processed", 5)
    run_registry.set_current(s)

    client = TestClient(create_app())
    r = client.get("/api/run/current")
    assert r.status_code == 200
    body = r.json()
    assert body["active"] is True
    assert body["command"] == "run"
    assert body["state"] == "running"
    assert body["counters"]["processed"] == 5


def test_pause_without_session_returns_404():
    client = TestClient(create_app())
    r = client.post("/api/run/current/pause")
    assert r.status_code == 404


def test_pause_and_unpause_transitions():
    s = RunSession(command="run")
    s.mark_running()
    run_registry.set_current(s)

    client = TestClient(create_app())

    r = client.post("/api/run/current/pause")
    assert r.status_code == 200
    assert r.json()["state"] in {"pausing", "paused"}

    r = client.post("/api/run/current/unpause")
    assert r.status_code == 200
    assert r.json()["state"] == "running"


def test_unpause_without_session_returns_404():
    client = TestClient(create_app())
    r = client.post("/api/run/current/unpause")
    assert r.status_code == 404
