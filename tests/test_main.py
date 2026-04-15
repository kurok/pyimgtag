"""Tests for pyimgtag CLI argument parsing."""

from __future__ import annotations

import pytest

from pyimgtag.main import build_parser, main


class TestBuildParser:
    def test_parser_creates_successfully(self):
        assert build_parser() is not None

    def test_version_flag(self):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["--version"])
        assert exc.value.code == 0

    def test_input_dir(self):
        args = build_parser().parse_args(["--input-dir", "/tmp/photos"])
        assert args.input_dir == "/tmp/photos"
        assert args.photos_library is None

    def test_photos_library(self):
        args = build_parser().parse_args(["--photos-library", "/tmp/lib.photoslibrary"])
        assert args.photos_library == "/tmp/lib.photoslibrary"
        assert args.input_dir is None

    def test_mutual_exclusion(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--input-dir", "/a", "--photos-library", "/b"])

    def test_requires_source(self):
        with pytest.raises(SystemExit):
            main([])

    def test_default_model(self):
        args = build_parser().parse_args(["--input-dir", "/tmp"])
        assert args.model == "gemma4:e4b"

    def test_custom_model(self):
        args = build_parser().parse_args(["--model", "gemma4:e12b", "--input-dir", "/tmp"])
        assert args.model == "gemma4:e12b"

    def test_default_max_dim(self):
        args = build_parser().parse_args(["--input-dir", "/tmp"])
        assert args.max_dim == 1280

    def test_limit(self):
        args = build_parser().parse_args(["--input-dir", "/tmp", "--limit", "20"])
        assert args.limit == 20

    def test_date_filters(self):
        args = build_parser().parse_args(
            ["--input-dir", "/tmp", "--date-from", "2026-01-01", "--date-to", "2026-12-31"]
        )
        assert args.date_from == "2026-01-01"
        assert args.date_to == "2026-12-31"

    def test_single_date(self):
        args = build_parser().parse_args(["--input-dir", "/tmp", "--date", "2026-04-01"])
        assert args.date == "2026-04-01"

    def test_dry_run_flag(self):
        args = build_parser().parse_args(["--input-dir", "/tmp", "--dry-run"])
        assert args.dry_run is True

    def test_skip_no_gps(self):
        args = build_parser().parse_args(["--input-dir", "/tmp", "--skip-no-gps"])
        assert args.skip_no_gps is True

    def test_extensions_default(self):
        args = build_parser().parse_args(["--input-dir", "/tmp"])
        assert args.extensions == "jpg,jpeg,heic,png"

    def test_extensions_custom(self):
        args = build_parser().parse_args(["--input-dir", "/tmp", "--extensions", "jpg,png"])
        assert args.extensions == "jpg,png"

    def test_preflight_flag(self):
        args = build_parser().parse_args(["--preflight"])
        assert args.preflight is True

    def test_preflight_flag_default_false(self):
        args = build_parser().parse_args(["--input-dir", "/tmp"])
        assert args.preflight is False

    def test_dedup_flag(self):
        args = build_parser().parse_args(["--input-dir", "/tmp", "--dedup"])
        assert args.dedup is True

    def test_dedup_threshold(self):
        args = build_parser().parse_args(["--input-dir", "/tmp", "--dedup-threshold", "3"])
        assert args.dedup_threshold == 3

    def test_db_flag(self):
        args = build_parser().parse_args(["--input-dir", "/tmp", "--db", "/tmp/my.db"])
        assert args.db == "/tmp/my.db"

    def test_no_cache_flag(self):
        args = build_parser().parse_args(["--input-dir", "/tmp", "--no-cache"])
        assert args.no_cache is True

    def test_output_flags(self):
        args = build_parser().parse_args(
            [
                "--input-dir",
                "/tmp",
                "--output-json",
                "out.json",
                "--output-csv",
                "out.csv",
                "--jsonl-stdout",
            ]
        )
        assert args.output_json == "out.json"
        assert args.output_csv == "out.csv"
        assert args.jsonl_stdout is True

    def test_verbose_short(self):
        args = build_parser().parse_args(["--input-dir", "/tmp", "-v"])
        assert args.verbose is True


class TestMainNoSource:
    def test_missing_dir_returns_error(self):
        result = main(["--input-dir", "/nonexistent/path/12345"])
        assert result == 1
