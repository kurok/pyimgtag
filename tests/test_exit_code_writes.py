"""`run` exits 4 when it tagged but every requested write failed (#459)."""

from __future__ import annotations

import pytest

from pyimgtag.exit_codes import EXIT_ALL_MODEL_CALLS_FAILED, EXIT_ALL_WRITES_FAILED, run_exit_code


def _stats(**kw) -> dict[str, int]:
    base = dict(
        model_calls=2,
        model_failures=0,
        metadata_written=0,
        sidecars_written=0,
        written_back=0,
        metadata_write_failures=0,
        write_back_failures=0,
    )
    base.update(kw)
    return base


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({}, 0),  # no write asked for (or --dry-run): nothing attempted
        ({"metadata_write_failures": 2}, EXIT_ALL_WRITES_FAILED),
        ({"write_back_failures": 1}, EXIT_ALL_WRITES_FAILED),
        ({"metadata_write_failures": 1, "metadata_written": 1}, 0),  # partial: 0
        ({"metadata_write_failures": 1, "sidecars_written": 1}, 0),
        ({"write_back_failures": 1, "written_back": 1}, 0),
        ({"metadata_written": 2}, 0),
        # A dead model wins over the write result.
        ({"model_failures": 2, "metadata_write_failures": 2}, EXIT_ALL_MODEL_CALLS_FAILED),
        ({"model_calls": 0}, 0),
        # --resume-from-db: no model call, every write failed.
        ({"model_calls": 0, "metadata_write_failures": 2}, EXIT_ALL_WRITES_FAILED),
    ],
)
def test_run_exit_code(kw, expected):
    assert run_exit_code(_stats(**kw)) == expected


def test_stats_without_write_keys_still_work():
    assert run_exit_code({"model_calls": 1, "model_failures": 0}) == 0


def test_the_codes_are_distinct_and_documented():
    import pyimgtag.exit_codes as ec

    assert EXIT_ALL_WRITES_FAILED == 4 != EXIT_ALL_MODEL_CALLS_FAILED
    assert "| 4 |" in (ec.__doc__ or "")


# --- through cmd_run, independent of whether exiftool is installed -------------


def _cmd_run(tmp_path, write_results, *flags):
    from unittest.mock import MagicMock, patch

    from pyimgtag.commands.run import cmd_run
    from pyimgtag.main import build_parser
    from pyimgtag.models import TagResult

    photos = tmp_path / "photos"
    photos.mkdir()
    for i in range(len(write_results)):
        (photos / f"p{i}.jpg").write_bytes(b"\xff\xd8x")
    parser = build_parser()
    args = parser.parse_args(
        ["run", "--input-dir", str(photos), "--db", str(tmp_path / "p.db"), "--no-web", *flags]
    )
    with (
        patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
        patch("pyimgtag.commands.run.OllamaClient") as cls,
        patch("pyimgtag.commands.run._write_metadata", side_effect=write_results),
    ):
        client = MagicMock()
        client.tag_image.side_effect = lambda path, context=None: TagResult(
            tags=["beach"], summary="s"
        )
        client.prompt_builder = None
        cls.return_value = client
        return cmd_run(args, parser)


@pytest.mark.parametrize(
    ("write_results", "expected"),
    [
        ([None, None], EXIT_ALL_WRITES_FAILED),  # every write failed
        ([None, "exif"], 0),  # one of two succeeded
        (["sidecar", "exif"], 0),
    ],
)
def test_cmd_run_write_exif_exit_code(tmp_path, write_results, expected):
    assert _cmd_run(tmp_path, write_results, "--write-exif") == expected


def test_cmd_run_dry_run_is_never_4(tmp_path):
    assert _cmd_run(tmp_path, [None, None], "--write-exif", "--dry-run") == 0
