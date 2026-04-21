"""Tests for the pyimgtag faces ui subcommand handler."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch


def _make_args(tmp_path):
    return argparse.Namespace(
        faces_action="ui",
        db=str(tmp_path / "progress.db"),
        host="127.0.0.1",
        port=8766,
    )


def test_faces_ui_success_path_invokes_uvicorn(tmp_path, capsys):
    """Happy path: mocks uvicorn.run; verifies the unified app is served on the right port."""
    from pyimgtag.commands.faces import _handle_faces_ui

    args = _make_args(tmp_path)
    with (
        patch("uvicorn.run") as mock_run,
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
    out = capsys.readouterr().out
    assert "pyimgtag webapp" in out
    assert "/faces" in out


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
