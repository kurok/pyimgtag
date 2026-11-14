"""Tests for the review subcommand handler (served through ``serve_unified``)."""

from __future__ import annotations

import argparse
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")


def _args() -> argparse.Namespace:
    return argparse.Namespace(db="/tmp/test.db", host="127.0.0.1", port=5000, no_browser=True)


def _fake_uvicorn(**run_kwargs) -> types.ModuleType:
    module = types.ModuleType("uvicorn")
    module.run = MagicMock(**run_kwargs)  # type: ignore[attr-defined]
    return module


def test_review_serves_the_unified_app_and_returns_0(capsys):
    from pyimgtag.commands.review_cmd import cmd_review

    uvicorn = _fake_uvicorn()
    with (
        patch.dict(sys.modules, {"uvicorn": uvicorn}),
        patch("pyimgtag.webapp.unified_app.create_unified_app") as create,
    ):
        assert cmd_review(_args()) == 0
    create.assert_called_once_with(db_path="/tmp/test.db")
    uvicorn.run.assert_called_once_with(
        create.return_value, host="127.0.0.1", port=5000, log_level="info"
    )
    assert "pyimgtag webapp: http://127.0.0.1:5000 (review at /review)" in capsys.readouterr().out


def test_review_calls_serve_unified_with_its_host_port_and_page():
    from pyimgtag.commands import review_cmd

    with patch("pyimgtag.review_server.serve_unified", return_value=0) as serve:
        args = _args()
        args.no_browser = False
        assert review_cmd.cmd_review(args) == 0
    serve.assert_called_once()
    assert serve.call_args.args == ("/tmp/test.db", "127.0.0.1", 5000)
    assert serve.call_args.kwargs["open_path"] == "/review"
    assert serve.call_args.kwargs["open_browser"] is True
    assert serve.call_args.kwargs["label"] == "review server"


def test_review_port_in_use_returns_1(capsys):
    from pyimgtag.commands.review_cmd import cmd_review

    busy = _fake_uvicorn(side_effect=OSError("[Errno 48] Address already in use"))
    with (
        patch.dict(sys.modules, {"uvicorn": busy}),
        patch("pyimgtag.webapp.unified_app.create_unified_app"),
    ):
        assert cmd_review(_args()) == 1
    err = capsys.readouterr().err
    assert "Error: could not start review server on 127.0.0.1:5000" in err


def test_review_without_uvicorn_returns_1(capsys):
    from pyimgtag.commands.review_cmd import cmd_review

    with patch.dict(sys.modules, {"uvicorn": None}):
        assert cmd_review(_args()) == 1
    err = capsys.readouterr().err
    assert "uvicorn is required for the review UI" in err
    assert "[review]" in err


def test_faces_ui_and_review_share_one_port_message(capsys):
    """Both commands answer a busy port the same way (#407)."""
    from pyimgtag.commands.faces import _handle_faces_ui
    from pyimgtag.commands.review_cmd import cmd_review

    faces_args = argparse.Namespace(db="/tmp/test.db", host="127.0.0.1", port=8766, no_browser=True)
    busy = _fake_uvicorn(side_effect=OSError("in use"))
    with (
        patch.dict(sys.modules, {"uvicorn": busy}),
        patch("pyimgtag.webapp.unified_app.create_unified_app"),
    ):
        assert cmd_review(_args()) == 1
        assert _handle_faces_ui(faces_args) == 1
    lines = [line for line in capsys.readouterr().err.splitlines() if line]
    assert lines == [
        "Error: could not start review server on 127.0.0.1:5000: in use",
        "Error: could not start faces UI on 127.0.0.1:8766: in use",
    ]
