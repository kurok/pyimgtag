"""Tests for the pyimgtag faces ui subcommand handler."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch


def _make_args(tmp_path, no_browser=True):
    return argparse.Namespace(
        faces_action="ui",
        db=str(tmp_path / "progress.db"),
        host="127.0.0.1",
        port=8766,
        no_browser=no_browser,
    )


def test_faces_ui_success_path_invokes_uvicorn(tmp_path, capsys):
    """Happy path: mocks uvicorn.run; verifies the unified app is served on the right port."""
    import sys
    import types

    from pyimgtag.commands.faces import _handle_faces_ui

    args = _make_args(tmp_path)
    mock_run = MagicMock()
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = mock_run  # type: ignore[attr-defined]

    with (
        patch.dict(sys.modules, {"uvicorn": fake_uvicorn}),
        patch("pyimgtag.webapp.unified_app.create_unified_app") as mock_create,
    ):
        mock_create.return_value = MagicMock(name="fastapi-app")
        rc = _handle_faces_ui(args)

    assert rc == 0
    mock_create.assert_called_once_with(db_path=args.db)
    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8766
    assert kwargs.get("log_level") == "warning"
    out = capsys.readouterr().out
    assert "pyimgtag faces UI" in out
    assert "/faces" in out


def test_faces_ui_port_in_use_returns_1(tmp_path, capsys):
    """A busy port (OSError from uvicorn.run) must print a helpful message and return 1."""
    from pyimgtag.commands.faces import _handle_faces_ui

    args = _make_args(tmp_path)
    with (
        patch("uvicorn.run", side_effect=OSError("[Errno 48] Address already in use")) as mock_run,
        patch("pyimgtag.webapp.unified_app.create_unified_app") as mock_create,
    ):
        mock_create.return_value = MagicMock(name="fastapi-app")
        rc = _handle_faces_ui(args)

    assert rc == 1
    mock_run.assert_called_once()
    err = capsys.readouterr().err
    assert "could not start faces UI" in err
    assert "127.0.0.1:8766" in err


def test_faces_ui_uvicorn_missing_returns_1(tmp_path, capsys, monkeypatch):
    """ImportError on uvicorn must print a helpful message and return 1."""
    import builtins

    from pyimgtag.commands.faces import _handle_faces_ui

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "uvicorn":
            raise ImportError("no uvicorn in this env")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    rc = _handle_faces_ui(_make_args(tmp_path))
    assert rc == 1
    err = capsys.readouterr().err
    assert "uvicorn is required" in err


def test_faces_ui_unified_app_import_missing_returns_1(tmp_path, capsys, monkeypatch):
    """ImportError on create_unified_app must print the error and return 1."""
    import builtins

    from pyimgtag.commands.faces import _handle_faces_ui

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "pyimgtag.webapp.unified_app":
            raise ImportError("fake missing unified app")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    rc = _handle_faces_ui(_make_args(tmp_path))
    assert rc == 1
    err = capsys.readouterr().err
    assert "fake missing unified app" in err or "Error" in err


def test_faces_ui_opens_browser_thread(tmp_path):
    """When --no-browser is not set, a daemon thread opens the browser."""
    import sys
    import types

    from pyimgtag.commands.faces import _handle_faces_ui

    args = _make_args(tmp_path, no_browser=False)
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = MagicMock()  # type: ignore[attr-defined]

    started_threads = []
    real_thread_cls = None

    import threading as _threading

    real_thread_cls = _threading.Thread

    class _CapturingThread(real_thread_cls):
        def start(self):  # run the target synchronously so webbrowser.open fires
            started_threads.append(self)
            if self._target is not None:
                self._target(*self._args, **self._kwargs)

    with (
        patch.dict(sys.modules, {"uvicorn": fake_uvicorn}),
        patch("pyimgtag.webapp.unified_app.create_unified_app") as mock_create,
        patch("pyimgtag.commands.faces.threading.Thread", _CapturingThread),
        patch("time.sleep"),
        patch("webbrowser.open") as mock_open,
    ):
        mock_create.return_value = MagicMock(name="fastapi-app")
        rc = _handle_faces_ui(args)

    assert rc == 0
    assert started_threads, "expected a browser-opening thread to be started"
    mock_open.assert_called_once()
    assert mock_open.call_args.args[0].endswith("/faces")


def test_faces_ui_browser_open_swallows_exception(tmp_path):
    """webbrowser.open raising must be swallowed silently inside the thread."""
    import sys
    import threading as _threading
    import types

    from pyimgtag.commands.faces import _handle_faces_ui

    args = _make_args(tmp_path, no_browser=False)
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = MagicMock()  # type: ignore[attr-defined]

    real_thread_cls = _threading.Thread

    class _SyncThread(real_thread_cls):
        def start(self):
            if self._target is not None:
                self._target(*self._args, **self._kwargs)

    with (
        patch.dict(sys.modules, {"uvicorn": fake_uvicorn}),
        patch("pyimgtag.webapp.unified_app.create_unified_app") as mock_create,
        patch("pyimgtag.commands.faces.threading.Thread", _SyncThread),
        patch("time.sleep"),
        patch("webbrowser.open", side_effect=RuntimeError("no display")),
    ):
        mock_create.return_value = MagicMock(name="fastapi-app")
        rc = _handle_faces_ui(args)

    assert rc == 0
