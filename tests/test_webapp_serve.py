"""The one foreground launcher for the dashboard (#407)."""

from __future__ import annotations

import importlib.util
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from pyimgtag.webapp import serve as serve_mod  # noqa: E402


def _uvicorn(**kwargs) -> types.ModuleType:
    module = types.ModuleType("uvicorn")
    module.run = MagicMock(**kwargs)  # type: ignore[attr-defined]
    return module


class _NowThread:
    """Runs the browser-open thread inline so the test can see it."""

    def __init__(self, target, daemon=None):
        self._target = target

    def start(self):
        self._target()


def test_banner_log_level_and_browser_page(capsys):
    uvicorn = _uvicorn()
    with (
        patch.dict(sys.modules, {"uvicorn": uvicorn}),
        patch("pyimgtag.webapp.unified_app.create_unified_app") as create,
        patch("pyimgtag.webapp.serve.threading.Thread", _NowThread),
        patch("pyimgtag.webapp.serve.time.sleep") as sleep,
        patch("webbrowser.open") as browser,
    ):
        code = serve_mod.serve_unified(
            "/db.sqlite",
            "127.0.0.1",
            9000,
            open_path="/faces",
            open_browser=True,
            log_level="warning",
            banner="hello banner",
        )
    assert code == 0
    create.assert_called_once_with(db_path="/db.sqlite")
    uvicorn.run.assert_called_once_with(
        create.return_value, host="127.0.0.1", port=9000, log_level="warning"
    )
    sleep.assert_called_once_with(serve_mod.BROWSER_OPEN_DELAY)
    browser.assert_called_once_with("http://127.0.0.1:9000/faces")
    assert capsys.readouterr().out == "hello banner\n"


def test_no_browser_no_thread():
    with (
        patch.dict(sys.modules, {"uvicorn": _uvicorn()}),
        patch("pyimgtag.webapp.unified_app.create_unified_app"),
        patch("pyimgtag.webapp.serve.threading.Thread") as thread,
    ):
        assert serve_mod.serve_unified(None, "h", 1) == 0
    thread.assert_not_called()


def test_a_browser_that_fails_to_open_is_ignored():
    with (
        patch.dict(sys.modules, {"uvicorn": _uvicorn()}),
        patch("pyimgtag.webapp.unified_app.create_unified_app"),
        patch("pyimgtag.webapp.serve.threading.Thread", _NowThread),
        patch("pyimgtag.webapp.serve.time.sleep"),
        patch("webbrowser.open", side_effect=RuntimeError("no browser")),
    ):
        assert serve_mod.serve_unified(None, "h", 1, open_browser=True) == 0


def test_a_busy_port_is_one_line_and_exit_1(capsys):
    with (
        patch.dict(sys.modules, {"uvicorn": _uvicorn(side_effect=OSError("in use"))}),
        patch("pyimgtag.webapp.unified_app.create_unified_app"),
    ):
        assert serve_mod.serve_unified(None, "127.0.0.1", 8765, label="thing") == 1
    assert capsys.readouterr().err == "Error: could not start thing on 127.0.0.1:8765: in use\n"


def test_missing_uvicorn_is_one_line_and_exit_1(capsys):
    with patch.dict(sys.modules, {"uvicorn": None}):
        assert serve_mod.serve_unified(None, "h", 1, label="thing") == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: uvicorn is required for the thing.")
    assert "pip install 'pyimgtag[review]'" in err


def test_a_database_that_cannot_be_opened_is_one_line_and_exit_1(capsys):
    with (
        patch.dict(sys.modules, {"uvicorn": _uvicorn()}),
        patch(
            "pyimgtag.webapp.unified_app.create_unified_app",
            side_effect=FileExistsError("[Errno 17] File exists: '/x'"),
        ),
    ):
        assert serve_mod.serve_unified(None, "127.0.0.1", 8765, label="thing") == 1
    assert capsys.readouterr().err == (
        "Error: could not start thing on 127.0.0.1:8765: [Errno 17] File exists: '/x'\n"
    )


_INSTALL = "Install with: pip install 'pyimgtag[review]'"


@pytest.mark.parametrize(
    ("launch", "expected"),
    [
        ("review", f"Error: uvicorn is required for the review UI. {_INSTALL}\n"),
        ("faces", f"Error: uvicorn is required for the faces UI. {_INSTALL}\n"),
        ("webapp", f"uvicorn is required to launch the dashboard standalone. {_INSTALL}\n"),
    ],
)
def test_missing_uvicorn_messages_are_unchanged(capsys, launch, expected):
    """Each entry point keeps the exact wording it printed before #407."""
    import argparse

    with patch.dict(sys.modules, {"uvicorn": None}):
        if launch == "review":
            from pyimgtag.review_server import serve

            assert serve(None, open_browser=False) == 1
        elif launch == "faces":
            from pyimgtag.commands.faces import _handle_faces_ui

            assert _handle_faces_ui(argparse.Namespace(db=None, no_browser=True)) == 1
        else:
            from pyimgtag.webapp import __main__ as entry

            with pytest.raises(SystemExit) as exit_info:
                entry.main()
            assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert err == expected
    assert "[dev]" not in err


def test_python_m_webapp_keeps_its_env_contract(monkeypatch):
    from pyimgtag.webapp import __main__ as entry

    monkeypatch.setenv("HOST", "0.0.0.0")  # nosec B104 — test value only
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setenv("PYIMGTAG_LOG_LEVEL", "debug")
    monkeypatch.setenv("PYIMGTAG_DB", "/data/p.db")
    with patch("pyimgtag.webapp.serve.serve_unified", return_value=0) as serve:
        entry.main()
    serve.assert_called_once()
    assert serve.call_args.args == ("/data/p.db", "0.0.0.0", 8123)
    assert serve.call_args.kwargs["log_level"] == "debug"
    assert serve.call_args.kwargs["label"] == "dashboard"
    with (
        patch("pyimgtag.webapp.serve.serve_unified", return_value=1),
        pytest.raises(SystemExit) as exit_info,
    ):
        entry.main()
    assert exit_info.value.code == 1


def test_the_dead_standalone_apps_are_gone():
    import pyimgtag.review_server as review_server

    assert importlib.util.find_spec("pyimgtag.faces_review_server") is None
    assert not hasattr(review_server, "create_app")
