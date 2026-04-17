"""Tests for the review subcommand handler."""

from __future__ import annotations

import argparse
import sys
from unittest.mock import MagicMock, patch


class TestCmdReview:
    def _args(self):
        ns = argparse.Namespace()
        ns.db = "/tmp/test.db"
        ns.host = "127.0.0.1"
        ns.port = 5000
        ns.no_browser = True
        return ns

    def test_serve_called_returns_0(self):
        from pyimgtag.commands.review_cmd import cmd_review

        mock_serve = MagicMock(return_value=None)
        fake_module = MagicMock()
        fake_module.serve = mock_serve
        with patch.dict(sys.modules, {"pyimgtag.review_server": fake_module}):
            result = cmd_review(self._args())
        assert result == 0
        mock_serve.assert_called_once_with(
            db_path="/tmp/test.db",
            host="127.0.0.1",
            port=5000,
            open_browser=False,
        )

    def test_import_error_returns_1(self, capsys):
        from pyimgtag.commands.review_cmd import cmd_review

        with patch.dict(sys.modules, {"pyimgtag.review_server": None}):
            result = cmd_review(self._args())
        assert result == 1
        assert capsys.readouterr().err != ""

    def test_serve_import_error_returns_1(self, capsys):
        from pyimgtag.commands.review_cmd import cmd_review

        fake_module = MagicMock()
        fake_module.serve = MagicMock(side_effect=ImportError("missing dep"))
        with patch.dict(sys.modules, {"pyimgtag.review_server": fake_module}):
            result = cmd_review(self._args())
        assert result == 1
        err = capsys.readouterr().err
        assert "missing dep" in err
