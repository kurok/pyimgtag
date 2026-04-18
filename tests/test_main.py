"""Tests for pyimgtag CLI argument parsing and subcommands."""

from __future__ import annotations

import contextlib

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

    def test_run_extensions_raw_example_in_help(self):
        """RAW extensions should appear somewhere reachable in the help output."""
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with contextlib.suppress(SystemExit):
            with redirect_stdout(buf):
                build_parser().parse_args(["run", "--help"])
        assert "cr2" in buf.getvalue().lower()

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

    def test_run_newest_first_flag(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--newest-first"])
        assert args.newest_first is True

    def test_run_newest_first_default_false(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp"])
        assert args.newest_first is False

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

    def test_run_sidecar_only_flag(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--sidecar-only"])
        assert args.sidecar_only is True

    def test_run_sidecar_only_default_false(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp"])
        assert args.sidecar_only is False

    def test_run_metadata_format_default(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp"])
        assert args.metadata_format == "auto"

    def test_run_metadata_format_xmp(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp", "--metadata-format", "xmp"])
        assert args.metadata_format == "xmp"

    def test_run_metadata_format_iptc(self):
        args = build_parser().parse_args(
            ["run", "--input-dir", "/tmp", "--metadata-format", "iptc"]
        )
        assert args.metadata_format == "iptc"

    def test_run_metadata_format_exif(self):
        args = build_parser().parse_args(
            ["run", "--input-dir", "/tmp", "--metadata-format", "exif"]
        )
        assert args.metadata_format == "exif"

    def test_run_metadata_format_invalid_rejected(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(
                ["run", "--input-dir", "/tmp", "--metadata-format", "badvalue"]
            )


class TestMainNoSource:
    def test_missing_dir_returns_error(self):
        result = main(["run", "--input-dir", "/nonexistent/path/12345"])
        assert result == 1

    def test_compute_dedup_map_empty(self, tmp_path):
        from pyimgtag.commands.run import _compute_dedup_map

        phash_map, skipped_dedup = _compute_dedup_map([], threshold=10)
        assert phash_map == {}
        assert skipped_dedup == set()


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
        assert "Reset 0 entries" in capsys.readouterr().err

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
        assert "Reset 1 entries" in capsys.readouterr().err

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
        assert "Reset 1 entries" in capsys.readouterr().err

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
        assert "No cleanup candidates found." in capsys.readouterr().err

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


class TestFacesBuildParser:
    def test_faces_subcommand_parses(self):
        args = build_parser().parse_args(["faces", "scan", "--input-dir", "/tmp/photos"])
        assert args.subcommand == "faces"
        assert args.faces_action == "scan"
        assert args.input_dir == "/tmp/photos"

    def test_faces_scan_defaults(self):
        args = build_parser().parse_args(["faces", "scan", "--input-dir", "/tmp"])
        assert args.max_dim == 1280
        assert args.detection_model == "hog"
        assert args.extensions == "jpg,jpeg,heic,png"
        assert args.limit is None
        assert args.db is None

    def test_faces_scan_custom_model(self):
        args = build_parser().parse_args(
            ["faces", "scan", "--input-dir", "/tmp", "--detection-model", "cnn"]
        )
        assert args.detection_model == "cnn"

    def test_faces_scan_photos_library(self):
        args = build_parser().parse_args(
            ["faces", "scan", "--photos-library", "/tmp/lib.photoslibrary"]
        )
        assert args.photos_library == "/tmp/lib.photoslibrary"

    def test_faces_scan_mutual_exclusion(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(
                ["faces", "scan", "--input-dir", "/a", "--photos-library", "/b"]
            )

    def test_faces_scan_requires_source(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["faces", "scan"])

    def test_faces_scan_limit(self):
        args = build_parser().parse_args(["faces", "scan", "--input-dir", "/tmp", "--limit", "10"])
        assert args.limit == 10

    def test_faces_cluster_defaults(self):
        args = build_parser().parse_args(["faces", "cluster"])
        assert args.faces_action == "cluster"
        assert args.eps == 0.5
        assert args.min_samples == 2

    def test_faces_cluster_custom_params(self):
        args = build_parser().parse_args(["faces", "cluster", "--eps", "0.3", "--min-samples", "5"])
        assert args.eps == 0.3
        assert args.min_samples == 5

    def test_faces_review_parses(self):
        args = build_parser().parse_args(["faces", "review"])
        assert args.faces_action == "review"

    def test_faces_apply_defaults(self):
        args = build_parser().parse_args(["faces", "apply"])
        assert args.faces_action == "apply"
        assert args.write_exif is False
        assert args.sidecar_only is False
        assert args.dry_run is False

    def test_faces_apply_write_exif(self):
        args = build_parser().parse_args(["faces", "apply", "--write-exif"])
        assert args.write_exif is True

    def test_faces_apply_sidecar_only(self):
        args = build_parser().parse_args(["faces", "apply", "--sidecar-only"])
        assert args.sidecar_only is True

    def test_faces_apply_dry_run(self):
        args = build_parser().parse_args(["faces", "apply", "--dry-run"])
        assert args.dry_run is True

    def test_faces_no_action_returns_1(self):
        result = main(["faces"])
        assert result == 1


class TestFacesReviewSubcommand:
    def test_review_empty_db(self, tmp_path, capsys):
        db_path = str(tmp_path / "test.db")
        result = main(["faces", "review", "--db", db_path])
        assert result == 0
        assert "No faces detected yet" in capsys.readouterr().err

    def test_review_with_persons(self, tmp_path, capsys):
        from pyimgtag.models import FaceDetection
        from pyimgtag.progress_db import ProgressDB

        db = ProgressDB(db_path=tmp_path / "test.db")
        det = FaceDetection(image_path="/img/a.jpg")
        f1 = db.insert_face("/img/a.jpg", det)
        f2 = db.insert_face("/img/b.jpg", det)
        pid = db.create_person(label="Alice", confirmed=True)
        db.set_person_id(f1, pid)
        db.set_person_id(f2, pid)
        # One unassigned face
        db.insert_face("/img/c.jpg", det)
        db.close()

        result = main(["faces", "review", "--db", str(tmp_path / "test.db")])
        assert result == 0
        err = capsys.readouterr().err
        assert "3 total" in err
        assert "2 assigned" in err
        assert "1 unassigned" in err
        assert "Alice" in err
        assert "[confirmed]" in err


class TestFacesClusterSubcommand:
    def test_cluster_empty_db(self, tmp_path, capsys):
        pytest.importorskip("sklearn", reason="scikit-learn not installed")
        db_path = str(tmp_path / "test.db")
        result = main(["faces", "cluster", "--db", db_path])
        assert result == 0
        assert "No clusters formed" in capsys.readouterr().err


class TestFacesApplySubcommand:
    def test_apply_no_persons(self, tmp_path, capsys):
        db_path = str(tmp_path / "test.db")
        result = main(["faces", "apply", "--db", db_path])
        assert result == 0
        assert "No persons found" in capsys.readouterr().err

    def test_apply_dry_run_shows_keywords(self, tmp_path, capsys):
        import numpy as np

        from pyimgtag.models import FaceDetection
        from pyimgtag.progress_db import ProgressDB

        db = ProgressDB(db_path=tmp_path / "test.db")
        det = FaceDetection(image_path="/img/a.jpg")
        f1 = db.insert_face("/img/a.jpg", det, embedding=np.ones(128))
        pid = db.create_person(label="Bob")
        db.set_person_id(f1, pid)
        db.close()

        result = main(["faces", "apply", "--db", str(tmp_path / "test.db"), "--dry-run"])
        assert result == 0
        err = capsys.readouterr().err
        assert "[dry-run]" in err
        assert "person:Bob" in err

    def test_apply_without_write_flag_lists_only(self, tmp_path, capsys):
        import numpy as np

        from pyimgtag.models import FaceDetection
        from pyimgtag.progress_db import ProgressDB

        db = ProgressDB(db_path=tmp_path / "test.db")
        det = FaceDetection(image_path="/img/a.jpg")
        f1 = db.insert_face("/img/a.jpg", det, embedding=np.ones(128))
        pid = db.create_person(label="Charlie")
        db.set_person_id(f1, pid)
        db.close()

        result = main(["faces", "apply", "--db", str(tmp_path / "test.db")])
        assert result == 0
        err = capsys.readouterr().err
        assert "person:Charlie" in err
        assert "Use --write-exif or --sidecar-only" in err


class TestQueryParserArgs:
    def test_query_parses(self):
        args = build_parser().parse_args(["query"])
        assert args.subcommand == "query"

    def test_query_tag_flag(self):
        args = build_parser().parse_args(["query", "--tag", "cat"])
        assert args.tag == "cat"

    def test_query_has_text_flag(self):
        args = build_parser().parse_args(["query", "--has-text"])
        assert args.has_text is True

    def test_query_no_text_flag(self):
        args = build_parser().parse_args(["query", "--no-text"])
        assert args.no_text is True

    def test_query_has_text_and_no_text_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["query", "--has-text", "--no-text"])

    def test_query_cleanup_flag(self):
        args = build_parser().parse_args(["query", "--cleanup", "delete"])
        assert args.cleanup == "delete"

    def test_query_city_flag(self):
        args = build_parser().parse_args(["query", "--city", "Berlin"])
        assert args.city == "Berlin"

    def test_query_country_flag(self):
        args = build_parser().parse_args(["query", "--country", "DE"])
        assert args.country == "DE"

    def test_query_format_default(self):
        args = build_parser().parse_args(["query"])
        assert args.format == "table"

    def test_query_format_json(self):
        args = build_parser().parse_args(["query", "--format", "json"])
        assert args.format == "json"

    def test_query_format_paths(self):
        args = build_parser().parse_args(["query", "--format", "paths"])
        assert args.format == "paths"

    def test_query_limit_flag(self):
        args = build_parser().parse_args(["query", "--limit", "20"])
        assert args.limit == 20

    def test_query_db_flag(self):
        args = build_parser().parse_args(["query", "--db", "/tmp/x.db"])
        assert args.db == "/tmp/x.db"

    def test_query_status_flag(self):
        args = build_parser().parse_args(["query", "--status", "error"])
        assert args.status == "error"


class TestJudgeParser:
    def test_judge_subcommand_exists(self):
        from pyimgtag.main import build_parser

        p = build_parser()
        args = p.parse_args(["judge", "--input-dir", "/tmp"])
        assert args.subcommand == "judge"

    def test_judge_default_sort_by_score(self):
        from pyimgtag.main import build_parser

        p = build_parser()
        args = p.parse_args(["judge", "--input-dir", "/tmp"])
        assert args.sort_by == "score"

    def test_judge_min_score_flag(self):
        from pyimgtag.main import build_parser

        p = build_parser()
        args = p.parse_args(["judge", "--input-dir", "/tmp", "--min-score", "3.5"])
        assert args.min_score == 3.5

    def test_judge_output_json_flag(self):
        from pyimgtag.main import build_parser

        p = build_parser()
        args = p.parse_args(["judge", "--input-dir", "/tmp", "--output-json", "out.json"])
        assert args.output_json == "out.json"

    def test_judge_limit_flag(self):
        from pyimgtag.main import build_parser

        p = build_parser()
        args = p.parse_args(["judge", "--input-dir", "/tmp", "--limit", "50"])
        assert args.limit == 50


class TestTagsParserArgs:
    def test_tags_list_parses(self):
        args = build_parser().parse_args(["tags", "list"])
        assert args.subcommand == "tags"
        assert args.tags_action == "list"

    def test_tags_rename_parses(self):
        args = build_parser().parse_args(["tags", "rename", "cat", "feline"])
        assert args.tags_action == "rename"
        assert args.old_tag == "cat"
        assert args.new_tag == "feline"

    def test_tags_rename_dry_run(self):
        args = build_parser().parse_args(["tags", "rename", "cat", "feline", "--dry-run"])
        assert args.dry_run is True

    def test_tags_delete_parses(self):
        args = build_parser().parse_args(["tags", "delete", "cat"])
        assert args.tags_action == "delete"
        assert args.tag == "cat"

    def test_tags_delete_dry_run(self):
        args = build_parser().parse_args(["tags", "delete", "cat", "--dry-run"])
        assert args.dry_run is True

    def test_tags_merge_parses(self):
        args = build_parser().parse_args(["tags", "merge", "cat", "animal"])
        assert args.tags_action == "merge"
        assert args.source_tag == "cat"
        assert args.target_tag == "animal"

    def test_tags_merge_dry_run(self):
        args = build_parser().parse_args(["tags", "merge", "cat", "animal", "--dry-run"])
        assert args.dry_run is True

    def test_tags_db_flag(self):
        args = build_parser().parse_args(["tags", "list", "--db", "/tmp/x.db"])
        assert args.db == "/tmp/x.db"


class TestQuerySubcommand:
    def _make_db(self, tmp_path):
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB

        db = ProgressDB(db_path=tmp_path / "test.db")
        for name, tags, city, country, cleanup in [
            ("a.jpg", ["cat", "indoor"], "Kyiv", "UA", None),
            ("b.jpg", ["dog", "outdoor"], "Berlin", "DE", "delete"),
        ]:
            img = tmp_path / name
            img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
            db.mark_done(
                img,
                ImageResult(
                    file_path=str(img),
                    file_name=name,
                    tags=tags,
                    nearest_city=city,
                    nearest_country=country,
                    cleanup_class=cleanup,
                ),
            )
        db.close()
        return str(tmp_path / "test.db")

    def test_query_empty_db_exits_zero(self, tmp_path, capsys):
        db_path = str(tmp_path / "empty.db")
        result = main(["query", "--db", db_path])
        assert result == 0

    def test_query_table_format(self, tmp_path, capsys):
        db_path = self._make_db(tmp_path)
        result = main(["query", "--db", db_path])
        assert result == 0
        captured = capsys.readouterr()
        assert "PATH" in captured.out
        assert "2 image(s) found" in captured.err

    def test_query_paths_format(self, tmp_path, capsys):
        db_path = self._make_db(tmp_path)
        result = main(["query", "--db", db_path, "--format", "paths"])
        assert result == 0
        out = capsys.readouterr().out
        lines = [ln for ln in out.strip().splitlines() if ln]
        assert len(lines) == 2

    def test_query_json_format(self, tmp_path, capsys):
        import json

        db_path = self._make_db(tmp_path)
        result = main(["query", "--db", db_path, "--format", "json"])
        assert result == 0
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 2

    def test_query_filter_by_tag(self, tmp_path, capsys):
        db_path = self._make_db(tmp_path)
        result = main(["query", "--db", db_path, "--tag", "cat"])
        assert result == 0
        assert "1 image(s) found" in capsys.readouterr().err

    def test_query_filter_by_cleanup(self, tmp_path, capsys):
        db_path = self._make_db(tmp_path)
        result = main(["query", "--db", db_path, "--cleanup", "delete"])
        assert result == 0
        assert "1 image(s) found" in capsys.readouterr().err


class TestTagsSubcommand:
    def _make_db(self, tmp_path):
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB

        db = ProgressDB(db_path=tmp_path / "test.db")
        for name, tags in [("a.jpg", ["cat", "indoor"]), ("b.jpg", ["cat", "outdoor"])]:
            img = tmp_path / name
            img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
            db.mark_done(img, ImageResult(file_path=str(img), file_name=name, tags=tags))
        db.close()
        return str(tmp_path / "test.db")

    def test_tags_list_exits_zero(self, tmp_path, capsys):
        db_path = self._make_db(tmp_path)
        result = main(["tags", "list", "--db", db_path])
        assert result == 0
        out = capsys.readouterr().out
        assert "cat" in out
        assert "2" in out

    def test_tags_list_empty_db(self, tmp_path, capsys):
        db_path = str(tmp_path / "empty.db")
        result = main(["tags", "list", "--db", db_path])
        assert result == 0
        assert "No tags" in capsys.readouterr().err

    def test_tags_rename_updates_db(self, tmp_path, capsys):
        db_path = self._make_db(tmp_path)
        result = main(["tags", "rename", "cat", "feline", "--db", db_path])
        assert result == 0
        assert "2 image(s)" in capsys.readouterr().err

    def test_tags_rename_dry_run_does_not_modify(self, tmp_path, capsys):
        from pyimgtag.progress_db import ProgressDB

        db_path = self._make_db(tmp_path)
        main(["tags", "rename", "cat", "feline", "--dry-run", "--db", db_path])
        assert "[dry-run]" in capsys.readouterr().err
        # DB should be unchanged
        db = ProgressDB(db_path=db_path)
        counts = dict(db.get_tag_counts())
        db.close()
        assert "cat" in counts

    def test_tags_delete_removes_tag(self, tmp_path, capsys):
        from pyimgtag.progress_db import ProgressDB

        db_path = self._make_db(tmp_path)
        result = main(["tags", "delete", "cat", "--db", db_path])
        assert result == 0
        db = ProgressDB(db_path=db_path)
        counts = dict(db.get_tag_counts())
        db.close()
        assert "cat" not in counts

    def test_tags_delete_dry_run_does_not_modify(self, tmp_path, capsys):
        from pyimgtag.progress_db import ProgressDB

        db_path = self._make_db(tmp_path)
        main(["tags", "delete", "cat", "--dry-run", "--db", db_path])
        assert "[dry-run]" in capsys.readouterr().err
        db = ProgressDB(db_path=db_path)
        counts = dict(db.get_tag_counts())
        db.close()
        assert "cat" in counts

    def test_tags_merge_updates_db(self, tmp_path, capsys):
        from pyimgtag.progress_db import ProgressDB

        db_path = self._make_db(tmp_path)
        result = main(["tags", "merge", "cat", "animal", "--db", db_path])
        assert result == 0
        db = ProgressDB(db_path=db_path)
        counts = dict(db.get_tag_counts())
        db.close()
        assert "cat" not in counts
        assert counts.get("animal", 0) == 2

    def test_tags_no_action_returns_error(self, tmp_path):
        result = main(["tags"])
        assert result == 1

    def test_tags_merge_dry_run_does_not_modify(self, tmp_path, capsys):
        from pyimgtag.progress_db import ProgressDB

        db_path = self._make_db(tmp_path)
        main(["tags", "merge", "cat", "animal", "--dry-run", "--db", db_path])
        assert "[dry-run]" in capsys.readouterr().err
        db = ProgressDB(db_path=db_path)
        counts = dict(db.get_tag_counts())
        db.close()
        assert "cat" in counts


class TestWriteBackMode:
    def test_run_write_back_mode_default_overwrite(self):
        args = build_parser().parse_args(["run", "--input-dir", "/tmp"])
        assert args.write_back_mode == "overwrite"

    def test_run_write_back_mode_append(self):
        args = build_parser().parse_args(
            ["run", "--input-dir", "/tmp", "--write-back-mode", "append"]
        )
        assert args.write_back_mode == "append"

    def test_run_write_back_mode_overwrite_explicit(self):
        args = build_parser().parse_args(
            ["run", "--input-dir", "/tmp", "--write-back-mode", "overwrite"]
        )
        assert args.write_back_mode == "overwrite"

    def test_run_write_back_mode_invalid(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(
                ["run", "--input-dir", "/tmp", "--write-back-mode", "invalid"]
            )

    def test_judge_write_back_mode_default_overwrite(self):
        args = build_parser().parse_args(["judge", "--input-dir", "/tmp"])
        assert args.write_back_mode == "overwrite"

    def test_judge_write_back_mode_append(self):
        args = build_parser().parse_args(
            ["judge", "--input-dir", "/tmp", "--write-back-mode", "append"]
        )
        assert args.write_back_mode == "append"

    def test_judge_write_back_flag(self):
        args = build_parser().parse_args(["judge", "--input-dir", "/tmp", "--write-back"])
        assert args.write_back is True

    def test_judge_write_back_default_false(self):
        args = build_parser().parse_args(["judge", "--input-dir", "/tmp"])
        assert args.write_back is False
