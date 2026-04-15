"""Tests for pyimgtag CLI."""

from __future__ import annotations

import pytest

from pyimgtag.main import build_parser, main


class TestBuildParser:
    def test_parser_creates_successfully(self):
        parser = build_parser()
        assert parser is not None

    def test_version_flag(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            build_parser().parse_args(["--version"])
        assert exc_info.value.code == 0

    def test_default_model(self):
        parser = build_parser()
        args = parser.parse_args(["tag"])
        assert args.model == "gemma4:e4b"

    def test_custom_model(self):
        parser = build_parser()
        args = parser.parse_args(["--model", "gemma4:e12b", "tag"])
        assert args.model == "gemma4:e12b"

    def test_dry_run_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--dry-run", "tag"])
        assert args.dry_run is True

    def test_batch_size_default(self):
        parser = build_parser()
        args = parser.parse_args(["tag"])
        assert args.batch_size == 10

    def test_batch_size_custom(self):
        parser = build_parser()
        args = parser.parse_args(["--batch-size", "50", "tag"])
        assert args.batch_size == 50

    def test_concurrency_default(self):
        parser = build_parser()
        args = parser.parse_args(["tag"])
        assert args.concurrency == 1

    def test_verbose_flag(self):
        parser = build_parser()
        args = parser.parse_args(["-v", "tag"])
        assert args.verbose is True

    def test_subcommands_exist(self):
        parser = build_parser()
        for cmd in ["tag", "search", "status", "export"]:
            args = parser.parse_args([cmd])
            assert args.command == cmd


class TestMain:
    def test_no_command_shows_help(self, capsys):
        result = main([])
        assert result == 0

    def test_unimplemented_command_returns_error(self):
        result = main(["tag"])
        assert result == 1
