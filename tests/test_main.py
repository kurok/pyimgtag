"""Tests for pyimgtag CLI argument parsing and subcommands."""

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

    def test_no_subcommand_returns_1(self):
        result = main([])
        assert result == 1

    def test_run_input_dir(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp/photos"])
        assert args.input_dir == "/tmp/photos"
        assert args.photos_library is None

    def test_run_photos_library(self):
        args = build_parser().parse_args(["run", "--photos-library", "/tmp/lib.photoslibrary"])
        assert args.photos_library == "/tmp/lib.photoslibrary"
        assert args.input_dir is None

    def test_run_mutual_exclusion(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", "--input-dir", "/a", "--photos-library", "/b"])

    def test_run_requires_source(self):
        with pytest.raises(SystemExit):
            main(["run"])

    def test_run_default_model(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp"])
        assert args.model == "gemma4:e4b"

    def test_run_custom_model(self):
        args = build_parser().parse_args(["run", "--model", "gemma4:e12b", "--input-dir", "/tmp"])
        assert args.model == "gemma4:e12b"

    def test_run_default_max_dim(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp"])
        assert args.max_dim == 1280

    def test_run_limit(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--limit", "20"])
        assert args.limit == 20

    def test_run_date_filters(self):
        args = build_parser().parse_args(
            ["run", "--input-dir", "/tmp", "--date-from", "2026-01-01", "--date-to", "2026-12-31"]
        )
        assert args.date_from == "2026-01-01"
        assert args.date_to == "2026-12-31"

    def test_run_single_date(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--date", "2026-04-01"])
        assert args.date == "2026-04-01"

    def test_run_dry_run_flag(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--dry-run"])
        assert args.dry_run is True

    def test_run_skip_no_gps(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--skip-no-gps"])
        assert args.skip_no_gps is True

    def test_run_extensions_default(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp"])
        assert args.extensions == "jpg,jpeg,heic,png"

    def test_run_extensions_custom(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--extensions", "jpg,png"])
        assert args.extensions == "jpg,png"

    def test_run_dedup_flag(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--dedup"])
        assert args.dedup is True

    def test_run_dedup_threshold(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--dedup-threshold", "3"])
        assert args.dedup_threshold == 3

    def test_run_db_flag(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--db", "/tmp/my.db"])
        assert args.db == "/tmp/my.db"

    def test_run_no_cache_flag(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--no-cache"])
        assert args.no_cache is True

    def test_run_no_recursive_flag(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--no-recursive"])
        assert args.no_recursive is True

    def test_run_recursive_by_default(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp"])
        assert args.no_recursive is False

    def test_run_output_flags(self):
        args = build_parser().parse_args(
            [
                "run",
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

    def test_run_verbose_short(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "-v"])
        assert args.verbose is True

    def test_run_help_exits_zero(self):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["run", "--help"])
        assert exc.value.code == 0

    def test_status_subcommand_parses(self):
        args = build_parser().parse_args(["status"])
        assert args.subcommand == "status"
        assert args.db is None

    def test_status_subcommand_with_db(self):
        args = build_parser().parse_args(["status", "--db", "/tmp/my.db"])
        assert args.subcommand == "status"
        assert args.db == "/tmp/my.db"

    def test_reprocess_subcommand_parses(self):
        args = build_parser().parse_args(["reprocess"])
        assert args.subcommand == "reprocess"
        assert args.db is None
        assert args.status is None

    def test_reprocess_subcommand_with_status(self):
        args = build_parser().parse_args(["reprocess", "--status", "error"])
        assert args.subcommand == "reprocess"
        assert args.status == "error"

    def test_preflight_subcommand_parses(self):
        args = build_parser().parse_args(["preflight"])
        assert args.subcommand == "preflight"

    def test_preflight_default_model(self):
        args = build_parser().parse_args(["preflight"])
        assert args.model == "gemma4:e4b"

    def test_preflight_custom_model(self):
        args = build_parser().parse_args(["preflight", "--model", "llava:latest"])
        assert args.model == "llava:latest"


class TestMainNoSource:
    def test_missing_dir_returns_error(self):
        result = main(["run", "--input-dir", "/nonexistent/path/12345"])
        assert result == 1


class TestStatusSubcommand:
    def test_status_empty_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        result = main(["status", "--db", db_path])
        assert result == 0

    def test_status_output_format(self, tmp_path, capsys):
        db_path = str(tmp_path / "test.db")
        result = main(["status", "--db", db_path])
        assert result == 0
        out = capsys.readouterr().out
        assert "Progress:" in out
        assert "ok:" in out
        assert "error:" in out
        assert "pending:" in out

    def test_status_shows_counts(self, tmp_path, capsys):
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB

        db_path = tmp_path / "test.db"
        img1 = tmp_path / "ok.jpg"
        img1.write_bytes(b"\x00" * 50)
        img2 = tmp_path / "err.jpg"
        img2.write_bytes(b"\x00" * 50)

        db = ProgressDB(db_path=db_path)
        ok_result = ImageResult(file_path=str(img1), file_name="ok.jpg", tags=["a"])
        db.mark_done(img1, ok_result)
        err_result = ImageResult(
            file_path=str(img2),
            file_name="err.jpg",
            processing_status="error",
            error_message="fail",
        )
        db.mark_done(img2, err_result)
        db.close()

        result = main(["status", "--db", str(db_path)])
        assert result == 0
        out = capsys.readouterr().out
        assert "1 / 2" in out


class TestReprocessSubcommand:
    def test_reprocess_empty_db(self, tmp_path, capsys):
        db_path = str(tmp_path / "test.db")
        result = main(["reprocess", "--db", db_path])
        assert result == 0
        out = capsys.readouterr().out
        assert "Reset 0 entries" in out

    def test_reprocess_all(self, tmp_path, capsys):
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB

        db_path = tmp_path / "test.db"
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\x00" * 50)

        db = ProgressDB(db_path=db_path)
        r = ImageResult(file_path=str(img), file_name="photo.jpg", tags=["tree"])
        db.mark_done(img, r)
        db.close()

        result = main(["reprocess", "--db", str(db_path)])
        assert result == 0
        out = capsys.readouterr().out
        assert "Reset 1 entries" in out

    def test_reprocess_by_status(self, tmp_path, capsys):
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB

        db_path = tmp_path / "test.db"
        img_ok = tmp_path / "ok.jpg"
        img_ok.write_bytes(b"\x00" * 50)
        img_err = tmp_path / "err.jpg"
        img_err.write_bytes(b"\x00" * 50)

        db = ProgressDB(db_path=db_path)
        ok_result = ImageResult(file_path=str(img_ok), file_name="ok.jpg", tags=["a"])
        db.mark_done(img_ok, ok_result)
        err_result = ImageResult(
            file_path=str(img_err),
            file_name="err.jpg",
            processing_status="error",
            error_message="fail",
        )
        db.mark_done(img_err, err_result)
        db.close()

        result = main(["reprocess", "--db", str(db_path), "--status", "error"])
        assert result == 0
        out = capsys.readouterr().out
        assert "Reset 1 entries" in out

        # ok entry should still be in DB
        db2 = ProgressDB(db_path=db_path)
        stats = db2.get_stats()
        db2.close()
        assert stats["ok"] == 1
        assert stats["error"] == 0


class TestCleanupSubcommand:
    def test_cleanup_empty_db_prints_no_candidates(self, tmp_path, capsys):
        db_path = str(tmp_path / "test.db")
        result = main(["cleanup", "--db", db_path])
        assert result == 0
        out = capsys.readouterr().out
        assert "No cleanup candidates found." in out

    def test_cleanup_subcommand_parses(self):
        args = build_parser().parse_args(["cleanup"])
        assert args.subcommand == "cleanup"
        assert args.include_review is False

    def test_cleanup_include_review_flag(self):
        args = build_parser().parse_args(["cleanup", "--include-review"])
        assert args.include_review is True

    def test_cleanup_db_flag(self):
        args = build_parser().parse_args(["cleanup", "--db", "/tmp/my.db"])
        assert args.db == "/tmp/my.db"

    def test_cleanup_shows_delete_candidates(self, tmp_path, capsys):
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB

        db_path = tmp_path / "test.db"
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\x00" * 10)

        db = ProgressDB(db_path=db_path)
        db.mark_done(
            img,
            ImageResult(
                file_path=str(img),
                file_name="photo.jpg",
                cleanup_class="delete",
                tags=["blur", "duplicate"],
            ),
        )
        db.close()

        result = main(["cleanup", "--db", str(db_path)])
        assert result == 0
        out = capsys.readouterr().out
        assert "Cleanup candidates" in out
        assert "[delete]" in out
        assert "photo.jpg" in out

    def test_cleanup_without_include_review_omits_review(self, tmp_path, capsys):
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB

        db_path = tmp_path / "test.db"
        img_rev = tmp_path / "review.jpg"
        img_rev.write_bytes(b"\x00" * 10)
        img_del = tmp_path / "delete.jpg"
        img_del.write_bytes(b"\x00" * 10)

        db = ProgressDB(db_path=db_path)
        db.mark_done(
            img_rev,
            ImageResult(
                file_path=str(img_rev),
                file_name="review.jpg",
                cleanup_class="review",
                tags=[],
            ),
        )
        db.mark_done(
            img_del,
            ImageResult(
                file_path=str(img_del),
                file_name="delete.jpg",
                cleanup_class="delete",
                tags=[],
            ),
        )
        db.close()

        result = main(["cleanup", "--db", str(db_path)])
        assert result == 0
        out = capsys.readouterr().out
        assert "[delete]" in out
        assert "[review]" not in out

    def test_cleanup_with_include_review_shows_both(self, tmp_path, capsys):
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB

        db_path = tmp_path / "test.db"
        img_rev = tmp_path / "review.jpg"
        img_rev.write_bytes(b"\x00" * 10)
        img_del = tmp_path / "delete.jpg"
        img_del.write_bytes(b"\x00" * 10)

        db = ProgressDB(db_path=db_path)
        db.mark_done(
            img_rev,
            ImageResult(
                file_path=str(img_rev),
                file_name="review.jpg",
                cleanup_class="review",
                tags=[],
            ),
        )
        db.mark_done(
            img_del,
            ImageResult(
                file_path=str(img_del),
                file_name="delete.jpg",
                cleanup_class="delete",
                tags=[],
            ),
        )
        db.close()

        result = main(["cleanup", "--db", str(db_path), "--include-review"])
        assert result == 0
        out = capsys.readouterr().out
        assert "[delete]" in out
        assert "[review]" in out
        assert "delete + review" in out
