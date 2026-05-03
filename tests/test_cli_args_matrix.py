"""Per-subcommand CLI argument matrix.

For every subcommand and every flag we expect the parser to expose, this
file pins:

- the flag's argparse default (so a silent default change is caught at
  PR time, not a user's first surprised support ticket)
- the flag's destination attribute name (so a rename is caught)
- that the flag accepts a representative value and stores it correctly

Plus a per-subcommand dispatch test that verifies ``main()`` routes the
parsed args to the expected handler with mocked dependencies, so the
``args.subcommand → handler`` table itself is exercised.

The tests intentionally avoid running any side effects (no DB writes,
no Ollama calls, no Photos library access) — they're cheap, fast, and
focused on the parser + dispatch surface.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from pyimgtag.main import build_parser, main


def _parse(*tokens: str) -> argparse.Namespace:
    return build_parser().parse_args(list(tokens))


# ---------------------------------------------------------------------------
# Defaults: every flag's default must match what the help text and docs
# advertise. Adding a flag is a contract change; updating this table is
# the explicit signal.
# ---------------------------------------------------------------------------


_RUN_DEFAULTS = [
    ("input_dir", None),
    ("photos_library", None),
    ("backend", "ollama"),
    ("model", None),
    ("ollama_url", "http://localhost:11434"),
    ("api_base", None),
    ("api_key", None),
    ("max_dim", 1280),
    ("timeout", 120),
    ("limit", None),
    ("date", None),
    ("date_from", None),
    ("date_to", None),
    ("extensions", "jpg,jpeg,heic,png"),
    ("skip_no_gps", False),
    ("dry_run", False),
    ("output_json", None),
    ("output_csv", None),
    ("jsonl_stdout", False),
    ("verbose", False),
    ("cache_dir", None),
    ("dedup", False),
    ("dedup_threshold", 5),
    ("db", None),
    ("no_cache", False),
    ("skip_if_tagged", False),
    ("resume_from_db", False),
    ("resume_threaded", False),
    ("write_back", False),
    ("write_back_mode", "overwrite"),
    ("write_exif", False),
    ("sidecar_only", False),
    ("metadata_format", "auto"),
    ("no_recursive", False),
    ("newest_first", False),
    # Web flags (added by add_web_flags)
    ("web", False),
    ("no_web", False),
    ("web_host", "127.0.0.1"),
    ("web_port", 8770),
    ("no_browser", False),
]


_JUDGE_DEFAULTS = [
    ("input_dir", None),
    ("photos_library", None),
    ("backend", "ollama"),
    ("model", None),
    ("ollama_url", "http://localhost:11434"),
    ("api_base", None),
    ("api_key", None),
    ("extensions", "jpg,jpeg,heic,png,tiff,webp"),
    ("limit", None),
    ("min_score", None),
    ("sort_by", "score"),
    ("output_json", None),
    ("verbose", False),
    ("no_recursive", False),
    ("write_back", False),
    ("write_back_mode", "overwrite"),
    ("db", None),
    ("max_dim", 1280),
    ("timeout", 120),
    ("skip_judged", False),
    # Web flags
    ("web", False),
    ("no_web", False),
    ("web_host", "127.0.0.1"),
    ("web_port", 8770),
    ("no_browser", False),
]


_QUERY_DEFAULTS = [
    ("db", None),
    ("tag", None),
    ("has_text", False),  # store_true; mutually exclusive with --no-text
    ("no_text", False),
    ("cleanup", None),
    ("scene_category", None),
    ("city", None),
    ("country", None),
    ("status", None),
    ("format", "table"),
    ("limit", None),
]


class TestRunDefaults:
    @pytest.mark.parametrize("attr,expected", _RUN_DEFAULTS)
    def test_default(self, attr: str, expected) -> None:
        # Provide a placeholder source so cmd_run's later validation doesn't
        # short-circuit during parser-only tests.
        args = _parse("run", "--input-dir", "/tmp")
        assert hasattr(args, attr), f"run is missing flag attr {attr!r}"
        if attr == "input_dir":
            # We supplied this — skip the default check on this single attr.
            return
        assert getattr(args, attr) == expected, attr


class TestJudgeDefaults:
    @pytest.mark.parametrize("attr,expected", _JUDGE_DEFAULTS)
    def test_default(self, attr: str, expected) -> None:
        args = _parse("judge", "--input-dir", "/tmp")
        assert hasattr(args, attr), f"judge is missing flag attr {attr!r}"
        if attr == "input_dir":
            return
        assert getattr(args, attr) == expected, attr


class TestQueryDefaults:
    @pytest.mark.parametrize("attr,expected", _QUERY_DEFAULTS)
    def test_default(self, attr: str, expected) -> None:
        args = _parse("query")
        assert hasattr(args, attr), f"query is missing flag attr {attr!r}"
        assert getattr(args, attr) == expected, attr


# ---------------------------------------------------------------------------
# Each flag actually stores a passed value on the right attribute. This
# catches typos and renames CLI-style.
# ---------------------------------------------------------------------------


_RUN_VALUE_CASES: list[tuple[str, str, object]] = [
    ("--backend", "anthropic", "anthropic"),
    ("--model", "gpt-4o", "gpt-4o"),
    ("--ollama-url", "http://gpu-host:11434", "http://gpu-host:11434"),
    ("--api-base", "https://gateway.local/v1", "https://gateway.local/v1"),
    ("--api-key", "sk-test", "sk-test"),
    ("--max-dim", "2048", 2048),
    ("--timeout", "300", 300),
    ("--limit", "10", 10),
    ("--date", "2026-04-01", "2026-04-01"),
    ("--date-from", "2026-04-01", "2026-04-01"),
    ("--date-to", "2026-04-30", "2026-04-30"),
    ("--extensions", "jpg,raf", "jpg,raf"),
    ("--cache-dir", "/tmp/cache", "/tmp/cache"),
    ("--dedup-threshold", "8", 8),
    ("--db", "/tmp/test.db", "/tmp/test.db"),
    ("--write-back-mode", "append", "append"),
    ("--metadata-format", "xmp", "xmp"),
    ("--web-host", "0.0.0.0", "0.0.0.0"),
    ("--web-port", "9999", 9999),
    ("--output-json", "out.json", "out.json"),
    ("--output-csv", "out.csv", "out.csv"),
]


_RUN_BOOL_FLAGS = [
    "--skip-no-gps",
    "--dry-run",
    "--jsonl-stdout",
    "--verbose",
    "--dedup",
    "--no-cache",
    "--skip-if-tagged",
    "--resume-from-db",
    "--resume-threaded",
    "--write-back",
    "--write-exif",
    "--sidecar-only",
    "--no-recursive",
    "--newest-first",
    "--web",
    "--no-web",
    "--no-browser",
]


def _flag_to_attr(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


class TestRunFlagValues:
    @pytest.mark.parametrize("flag,literal,expected", _RUN_VALUE_CASES)
    def test_run_flag_round_trip(self, flag: str, literal: str, expected) -> None:
        args = _parse("run", "--input-dir", "/tmp", flag, literal)
        assert getattr(args, _flag_to_attr(flag)) == expected

    @pytest.mark.parametrize("flag", _RUN_BOOL_FLAGS)
    def test_run_bool_flag_flips(self, flag: str) -> None:
        args = _parse("run", "--input-dir", "/tmp", flag)
        assert getattr(args, _flag_to_attr(flag)) is True

    def test_run_input_dir_xor_photos_library(self) -> None:
        # Mutually exclusive group rejects both at once.
        with pytest.raises(SystemExit):
            _parse(
                "run",
                "--input-dir",
                "/a",
                "--photos-library",
                "/b.photoslibrary",
            )

    def test_run_invalid_backend_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _parse("run", "--input-dir", "/tmp", "--backend", "bedrock")

    def test_run_invalid_write_back_mode_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _parse("run", "--input-dir", "/tmp", "--write-back-mode", "merge-deep")

    def test_run_invalid_metadata_format_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _parse("run", "--input-dir", "/tmp", "--metadata-format", "rdf")


_JUDGE_VALUE_CASES: list[tuple[str, str, object]] = [
    ("--backend", "openai", "openai"),
    ("--model", "claude-opus-4-7", "claude-opus-4-7"),
    ("--ollama-url", "http://gpu:11434", "http://gpu:11434"),
    ("--api-base", "https://gw.local", "https://gw.local"),
    ("--api-key", "sk-x", "sk-x"),
    ("--extensions", "jpg", "jpg"),
    ("--limit", "20", 20),
    ("--min-score", "8", 8.0),
    ("--sort-by", "name", "name"),
    ("--output-json", "scores.json", "scores.json"),
    ("--write-back-mode", "append", "append"),
    ("--db", "/tmp/j.db", "/tmp/j.db"),
    ("--max-dim", "1024", 1024),
    ("--timeout", "60", 60),
    ("--web-host", "192.168.1.1", "192.168.1.1"),
    ("--web-port", "9000", 9000),
]


_JUDGE_BOOL_FLAGS = [
    "--verbose",
    "--no-recursive",
    "--write-back",
    "--skip-judged",
    "--web",
    "--no-web",
    "--no-browser",
]


class TestJudgeFlagValues:
    @pytest.mark.parametrize("flag,literal,expected", _JUDGE_VALUE_CASES)
    def test_judge_flag_round_trip(self, flag: str, literal: str, expected) -> None:
        args = _parse("judge", "--input-dir", "/tmp", flag, literal)
        assert getattr(args, _flag_to_attr(flag)) == expected

    @pytest.mark.parametrize("flag", _JUDGE_BOOL_FLAGS)
    def test_judge_bool_flag_flips(self, flag: str) -> None:
        args = _parse("judge", "--input-dir", "/tmp", flag)
        assert getattr(args, _flag_to_attr(flag)) is True

    def test_judge_invalid_sort_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _parse("judge", "--input-dir", "/tmp", "--sort-by", "random")

    def test_judge_invalid_backend_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _parse("judge", "--input-dir", "/tmp", "--backend", "perplexity")


# ---------------------------------------------------------------------------
# `faces` sub-actions: each accepts the right keys and rejects unknown ones.
# ---------------------------------------------------------------------------


class TestFacesSubcommands:
    def test_scan_accepts_db_extensions_max_dim_model_limit(self) -> None:
        args = _parse(
            "faces",
            "scan",
            "--input-dir",
            "/tmp",
            "--db",
            "/tmp/f.db",
            "--extensions",
            "jpg,heic",
            "--max-dim",
            "1024",
            "--detection-model",
            "cnn",
            "--limit",
            "100",
        )
        assert args.faces_action == "scan"
        assert args.db == "/tmp/f.db"
        assert args.extensions == "jpg,heic"
        assert args.max_dim == 1024
        assert args.detection_model == "cnn"
        assert args.limit == 100

    def test_scan_input_dir_xor_photos_library(self) -> None:
        with pytest.raises(SystemExit):
            _parse(
                "faces",
                "scan",
                "--input-dir",
                "/a",
                "--photos-library",
                "/b.photoslibrary",
            )

    def test_cluster_eps_min_samples(self) -> None:
        args = _parse("faces", "cluster", "--db", "/tmp/f.db", "--eps", "0.4", "--min-samples", "3")
        assert args.faces_action == "cluster"
        assert args.eps == 0.4
        assert args.min_samples == 3

    def test_review_only_takes_db(self) -> None:
        args = _parse("faces", "review", "--db", "/tmp/f.db")
        assert args.faces_action == "review"
        assert args.db == "/tmp/f.db"

    def test_apply_dry_run_and_metadata_format(self) -> None:
        args = _parse(
            "faces",
            "apply",
            "--db",
            "/tmp/f.db",
            "--write-exif",
            "--dry-run",
        )
        assert args.faces_action == "apply"
        assert args.write_exif is True
        assert args.dry_run is True

    def test_import_photos_only_takes_db(self) -> None:
        args = _parse("faces", "import-photos", "--db", "/tmp/f.db")
        assert args.faces_action == "import-photos"
        assert args.db == "/tmp/f.db"

    def test_ui_host_port_defaults(self) -> None:
        args = _parse("faces", "ui", "--db", "/tmp/f.db")
        assert args.faces_action == "ui"
        assert args.host == "127.0.0.1"
        assert args.port == 8766

    def test_ui_host_port_override(self) -> None:
        args = _parse("faces", "ui", "--db", "/tmp/f.db", "--host", "0.0.0.0", "--port", "9001")
        assert args.host == "0.0.0.0"
        assert args.port == 9001

    def test_unknown_sub_action_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _parse("faces", "merge")  # nope


# ---------------------------------------------------------------------------
# `tags` sub-actions: positional args + optional --dry-run / --db.
# ---------------------------------------------------------------------------


class TestTagsSubcommands:
    def test_list_takes_only_db(self) -> None:
        args = _parse("tags", "list", "--db", "/tmp/p.db")
        assert args.tags_action == "list"
        assert args.db == "/tmp/p.db"

    def test_rename_takes_two_positionals(self) -> None:
        args = _parse("tags", "rename", "old", "new", "--dry-run", "--db", "/tmp/p.db")
        assert args.tags_action == "rename"
        assert args.old_tag == "old"
        assert args.new_tag == "new"
        assert args.dry_run is True
        assert args.db == "/tmp/p.db"

    def test_rename_requires_two_positionals(self) -> None:
        with pytest.raises(SystemExit):
            _parse("tags", "rename", "old")

    def test_delete_takes_one_positional(self) -> None:
        args = _parse("tags", "delete", "stale-tag", "--dry-run")
        assert args.tags_action == "delete"
        assert args.tag == "stale-tag"
        assert args.dry_run is True

    def test_merge_takes_two_positionals(self) -> None:
        args = _parse("tags", "merge", "src", "tgt", "--db", "/tmp/p.db")
        assert args.tags_action == "merge"
        assert args.source_tag == "src"
        assert args.target_tag == "tgt"


# ---------------------------------------------------------------------------
# Smaller subcommands: status, reprocess, cleanup, preflight, review.
# ---------------------------------------------------------------------------


class TestSmallSubcommands:
    def test_status(self) -> None:
        args = _parse("status", "--db", "/tmp/p.db")
        assert args.subcommand == "status"
        assert args.db == "/tmp/p.db"

    def test_reprocess_status_filter(self) -> None:
        args = _parse("reprocess", "--db", "/tmp/p.db", "--status", "error")
        assert args.subcommand == "reprocess"
        assert args.status == "error"

    def test_reprocess_no_status_defaults_to_all(self) -> None:
        args = _parse("reprocess", "--db", "/tmp/p.db")
        assert args.status is None

    def test_cleanup_include_review(self) -> None:
        args = _parse("cleanup", "--db", "/tmp/p.db", "--include-review")
        assert args.subcommand == "cleanup"
        assert args.include_review is True

    def test_cleanup_default_excludes_review(self) -> None:
        args = _parse("cleanup", "--db", "/tmp/p.db")
        assert args.include_review is False

    def test_preflight_pure_form(self) -> None:
        args = _parse("preflight")
        assert args.subcommand == "preflight"
        assert args.ollama_url == "http://localhost:11434"
        assert args.model == "gemma4:e4b"
        assert args.input_dir is None
        assert args.photos_library is None

    def test_preflight_with_source(self) -> None:
        args = _parse("preflight", "--input-dir", "/tmp")
        assert args.input_dir == "/tmp"

    def test_preflight_input_dir_xor_photos_library(self) -> None:
        with pytest.raises(SystemExit):
            _parse(
                "preflight",
                "--input-dir",
                "/a",
                "--photos-library",
                "/b.photoslibrary",
            )

    def test_review_defaults(self) -> None:
        args = _parse("review")
        assert args.subcommand == "review"
        assert args.host == "127.0.0.1"
        assert args.port == 8765
        assert args.no_browser is False

    def test_review_no_browser(self) -> None:
        args = _parse("review", "--no-browser")
        assert args.no_browser is True


# ---------------------------------------------------------------------------
# Query: text filter is a mutually-exclusive group; status uses choices.
# ---------------------------------------------------------------------------


class TestQueryFlagSemantics:
    def test_status_choice_validated(self) -> None:
        with pytest.raises(SystemExit):
            _parse("query", "--status", "warning")

    def test_has_text_xor_no_text(self) -> None:
        # Both can't be passed at once.
        with pytest.raises(SystemExit):
            _parse("query", "--has-text", "--no-text")

    def test_format_choices_validated(self) -> None:
        # Whitelist of output formats; reject unknown.
        with pytest.raises(SystemExit):
            _parse("query", "--format", "yaml")
        for fmt in ("table", "json", "paths"):
            args = _parse("query", "--format", fmt)
            assert args.format == fmt


# ---------------------------------------------------------------------------
# Top-level dispatch: main() routes the parsed args to the expected
# handler. Mocked side effects mean the body of the handler doesn't
# matter — only that the right one was selected and its return code
# propagates back.
# ---------------------------------------------------------------------------


_DISPATCH_TABLE = [
    ("run", "pyimgtag.commands.run.cmd_run", ["run", "--input-dir", "/tmp"]),
    ("status", "pyimgtag.commands.db.cmd_status", ["status"]),
    ("reprocess", "pyimgtag.commands.db.cmd_reprocess", ["reprocess"]),
    ("preflight", "pyimgtag.commands.preflight_cmd.cmd_preflight", ["preflight"]),
    ("cleanup", "pyimgtag.commands.db.cmd_cleanup", ["cleanup"]),
    ("review", "pyimgtag.commands.review_cmd.cmd_review", ["review"]),
    ("query", "pyimgtag.commands.query.cmd_query", ["query"]),
    ("tags", "pyimgtag.commands.tags.cmd_tags", ["tags", "list"]),
    ("faces", "pyimgtag.commands.faces.cmd_faces", ["faces", "review"]),
    ("judge", "pyimgtag.commands.judge.cmd_judge", ["judge", "--input-dir", "/tmp"]),
]


class TestDispatchRoutes:
    """Each subcommand routes to its declared handler exactly once."""

    @pytest.mark.parametrize("name,target,argv", _DISPATCH_TABLE)
    def test_dispatch(self, name: str, target: str, argv: list[str]) -> None:
        # PyPI version-check makes a network call on every invocation; turn
        # it off so the matrix doesn't rely on connectivity.
        with (
            patch.dict("os.environ", {"PYIMGTAG_NO_UPDATE_CHECK": "1"}),
            patch(target, return_value=0) as handler,
        ):
            rc = main(argv)
        assert rc == 0
        assert handler.call_count == 1, f"{name}: expected single dispatch"


class TestNoSubcommandPrintsHelp:
    def test_no_subcommand_returns_1(self) -> None:
        with patch.dict("os.environ", {"PYIMGTAG_NO_UPDATE_CHECK": "1"}):
            assert main([]) == 1


class TestJudgeAlwaysOpensProgressDb:
    """Regression: ``main()`` used to skip opening ``progress_db`` for the
    ``judge`` subcommand whenever ``--db`` was omitted, so every score
    was silently dropped before reaching the database. ``cmd_judge``
    must now always receive a real DB instance."""

    def test_judge_without_db_flag_still_gets_db(self, tmp_path) -> None:
        captured: dict = {}

        def _record(_args, db_arg):
            captured["db"] = db_arg
            return 0

        # Override HOME so the default ``~/.cache/pyimgtag/progress.db``
        # path resolves under tmp_path instead of the user's real cache.
        env = {
            "PYIMGTAG_NO_UPDATE_CHECK": "1",
            "HOME": str(tmp_path),
            "PYIMGTAG_NO_WEB": "1",  # don't try to start the web server
        }
        with (
            patch.dict("os.environ", env, clear=False),
            patch("pyimgtag.commands.judge.cmd_judge", side_effect=_record),
        ):
            rc = main(["judge", "--input-dir", str(tmp_path)])

        assert rc == 0
        # Must be a ProgressDB instance, not None.
        from pyimgtag.progress_db import ProgressDB

        assert isinstance(captured["db"], ProgressDB), (
            "cmd_judge received None — main() failed to open the default DB"
        )
