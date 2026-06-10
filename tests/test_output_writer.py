"""Tests for JSON and CSV output writers."""

from __future__ import annotations

import csv
import json
from unittest.mock import patch

import pytest

from pyimgtag.models import ImageResult
from pyimgtag.output_writer import result_to_jsonl, write_csv, write_json


def _sample() -> ImageResult:
    return ImageResult(
        file_path="/tmp/photo.jpg",
        file_name="photo.jpg",
        source_type="directory",
        is_local=True,
        image_date="2026-04-01 14:30:00",
        tags=["sunset", "beach"],
        scene_summary="sunset at beach",
        gps_lat=37.775,
        gps_lon=-122.419,
        nearest_city="San Francisco",
        nearest_region="California",
        nearest_country="United States",
        processing_status="ok",
    )


class TestWriteJson:
    def test_writes_valid_json(self, tmp_path):
        out = tmp_path / "out.json"
        write_json([_sample()], out)
        data = json.loads(out.read_text())
        assert len(data) == 1
        assert data[0]["tags"] == ["sunset", "beach"]
        assert data[0]["nearest_city"] == "San Francisco"

    def test_raises_oserror_on_write_failure(self, tmp_path):
        out = tmp_path / "out.json"
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="Failed to write JSON"):
                write_json([_sample()], out)

    def test_writes_non_ascii_as_utf8(self, tmp_path):
        """Output must be UTF-8 regardless of locale (cp1252 on Windows would
        otherwise raise UnicodeEncodeError on non-ASCII place names)."""
        out = tmp_path / "out.json"
        result = _sample()
        result.nearest_city = "Óbidos"
        write_json([result], out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data[0]["nearest_city"] == "Óbidos"
        assert "Óbidos".encode() in out.read_bytes()


class TestWriteCsv:
    def test_writes_csv_with_header(self, tmp_path):
        out = tmp_path / "out.csv"
        write_csv([_sample()], out)
        with open(out) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["file_name"] == "photo.jpg"
        assert rows[0]["tags"] == "sunset;beach"

    def test_raises_oserror_on_write_failure(self, tmp_path):
        out = tmp_path / "out.csv"
        with patch("builtins.open", side_effect=OSError("permission denied")):
            with pytest.raises(OSError, match="Failed to write CSV"):
                write_csv([_sample()], out)

    def test_writes_non_ascii_as_utf8(self, tmp_path):
        """Output must be UTF-8 regardless of locale (cp1252 on Windows would
        otherwise raise UnicodeEncodeError on non-ASCII place names)."""
        out = tmp_path / "out.csv"
        result = _sample()
        result.nearest_city = "Óbidos"
        write_csv([result], out)
        with open(out, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["nearest_city"] == "Óbidos"
        assert "Óbidos".encode() in out.read_bytes()


class TestJsonl:
    def test_single_line(self):
        line = result_to_jsonl(_sample())
        data = json.loads(line)
        assert data["file_name"] == "photo.jpg"
        assert "\n" not in line
