"""Tests for webapp bootstrap helper and shared flag helper."""

from __future__ import annotations

import argparse
import socket

import pytest

from pyimgtag import run_registry
from pyimgtag.webapp.config import add_web_flags


@pytest.fixture(autouse=True)
def _reset_registry():
    run_registry.set_current(None)
    yield
    run_registry.set_current(None)


def _parser_with_flags() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    add_web_flags(p)
    return p


def test_add_web_flags_defaults():
    args = _parser_with_flags().parse_args([])
    assert args.web is False
    assert args.no_web is False
    assert args.web_host == "127.0.0.1"
    assert args.web_port == 8770
    assert args.no_browser is False


def test_add_web_flags_all_set():
    args = _parser_with_flags().parse_args(
        ["--web", "--web-host", "0.0.0.0", "--web-port", "9999", "--no-browser"]
    )
    assert args.web is True
    assert args.web_host == "0.0.0.0"
    assert args.web_port == 9999
    assert args.no_browser is True


def test_start_dashboard_for_no_web_returns_none(monkeypatch):
    monkeypatch.delenv("PYIMGTAG_NO_WEB", raising=False)
    from pyimgtag.webapp.bootstrap import start_dashboard_for

    args = _parser_with_flags().parse_args(["--no-web"])
    session, dashboard = start_dashboard_for(args, command="judge")
    assert session is None
    assert dashboard is None
    assert run_registry.get_current() is None


def test_start_dashboard_for_registers_session_and_stops_cleanly(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    monkeypatch.delenv("PYIMGTAG_NO_WEB", raising=False)

    from pyimgtag.webapp.bootstrap import start_dashboard_for

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]

    args = _parser_with_flags().parse_args(["--web", "--web-port", str(free_port), "--no-browser"])
    session, dashboard = start_dashboard_for(args, command="judge")
    try:
        assert session is not None
        assert session.command == "judge"
        assert run_registry.get_current() is session
        assert dashboard is not None
        assert dashboard.url == f"http://127.0.0.1:{free_port}"
    finally:
        if dashboard is not None:
            dashboard.stop()
        run_registry.set_current(None)


def test_start_dashboard_for_warns_and_returns_none_on_import_error(monkeypatch, capsys):
    monkeypatch.delenv("PYIMGTAG_NO_WEB", raising=False)

    from pyimgtag.webapp import bootstrap

    def _raise(*_a, **_kw):
        raise ImportError("fake missing uvicorn")

    monkeypatch.setattr("pyimgtag.webapp.server_thread.DashboardServer.__init__", _raise)
    args = _parser_with_flags().parse_args(["--web", "--no-browser"])
    session, dashboard = bootstrap.start_dashboard_for(args, command="faces scan")
    assert session is None
    assert dashboard is None
    assert run_registry.get_current() is None
    err = capsys.readouterr().err
    assert "dashboard disabled" in err
