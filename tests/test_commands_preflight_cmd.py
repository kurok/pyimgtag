"""Tests for the preflight subcommand handler."""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest


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

        with patch("pyimgtag.commands.preflight_cmd.run_preflight", return_value=[]) as mock_run:
            cmd_preflight(self._args(input_dir="/tmp/photos"))
        mock_run.assert_called_once_with(
            "http://localhost:11434", "gemma4:e4b", "/tmp/photos", "directory"
        )

    def test_photos_library_sets_source_type(self):
        from pyimgtag.commands.preflight_cmd import cmd_preflight

        with patch("pyimgtag.commands.preflight_cmd.run_preflight", return_value=[]) as mock_run:
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


class TestSeverity:
    """#432: only required checks decide the exit code; skips are [SKIP]."""

    @staticmethod
    def _run(checks, capsys):
        import argparse

        from pyimgtag.commands.preflight_cmd import cmd_preflight

        args = argparse.Namespace(
            ollama_url="http://x", model="m", input_dir=None, photos_library=None
        )
        with patch("pyimgtag.commands.preflight_cmd.run_preflight", return_value=checks):
            rc = cmd_preflight(args)
        return rc, capsys.readouterr().out

    def test_only_ffmpeg_missing_is_a_warning_and_exit_0(self, capsys):
        rc, out = self._run(
            [
                ("Ollama", True, "Ollama is reachable"),
                ("Ollama model", True, "model m is available"),
                ("exiftool", True, "exiftool 13.0"),
                ("ffmpeg (video)", False, "ffmpeg is not installed"),
                ("Directory", True, "Directory accessible (3 files)"),
            ],
            capsys,
        )
        assert rc == 0
        assert "[WARN] ffmpeg is not installed" in out
        assert "[FAIL]" not in out

    def test_missing_exiftool_is_a_warning_too(self, capsys):
        rc, out = self._run(
            [("Ollama", True, "ok"), ("exiftool", False, "exiftool is not installed")], capsys
        )
        assert rc == 0
        assert "[WARN] exiftool is not installed" in out

    def test_unreachable_ollama_fails_and_the_model_row_is_skipped(self, capsys):
        rc, out = self._run(
            [
                ("Ollama", False, "Cannot connect to Ollama at http://x"),
                (
                    "Ollama model",
                    False,
                    "skipped: model not checked because Ollama is not reachable",
                ),
                ("ffmpeg (video)", True, "ffmpeg 7"),
            ],
            capsys,
        )
        assert rc == 1
        assert "[FAIL] Cannot connect to Ollama" in out
        assert "[SKIP] model not checked because Ollama is not reachable" in out
        assert out.count("[FAIL]") == 1

    @pytest.mark.parametrize("name", ["Ollama model", "Directory", "Photos library"])
    def test_a_failed_required_check_still_exits_1(self, capsys, name):
        rc, out = self._run([(name, False, "broken")], capsys)
        assert rc == 1
        assert "[FAIL] broken" in out


def test_run_preflight_marks_the_skipped_model_check():
    from pyimgtag.preflight import OPTIONAL_CHECKS, SKIPPED_PREFIX, run_preflight

    with (
        patch("pyimgtag.preflight.check_ollama", return_value=(False, "down")),
        patch("pyimgtag.preflight.check_exiftool", return_value=(True, "ok")),
        patch("pyimgtag.preflight.check_ffmpeg", return_value=(False, "no ffmpeg")),
    ):
        results = dict((n, (p, m)) for n, p, m in run_preflight("http://x", "m"))
    assert results["Ollama model"][1].startswith(SKIPPED_PREFIX)
    # Every optional name is a real check name, so a rename cannot silently
    # turn a warning into a failure.
    assert OPTIONAL_CHECKS <= results.keys()


def test_a_skipped_required_check_is_never_a_pass(capsys):
    """Even if every earlier row passed, a skipped required check exits 1."""
    rc, out = TestSeverity._run(
        [("Ollama", True, "ok"), ("Directory", False, "skipped: source not checked")], capsys
    )
    assert rc == 1
    assert "[SKIP] source not checked" in out
    assert "skipped:" not in out
