"""Tests for ``events detect --refresh-dates``: re-reading capture dates with no model.

Rows tagged before file times stopped standing in for capture dates carry the
day a file was copied. The refresh must re-read the date from the file, write
only ``image_date``, and never reach the model or the geocoder.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from pyimgtag.models import ExifData
from pyimgtag.progress_db import ProgressDB
from pyimgtag.video import VideoInfo, VideoToolError

#: The copy date a pre-#475 run stored for every file in the fixture.
_COPY_DATE = "2026-11-15 10:00:00"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any HTTP request (Ollama, Nominatim, a cloud backend) fails the test."""
    import requests

    def _refuse(*_a, **_k):
        raise AssertionError("the date refresh made a network call")

    monkeypatch.setattr(requests.sessions.Session, "request", _refuse)


def _insert(db: ProgressDB, path: Path, image_date: str | None, media_type: str = "image") -> None:
    db._conn.execute(
        """INSERT INTO processed_images
               (file_path, file_size, file_mtime, status, processed_at, tags,
                image_date, media_type)
           VALUES (?, 123, 456.0, 'ok', '2026-11-15', '[]', ?, ?)""",
        (str(path), image_date, media_type),
    )
    db._conn.commit()


def _row(db: ProgressDB, path: Path) -> tuple:
    return db._conn.execute(
        "SELECT image_date, file_size, file_mtime FROM processed_images WHERE file_path = ?",
        (str(path),),
    ).fetchone()


def _run(argv: list[str], db_path: Path) -> tuple[int, str, str]:
    from pyimgtag.main import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv + ["--db", str(db_path)])
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def _fake_exif(dates: dict[str, str | None]):
    def _read(path):
        return ExifData(date_original=dates.get(Path(path).name))

    return _read


def test_a_file_time_date_becomes_undated_end_to_end(tmp_path):
    """A real JPEG with no EXIF date, stored with its copy date, ends up unassigned."""
    shot = tmp_path / "Screenshot 2024-06-02.png"
    Image.new("RGB", (8, 8), "white").save(shot)
    db_path = tmp_path / "p.db"
    with ProgressDB(db_path=db_path) as db:
        _insert(db, shot, _COPY_DATE)

    code, _, err = _run(["events", "detect", "--refresh-dates"], db_path)
    assert code == 0
    assert "1 changed, 1 now undated" in err

    with ProgressDB(db_path=db_path) as db:
        # Only the date moved: the freshness columns still let `run` skip it.
        assert _row(db, shot) == (None, 123, 456.0)

    code, out, _ = _run(["events", "list", "--unassigned"], db_path)
    assert code == 0
    assert shot.name in out


def test_counts_changed_undated_and_unchanged(tmp_path, monkeypatch):
    names = ["real.jpg", "copied.jpg", "same.jpg", "respelled.jpg", "gained.jpg"]
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    monkeypatch.setattr(
        "pyimgtag.exif_reader.read_exif",
        _fake_exif(
            {
                "real.jpg": "2019-07-01 12:00:00",
                "copied.jpg": None,
                "same.jpg": "2020-01-01 08:00:00",
                "respelled.jpg": "2021-03-04 05:06:07",
                "gained.jpg": "2022-02-02 02:02:02",
            }
        ),
    )
    from pyimgtag.commands.events import refresh_capture_dates

    with ProgressDB(db_path=tmp_path / "p.db") as db:
        _insert(db, tmp_path / "real.jpg", _COPY_DATE)
        _insert(db, tmp_path / "copied.jpg", _COPY_DATE)
        _insert(db, tmp_path / "same.jpg", "2020-01-01 08:00:00")
        _insert(db, tmp_path / "respelled.jpg", "2021:03:04 05:06:07")
        _insert(db, tmp_path / "gained.jpg", None)

        counts = refresh_capture_dates(db)

        assert counts == {
            "checked": 5,
            "changed": 3,  # real, copied, gained -- not the respelled one
            "undated": 1,
            "missing": 0,
            "unreadable": 0,
        }
        assert _row(db, tmp_path / "real.jpg")[0] == "2019-07-01 12:00:00"
        assert _row(db, tmp_path / "copied.jpg")[0] is None
        # The same instant is rewritten in the current spelling.
        assert _row(db, tmp_path / "respelled.jpg")[0] == "2021-03-04 05:06:07"
        assert _row(db, tmp_path / "gained.jpg")[0] == "2022-02-02 02:02:02"


def test_a_missing_file_keeps_its_row(tmp_path, monkeypatch):
    monkeypatch.setattr("pyimgtag.exif_reader.read_exif", _fake_exif({}))
    from pyimgtag.commands.events import refresh_capture_dates

    gone = tmp_path / "gone.jpg"
    with ProgressDB(db_path=tmp_path / "p.db") as db:
        _insert(db, gone, _COPY_DATE)
        counts = refresh_capture_dates(db)
        assert counts["missing"] == 1
        assert counts["checked"] == 0
        assert _row(db, gone)[0] == _COPY_DATE


def test_a_clip_takes_its_container_date(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mov"
    clip.write_bytes(b"x")
    monkeypatch.setattr("pyimgtag.exif_reader.read_exif", _fake_exif({}))
    monkeypatch.setattr(
        "pyimgtag.video.probe", lambda _p: VideoInfo(creation_time="2023-05-06 07:08:09")
    )
    from pyimgtag.commands.events import refresh_capture_dates

    with ProgressDB(db_path=tmp_path / "p.db") as db:
        _insert(db, clip, _COPY_DATE, media_type="video")
        counts = refresh_capture_dates(db)
        assert counts["changed"] == 1
        assert _row(db, clip)[0] == "2023-05-06 07:08:09"


def test_a_still_never_probes_its_container(tmp_path, monkeypatch):
    still = tmp_path / "still.jpg"
    still.write_bytes(b"x")
    monkeypatch.setattr("pyimgtag.exif_reader.read_exif", _fake_exif({}))

    def _probe(_p):
        raise AssertionError("ffprobe ran for a still")

    monkeypatch.setattr("pyimgtag.video.probe", _probe)
    from pyimgtag.commands.events import refresh_capture_dates

    with ProgressDB(db_path=tmp_path / "p.db") as db:
        _insert(db, still, _COPY_DATE)
        assert refresh_capture_dates(db)["undated"] == 1


def test_an_unreadable_clip_keeps_its_stored_date(tmp_path, monkeypatch):
    """Without ffprobe the date is unknown, which is not the same as undated."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr("pyimgtag.exif_reader.read_exif", _fake_exif({}))

    def _probe(_p):
        raise VideoToolError("ffprobe not found")

    monkeypatch.setattr("pyimgtag.video.probe", _probe)
    from pyimgtag.commands.events import refresh_capture_dates

    with ProgressDB(db_path=tmp_path / "p.db") as db:
        _insert(db, clip, "2023-05-06 07:08:09", media_type="video")
        counts = refresh_capture_dates(db)
        assert counts["unreadable"] == 1
        assert _row(db, clip)[0] == "2023-05-06 07:08:09"


def test_detect_without_the_flag_does_not_read_files(tmp_path, monkeypatch):
    shot = tmp_path / "a.jpg"
    shot.write_bytes(b"x")

    def _read(_p):
        raise AssertionError("detect read a file without --refresh-dates")

    monkeypatch.setattr("pyimgtag.exif_reader.read_exif", _read)
    db_path = tmp_path / "p.db"
    with ProgressDB(db_path=db_path) as db:
        _insert(db, shot, _COPY_DATE)

    code, _, err = _run(["events", "detect"], db_path)
    assert code == 0
    assert "Re-read" not in err


def test_refresh_moves_photos_out_of_a_copy_date_event(tmp_path, monkeypatch):
    """Two screenshots dated by their copy day stop forming a November event."""
    names = ["s1.png", "s2.png"]
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    monkeypatch.setattr("pyimgtag.exif_reader.read_exif", _fake_exif({}))
    db_path = tmp_path / "p.db"
    with ProgressDB(db_path=db_path) as db:
        for name in names:
            _insert(db, tmp_path / name, _COPY_DATE)

    _run(["events", "detect"], db_path)
    _, out, _ = _run(["events", "list", "--format", "json"], db_path)
    assert len(json.loads(out)) == 1

    code, _, err = _run(["events", "detect", "--refresh-dates"], db_path)
    assert code == 0
    assert "2 now undated" in err
    with ProgressDB(db_path=db_path) as db:
        assert db.list_events() == []
    _, out, _ = _run(["events", "list", "--unassigned"], db_path)
    assert all(name in out for name in names)
