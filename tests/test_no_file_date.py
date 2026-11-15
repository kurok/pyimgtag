"""A photo with no recorded capture date stays undated end to end (#475).

Its file creation/modification time is when it was copied, not when it was
taken. Using it put screenshots from 2024 into a "15 November 2026" event and
into ``query --year 2026``.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from pyimgtag.exif_reader import _parse_exif_date, _read_exiftool
from pyimgtag.main import build_parser, main
from pyimgtag.models import ImageResult, TagResult
from pyimgtag.progress_db import ProgressDB


def _library(tmp_path: Path) -> Path:
    lib = tmp_path / "lib"
    lib.mkdir()
    shot = lib / "Screenshot 2024-06-14 at 10.32.11.png"
    Image.new("RGB", (8, 8)).save(shot)
    now = time.time()
    os.utime(shot, (now, now))  # copied today
    return lib


def _run(tmp_path: Path, lib: Path, *extra: str) -> tuple[int, str]:
    from pyimgtag.commands.run import cmd_run

    db = tmp_path / "progress.db"
    args = build_parser().parse_args(
        ["run", "--input-dir", str(lib), "--no-web", "--db", str(db), *extra]
    )
    with (
        patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
        patch("pyimgtag.commands.run.check_ollama_model", return_value=(True, "")),
        patch("pyimgtag.commands.run.OllamaClient") as client_cls,
        # No exiftool: the pure-Python tiers decide, the same as on a bare install.
        patch("pyimgtag.exif_reader.exiftool.run", side_effect=FileNotFoundError),
    ):
        client = MagicMock()
        client.tag_image.return_value = TagResult(tags=["screenshot"], summary="")
        client_cls.return_value = client
        return cmd_run(args, MagicMock()), str(db)


def test_undated_photo_is_stored_without_a_date(tmp_path, capsys):
    rc, db = _run(tmp_path, _library(tmp_path))

    assert rc == 0
    with ProgressDB(db_path=db) as conn:
        rows = conn.query_images()
        assert [r["image_date"] for r in rows] == [None]
        assert conn.query_images(date_prefix=time.strftime("%Y")) == []


def test_date_filter_skips_it_and_says_it_had_no_date(tmp_path, capsys):
    rc, _ = _run(tmp_path, _library(tmp_path), "--date-from", "2000-01-01")

    assert rc == 0
    err = capsys.readouterr().err
    assert "Skipped (date):    1" in err
    assert "  without a date:  1" in err


def test_no_date_row_only_under_a_date_filter(tmp_path, capsys):
    _run(tmp_path, _library(tmp_path))

    assert "without a date" not in capsys.readouterr().err


def test_events_leave_it_unassigned(tmp_path, capsys):
    _, db = _run(tmp_path, _library(tmp_path))
    capsys.readouterr()

    assert main(["events", "detect", "--db", db]) == 0
    assert main(["events", "list", "--db", db, "--unassigned"]) == 0

    out = capsys.readouterr().out
    assert "Screenshot 2024-06-14 at 10.32.11.png" in out


# --- recorded dates in the forms real files carry ----------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2024:06:14 10:32:11", "2024-06-14 10:32:11"),
        ("2024-06-14T10:32:11", "2024-06-14 10:32:11"),
        ("2024:06:14 10:32:11+02:00", "2024-06-14 10:32:11"),
        ("2024:06:14 10:32:11.123", "2024-06-14 10:32:11"),
        ("2024-06-14T10:32:11.5Z", "2024-06-14 10:32:11"),
        ("2024:06:14", "2024-06-14 00:00:00"),
        ("0000:00:00 00:00:00", None),
        ("2024:13:01 00:00:00", None),
        ("not a date", None),
    ],
)
def test_recorded_date_forms(raw, expected):
    assert _parse_exif_date(raw) == expected


def test_exiftool_falls_back_to_xmp_date_created(tmp_path):
    shot = tmp_path / "export.png"
    shot.write_bytes(b"x")
    proc = MagicMock(returncode=0, stdout=json.dumps([{"DateCreated": "2024:06:14"}]))
    with patch("pyimgtag.exif_reader.exiftool.run", return_value=proc):
        result = _read_exiftool(shot)
    assert result is not None
    assert result.date_original == "2024-06-14 00:00:00"


def test_exiftool_prefers_date_time_original(tmp_path):
    shot = tmp_path / "a.jpg"
    shot.write_bytes(b"x")
    info = {"DateTimeOriginal": "2023:01:02 03:04:05", "DateCreated": "2024:06:14"}
    proc = MagicMock(returncode=0, stdout=json.dumps([info]))
    with patch("pyimgtag.exif_reader.exiftool.run", return_value=proc):
        result = _read_exiftool(shot)
    assert result is not None
    assert result.date_original == "2023-01-02 03:04:05"


# --- clips: the container date, normalized like EXIF -------------------------


def test_apple_local_creation_date_keeps_its_wall_clock():
    from pyimgtag.video import capture_time

    assert capture_time("2024-06-14T10:32:11+0200", "2024-06-14T08:32:11Z") == (
        "2024-06-14 10:32:11"
    )


def test_utc_creation_time_becomes_local_naive():
    from pyimgtag.video import capture_time

    expected = (
        datetime(2024, 6, 14, 8, 32, 11, tzinfo=timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
    )
    assert capture_time(None, "2024-06-14T08:32:11.000000Z") == expected
    assert capture_time(None, None) is None
    assert capture_time("garbage", None) is None


def test_a_clip_without_exif_takes_its_container_date_before_filtering(tmp_path):
    from pyimgtag.commands.run import _read_capture_metadata
    from pyimgtag.filters import passes_date_filter
    from pyimgtag.models import ExifData
    from pyimgtag.video import VideoInfo

    clip = tmp_path / "clip.mov"
    clip.write_bytes(b"x")
    args = build_parser().parse_args(
        ["run", "--input-dir", str(tmp_path), "--include-video", "--date-from", "2024-01-01"]
    )
    with (
        patch("pyimgtag.commands.run.read_exif", return_value=ExifData()),
        patch("pyimgtag.video.probe", return_value=VideoInfo(creation_time="2024-06-14 10:32:11")),
    ):
        exif = _read_capture_metadata(clip, args)

    assert exif.date_original == "2024-06-14 10:32:11"
    assert passes_date_filter(exif, clip, date_from="2024-01-01") is True


def test_a_still_never_calls_ffprobe(tmp_path):
    from pyimgtag.commands.run import _read_capture_metadata
    from pyimgtag.models import ExifData

    shot = tmp_path / "a.png"
    shot.write_bytes(b"x")
    args = build_parser().parse_args(["run", "--input-dir", str(tmp_path), "--include-video"])
    with (
        patch("pyimgtag.commands.run.read_exif", return_value=ExifData()),
        patch("pyimgtag.video.probe", side_effect=AssertionError("not a clip")),
    ):
        assert _read_capture_metadata(shot, args).date_original is None


def test_events_take_a_zoned_date_next_to_naive_ones(tmp_path, capsys):
    """An older clip row may hold ffprobe's raw UTC string; events must not crash."""
    from pyimgtag.commands.events import _parse_date

    zoned = _parse_date("2024-06-14T08:32:11.000000Z")
    naive = _parse_date("2024:06:14 10:00:00")
    assert zoned is not None and zoned.tzinfo is None
    assert naive is not None
    sorted([zoned, naive])  # comparable

    db_path = tmp_path / "mixed.db"
    with ProgressDB(db_path=db_path) as db:
        for name, date in (("a.jpg", "2024:06:14 10:00:00"), ("b.mov", "2024-06-14T08:32:11Z")):
            img = tmp_path / name
            img.write_bytes(b"x")
            db.mark_done(
                img, ImageResult(file_path=str(img), file_name=name, tags=["x"], image_date=date)
            )
    assert main(["events", "detect", "--db", str(db_path)]) == 0
