"""Tests for __main__ entry-point modules."""

from __future__ import annotations

import os
import runpy
import sys
from unittest.mock import MagicMock, patch

import pytest


class TestPyimgtagMain:
    """pyimgtag/__main__.py — minimal coverage for the sys.exit(main()) wrapper."""

    def test_runs_as_main_calls_sys_exit(self):
        """Running the module as __main__ calls sys.exit with main()'s return code."""
        with (
            patch("pyimgtag.main.main", return_value=0) as mock_main,
            patch("sys.exit") as mock_exit,
        ):
            runpy.run_module("pyimgtag", run_name="__main__", alter_sys=False)

        mock_main.assert_called_once()
        mock_exit.assert_called_once_with(0)


class TestMainModuleEntrypoint:
    """pyimgtag/main.py — the ``if __name__ == '__main__'`` wrapper."""

    def test_running_main_module_calls_sys_exit(self, monkeypatch):
        # run_module re-executes the module body, so the ``main`` name inside
        # the ``__main__`` namespace is the freshly-defined function (not a
        # patchable attribute). Feed a benign argv ("no subcommand" → rc 1)
        # and capture the exit code via a patched sys.exit. Disable the
        # update check so the run stays fully offline.
        monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
        with (
            patch.object(sys, "argv", ["pyimgtag"]),
            patch("sys.exit") as mock_exit,
        ):
            runpy.run_module("pyimgtag.main", run_name="__main__", alter_sys=False)

        mock_exit.assert_called_once_with(1)


class TestWebappMainEntrypoint:
    """pyimgtag/webapp/__main__.py — the ``if __name__ == '__main__'`` wrapper."""

    def test_running_webapp_module_calls_main(self):
        if "pyimgtag.webapp.__main__" in sys.modules:
            del sys.modules["pyimgtag.webapp.__main__"]
        mock_uvicorn = MagicMock()
        with patch("pyimgtag.webapp.unified_app.create_unified_app", return_value=MagicMock()):
            with patch.dict(sys.modules, {"uvicorn": mock_uvicorn}):
                # run_module executes the module body, hitting the
                # ``if __name__ == "__main__": main()`` guard.
                runpy.run_module("pyimgtag.webapp", run_name="__main__", alter_sys=False)

        # main() ran iff the guard fired — it ends in uvicorn.run(...).
        mock_uvicorn.run.assert_called_once()


class TestWebappMain:
    """pyimgtag/webapp/__main__.py — uvicorn startup and ImportError guard."""

    def test_uvicorn_missing_raises_systemexit(self):
        """main() must raise SystemExit with an install hint when uvicorn is absent."""
        # Import fresh each time by removing cached version
        if "pyimgtag.webapp.__main__" in sys.modules:
            del sys.modules["pyimgtag.webapp.__main__"]

        with patch.dict(sys.modules, {"uvicorn": None}):
            from pyimgtag.webapp.__main__ import main

            with pytest.raises(SystemExit):
                main()

    def test_normal_startup_calls_uvicorn_run(self):
        """main() must call uvicorn.run with host/port/log_level from env vars."""
        mock_uvicorn = MagicMock()
        mock_app = MagicMock()

        env_overrides = {
            "HOST": "0.0.0.0",
            "PORT": "9000",
            "PYIMGTAG_LOG_LEVEL": "debug",
        }

        if "pyimgtag.webapp.__main__" in sys.modules:
            del sys.modules["pyimgtag.webapp.__main__"]

        with (
            patch.dict(sys.modules, {"uvicorn": mock_uvicorn}),
            patch("pyimgtag.webapp.unified_app.create_unified_app", return_value=mock_app),
            patch.dict(os.environ, env_overrides, clear=False),
        ):
            from pyimgtag.webapp.__main__ import main

            main()

        mock_uvicorn.run.assert_called_once()
        _, kwargs = mock_uvicorn.run.call_args
        assert kwargs.get("host") == "0.0.0.0"
        assert kwargs.get("port") == 9000
        assert kwargs.get("log_level") == "debug"
