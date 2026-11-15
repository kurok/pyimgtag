"""The ``[n/total]`` counter ends at its total under filters (#477)."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.main import build_parser
from pyimgtag.models import ExifData, TagResult

_COUNTER = re.compile(r"^\[(\d+)/(\d+)\]")


def _library(tmp_path: Path, count: int = 10) -> Path:
    lib = tmp_path / "lib"
    lib.mkdir()
    for i in range(count):
        (lib / f"img{i:02d}.jpg").write_bytes(b"x")
    return lib


def _exif_for(path: Path) -> ExifData:
    """img03 and img07 are from July 2024 and have GPS; the rest are June."""
    july = path.stem in ("img03", "img07")
    return ExifData(
        date_original="2024-07-10 12:00:00" if july else "2024-06-10 12:00:00",
        gps_lat=47.0 if july else None,
        gps_lon=8.0 if july else None,
        has_gps=july,
    )


def _run(tmp_path: Path, *extra: str) -> tuple[int, int, object]:
    from pyimgtag.commands.run import cmd_run

    args = build_parser().parse_args(
        [
            "run",
            "--input-dir",
            str(_library(tmp_path)),
            "--no-web",
            "--no-cache",
            "--db",
            str(tmp_path / "p.db"),
            *extra,
        ]
    )
    with (
        patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
        patch("pyimgtag.commands.run.check_ollama_model", return_value=(True, "")),
        patch("pyimgtag.commands.run.OllamaClient") as client_cls,
        patch("pyimgtag.commands.run.read_exif", side_effect=_exif_for) as read,
        patch("pyimgtag.commands.run.ReverseGeocoder"),
    ):
        client = MagicMock()
        client.tag_image.return_value = TagResult(tags=["x"], summary="")
        client_cls.return_value = client
        rc = cmd_run(args, MagicMock())
        reads = read.call_count
    return rc, reads, args


@pytest.fixture()
def counters(capsys):
    def _counters() -> tuple[list[tuple[int, int]], str]:
        err = capsys.readouterr().err
        found = [
            (int(m.group(1)), int(m.group(2)))
            for m in (_COUNTER.match(line) for line in err.splitlines())
            if m
        ]
        return found, err

    return _counters


@pytest.mark.parametrize("jobs", ["1", "2"])
def test_date_filter_counter_ends_at_its_total(tmp_path, counters, jobs):
    rc, _, _ = _run(tmp_path, "--date-from", "2024-07-01", "--date-to", "2024-07-31", "-j", jobs)

    found, err = counters()
    assert rc == 0
    assert found == [(1, 2), (2, 2)]
    assert "Skipped (date):    8" in err  # counted once, by the up-front pass


def test_skip_no_gps_counter_ends_at_its_total(tmp_path, counters):
    _run(tmp_path, "--skip-no-gps")

    found, err = counters()
    assert found == [(1, 2), (2, 2)]
    assert "Skipped (no GPS):  8" in err


def test_limit_caps_the_total(tmp_path, counters):
    _run(tmp_path, "--date-from", "2024-07-01", "--limit", "1")

    found, _ = counters()
    assert found == [(1, 1)]


def test_without_filters_the_total_is_the_scan(tmp_path, counters):
    _, reads, _ = _run(tmp_path)

    found, err = counters()
    assert found[-1] == (10, 10)
    assert "Reading capture dates" not in err
    assert reads == 10  # no up-front pass: one EXIF read per file, as before


def test_per_file_skips_shrink_the_total():
    from pyimgtag.commands.run import _new_stats, _progress_total

    stats = _new_stats(10)
    args = build_parser().parse_args(["run", "--input-dir", "."])
    assert _progress_total(stats, args) == 10
    stats["skipped_cached"] = 3
    stats["skipped_no_date"] = 2  # a subset of skipped_date, never subtracted twice
    assert _progress_total(stats, args) == 7


def test_judge_min_score_explains_the_gaps(tmp_path, capsys):
    from pyimgtag.commands.judge import cmd_judge
    from pyimgtag.models import JudgeScores

    sys_args = build_parser().parse_args(
        ["judge", "--input-dir", str(tmp_path), "--min-score", "8", "--no-web"]
    )
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    with (
        patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
        patch("pyimgtag.commands.judge.scan_directory", return_value=[img]),
        patch("pyimgtag.commands.judge.OllamaClient") as client_cls,
    ):
        client_cls.return_value.judge_image.return_value = JudgeScores(score=9, verdict="ok")
        assert cmd_judge(sys_args, MagicMock()) == 0

    assert "position among all 1 judged" in capsys.readouterr().err
