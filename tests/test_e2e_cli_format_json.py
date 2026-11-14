"""End to end: ``--format json`` from the real CLI is one JSON document on stdout (#404)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB
from tests.e2e_support import run_cli


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = tmp_path / "p.db"
    with ProgressDB(path) as progress:
        img = tmp_path / "a.jpg"
        img.write_bytes(b"\x00")
        progress.mark_done(
            img,
            ImageResult(
                file_path=str(img), file_name="a.jpg", tags=["beach"], cleanup_class="delete"
            ),
        )
        progress.create_person("Ann", confirmed=True)
    return str(path)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["status"], {"total": 1, "ok": 1, "error": 0, "pending": 0}),
        (["tags", "list"], [{"tag": "beach", "count": 1}]),
        (
            ["faces", "review"],
            [{"person_id": 1, "label": "Ann", "confirmed": True, "face_count": 0}],
        ),
    ],
    ids=["status", "tags-list", "faces-review"],
)
def test_json_output_is_the_whole_stdout(tmp_path: Path, db: str, argv, expected) -> None:
    result = run_cli(*argv, "--db", db, "--format", "json", home=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    assert result.stderr == ""


def test_cleanup_json(tmp_path: Path, db: str) -> None:
    result = run_cli("cleanup", "--db", db, "--format", "json", home=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    doc = json.loads(result.stdout)
    assert [c["file_path"] for c in doc["candidates"]] == [str(tmp_path / "a.jpg")]
    assert doc["candidates"][0]["tags"] == ["beach"]


def test_judge_accepts_short_v(tmp_path: Path) -> None:
    result = run_cli("judge", "--help", home=tmp_path)
    assert result.returncode == 0
    assert "-v, --verbose" in result.stdout
