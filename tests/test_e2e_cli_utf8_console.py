"""End to end: the CLI writes UTF-8 even when the console is cp1252 (#449).

Windows consoles default to a legacy code page; ``PYTHONIOENCODING=cp1252``
reproduces that on any OS. ``insights``, ``query`` and ``judge`` used to die
with UnicodeEncodeError on "→", "Île-de-France" or a Cyrillic file name.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from pyimgtag.main import force_utf8_console
from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB
from tests.e2e_support import fake_ollama, run_cli

_CP1252 = {"PYTHONIOENCODING": "cp1252"}


@pytest.fixture
def library(tmp_path: Path) -> tuple[Path, Path]:
    photos = tmp_path / "photos"
    photos.mkdir()
    photo = photos / "закат.jpg"
    Image.new("RGB", (32, 32), (200, 90, 30)).save(photo)
    db = tmp_path / "p.db"
    with ProgressDB(db) as progress:
        progress.mark_done(
            photo,
            ImageResult(
                file_path=str(photo),
                file_name=photo.name,
                tags=["sunset", "café"],
                image_date="2026-05-01T19:30:00",
                nearest_city="Paris",
                nearest_region="Île-de-France",
                nearest_country="France",
            ),
        )
    return photos, db


def _ok(result) -> str:
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "UnicodeEncodeError" not in output
    return output


def test_insights_prints_the_date_arrow(tmp_path: Path, library) -> None:
    _, db = library
    output = _ok(run_cli("insights", "--db", str(db), home=tmp_path, env=_CP1252))
    assert "→" in output


def test_query_prints_a_cyrillic_file_name(tmp_path: Path, library) -> None:
    _, db = library
    output = _ok(run_cli("query", "--db", str(db), "--tag", "sunset", home=tmp_path, env=_CP1252))
    assert "закат.jpg" in output


def test_text_cp1252_could_encode_is_written_as_utf8(tmp_path: Path, library) -> None:
    """The silent case: "é" is encodable in cp1252, so it used to come out as a
    cp1252 byte that a UTF-8 reader cannot decode, not as a crash."""
    _, db = library
    output = _ok(run_cli("tags", "list", "--db", str(db), home=tmp_path, env=_CP1252))
    assert "café" in output


def test_judge_prints_its_arrow(tmp_path: Path, library) -> None:
    photos, _ = library
    with fake_ollama(judge_reply=json.dumps({"score": 8, "reason": "warm"})) as url:
        result = run_cli(
            "judge", "--input-dir", str(photos), "--ollama-url", url, "--model", "gemma4",
            "--no-web", "--db", str(tmp_path / "j.db"), home=tmp_path, env=_CP1252,
        )  # fmt: skip
    output = _ok(result)
    assert "закат.jpg → 8/10" in output


@pytest.mark.parametrize(("encoding", "expected"), [("cp1252", "utf-8"), ("utf-8", "utf-8")])
def test_force_utf8_console_reconfigures_only_non_utf8_streams(monkeypatch, encoding, expected):
    out = io.TextIOWrapper(io.BytesIO(), encoding=encoding)
    err = io.TextIOWrapper(io.BytesIO(), encoding=encoding)
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    force_utf8_console()
    assert out.encoding == expected and err.encoding == expected
    print("→ Île-de-France закат")  # must not raise
    if encoding == "cp1252":
        assert out.errors == "replace"


def test_force_utf8_console_skips_streams_it_cannot_reconfigure(monkeypatch):
    plain = io.StringIO()
    monkeypatch.setattr("sys.stdout", plain)
    force_utf8_console()  # StringIO has no reconfigure(): left alone, no error
    assert plain.getvalue() == ""
