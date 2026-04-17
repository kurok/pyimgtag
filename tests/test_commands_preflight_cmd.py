"""Tests for the preflight subcommand handler."""

from __future__ import annotations

import argparse
from unittest.mock import patch


class TestCmdPreflight:
    def _args(self, input_dir=None, photos_library=None):
        ns = argparse.Namespace()
        ns.input_dir = input_dir
        ns.photos_library = photos_library
        ns.ollama_url = "http://localhost:11434"
        ns.model = "gemma4:e4b"
        return ns

    def test_input_dir_all_pass_returns_0(self):
        from pyimgtag.commands.preflight_cmd import cmd_preflight

        checks = [("ollama", True, "Ollama is running"), ("model", True, "Model found")]
        with patch("pyimgtag.commands.preflight_cmd.run_preflight", return_value=checks):
            result = cmd_preflight(self._args(input_dir="/tmp/photos"))
        assert result == 0

    def test_photos_library_some_fail_returns_1(self):
        from pyimgtag.commands.preflight_cmd import cmd_preflight

        checks = [
            ("ollama", True, "Ollama is running"),
            ("model", False, "Model not found"),
        ]
        with patch("pyimgtag.commands.preflight_cmd.run_preflight", return_value=checks):
            result = cmd_preflight(self._args(photos_library="/tmp/lib.photoslibrary"))
        assert result == 1

    def test_input_dir_sets_source_type_directory(self):
        from pyimgtag.commands.preflight_cmd import cmd_preflight

        with patch(
            "pyimgtag.commands.preflight_cmd.run_preflight", return_value=[]
        ) as mock_run:
            cmd_preflight(self._args(input_dir="/tmp/photos"))
        mock_run.assert_called_once_with(
            "http://localhost:11434", "gemma4:e4b", "/tmp/photos", "directory"
        )

    def test_photos_library_sets_source_type(self):
        from pyimgtag.commands.preflight_cmd import cmd_preflight

        with patch(
            "pyimgtag.commands.preflight_cmd.run_preflight", return_value=[]
        ) as mock_run:
            cmd_preflight(self._args(photos_library="/tmp/lib.photoslibrary"))
        mock_run.assert_called_once_with(
            "http://localhost:11434",
            "gemma4:e4b",
            "/tmp/lib.photoslibrary",
            "photos_library",
        )

    def test_output_contains_pass_label(self, capsys):
        from pyimgtag.commands.preflight_cmd import cmd_preflight

        checks = [("check", True, "All good")]
        with patch("pyimgtag.commands.preflight_cmd.run_preflight", return_value=checks):
            cmd_preflight(self._args(input_dir="/tmp"))
        out = capsys.readouterr().out
        assert "[PASS]" in out

    def test_output_contains_fail_label(self, capsys):
        from pyimgtag.commands.preflight_cmd import cmd_preflight

        checks = [("check", False, "Something broke")]
        with patch("pyimgtag.commands.preflight_cmd.run_preflight", return_value=checks):
            cmd_preflight(self._args(input_dir="/tmp"))
        out = capsys.readouterr().out
        assert "[FAIL]" in out
