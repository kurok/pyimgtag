"""Tests for ImportError fallbacks when fastapi/uvicorn are missing."""

from __future__ import annotations

import builtins
from unittest.mock import patch

import pytest

_REAL_IMPORT = builtins.__import__


def _import_failing_for(missing: set[str]):
    """Return an __import__ stub that raises ImportError for the given names."""

    def _fake(name, globals=None, locals=None, fromlist=(), level=0):
        if name in missing or name.split(".")[0] in missing:
            raise ImportError(f"No module named {name!r}")
        return _REAL_IMPORT(name, globals, locals, fromlist, level)

    return _fake


def test_create_app_raises_import_error_without_fastapi():
    """create_app must surface a helpful ImportError when fastapi is missing."""
    from pyimgtag.webapp.dashboard_server import create_app

    with patch("builtins.__import__", side_effect=_import_failing_for({"fastapi"})):
        with pytest.raises(ImportError, match="fastapi and uvicorn"):
            create_app()


def test_dashboard_server_init_raises_import_error_without_uvicorn():
    """DashboardServer() must raise ImportError when uvicorn is missing."""
    from pyimgtag.webapp.server_thread import DashboardServer

    with patch("builtins.__import__", side_effect=_import_failing_for({"uvicorn"})):
        with pytest.raises(ImportError, match="uvicorn is required"):
            DashboardServer(app=object(), host="127.0.0.1", port=0)


def test_maybe_start_dashboard_falls_back_on_import_error(capsys, tmp_path):
    """start_dashboard_for should return (None, None) and warn if uvicorn is missing."""
    from pyimgtag import run_registry
    from pyimgtag.main import build_parser
    from pyimgtag.webapp.bootstrap import start_dashboard_for

    run_registry.set_current(None)
    parser = build_parser()
    args = parser.parse_args(["run", "--input-dir", str(tmp_path), "--web", "--no-browser"])

    with patch(
        "pyimgtag.webapp.server_thread.DashboardServer.__init__",
        side_effect=ImportError("fake missing uvicorn"),
    ):
        session, dashboard = start_dashboard_for(args, command="run")

    assert session is None
    assert dashboard is None
    assert run_registry.get_current() is None
    err = capsys.readouterr().err
    assert "dashboard disabled" in err


def test_maybe_start_dashboard_prints_not_ready_when_start_fails(capsys, tmp_path, monkeypatch):
    """When DashboardServer.start() returns False, the 'not yet ready' message should print."""
    from pyimgtag import run_registry
    from pyimgtag.main import build_parser
    from pyimgtag.webapp.bootstrap import start_dashboard_for

    run_registry.set_current(None)
    parser = build_parser()
    args = parser.parse_args(
        ["run", "--input-dir", str(tmp_path), "--web", "--no-browser", "--web-port", "0"]
    )

    # Patch DashboardServer.start to report not-ready without actually binding.
    from pyimgtag.webapp import server_thread

    class _FakeServer:
        host = "127.0.0.1"
        port = 0
        url = "http://127.0.0.1:0"

        def __init__(self, *a, **kw) -> None:  # noqa: D401
            pass

        def start(self, ready_timeout: float = 5.0) -> bool:
            return False

        def stop(self, timeout: float = 3.0) -> None:
            pass

    monkeypatch.setattr(server_thread, "DashboardServer", _FakeServer)

    session, dashboard = start_dashboard_for(args, command="run")
    try:
        assert session is not None
        assert dashboard is not None
        out = capsys.readouterr().out
        assert "not yet ready" in out
    finally:
        run_registry.set_current(None)


def test_maybe_start_dashboard_webbrowser_failure_prints_warning(capsys, tmp_path, monkeypatch):
    """A webbrowser.open() failure must be caught and surfaced as a stderr warning."""
    from pyimgtag import run_registry
    from pyimgtag.main import build_parser
    from pyimgtag.webapp import server_thread
    from pyimgtag.webapp.bootstrap import start_dashboard_for

    run_registry.set_current(None)
    parser = build_parser()
    args = parser.parse_args(["run", "--input-dir", str(tmp_path), "--web", "--web-port", "0"])

    class _FakeServer:
        host = "127.0.0.1"
        port = 0
        url = "http://127.0.0.1:0"

        def __init__(self, *a, **kw) -> None:
            pass

        def start(self, ready_timeout: float = 5.0) -> bool:
            return True

        def stop(self, timeout: float = 3.0) -> None:
            pass

    monkeypatch.setattr(server_thread, "DashboardServer", _FakeServer)
    monkeypatch.setattr(
        "webbrowser.open", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    try:
        session, dashboard = start_dashboard_for(args, command="run")
        err = capsys.readouterr().err
        assert "could not open browser" in err
        assert session is not None
    finally:
        run_registry.set_current(None)
