"""A photo with no recorded capture date stays undated end to end (#475).

Its file creation/modification time is when it was copied, not when it was
taken. Using it put screenshots from 2024 into a "15 November 2026" event and
into ``query --year 2026``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from pyimgtag.main import build_parser, main
from pyimgtag.models import TagResult
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
