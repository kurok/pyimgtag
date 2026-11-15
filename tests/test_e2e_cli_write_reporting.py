"""End to end: `pyimgtag run` reports what it wrote (#438).

Real CLI against a fake Ollama. Whether exiftool is installed decides whether a
write can succeed, so the expectation follows the machine: either the file is
counted and marked, or the failure is counted - never silence.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image

from tests.e2e_support import fake_ollama, run_cli

_TAGS = json.dumps(
    {
        "tags": ["beach", "sunset"],
        "summary": "A beach.",
        "scene_category": "outdoor_leisure",
        "emotional_tone": "positive",
        "cleanup_class": "keep",
        "has_text": False,
        "text_summary": None,
        "event_hint": "outing",
        "significance": "low",
    }
)


def _run(tmp_path: Path, *flags: str):
    photos = tmp_path / "photos"
    photos.mkdir(exist_ok=True)
    for i in range(2):
        Image.new("RGB", (32, 32), (40 * i, 90, 160)).save(photos / f"p{i}.jpg")
    with fake_ollama(tag_reply=_TAGS) as url:
        return run_cli(
            "run", "--input-dir", str(photos), "--ollama-url", url, "--model", "gemma4",
            "--no-web", "--db", str(tmp_path / "p.db"), *flags, home=tmp_path,
        )  # fmt: skip


def test_write_exif_counts_every_file(tmp_path: Path) -> None:
    result = _run(tmp_path, "--write-exif")
    assert result.returncode == 0, result.stderr
    if shutil.which("exiftool"):
        assert "  Metadata written: 2" in result.stderr
        assert result.stderr.count("| exif") == 2
    else:
        assert "  Metadata written: 0" in result.stderr
        assert "  Write failures:   2" in result.stderr


def test_dry_run_reports_zero_written(tmp_path: Path) -> None:
    result = _run(tmp_path, "--write-exif", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "  Metadata written: 0" in result.stderr
    assert "  Write failures:   0" in result.stderr


def test_without_write_flags_the_summary_is_unchanged(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Metadata written" not in result.stderr
    assert "| exif" not in result.stderr
