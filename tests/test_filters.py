"""Tests for date and GPS filter logic."""

from __future__ import annotations

from pyimgtag.filters import parse_date, passes_date_filter
from pyimgtag.models import ExifData


class TestParseDate:
    def test_valid(self):
        dt = parse_date("2026-04-01")
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 1

    def test_invalid(self):
        import pytest

        with pytest.raises(ValueError):
            parse_date("not-a-date")


class TestPassesDateFilter:
    def _exif(self, date: str | None = None, gps: bool = False) -> ExifData:
        return ExifData(date_original=date, has_gps=gps)

    def test_no_filters(self, tmp_path):
        f = tmp_path / "img.jpg"
        f.touch()
        assert passes_date_filter(self._exif(), f) is True

    def test_exact_date_match(self, tmp_path):
        f = tmp_path / "img.jpg"
        f.touch()
        assert passes_date_filter(self._exif("2026-04-01 14:00:00"), f, date="2026-04-01") is True

    def test_exact_date_no_match(self, tmp_path):
        f = tmp_path / "img.jpg"
        f.touch()
        assert passes_date_filter(self._exif("2026-04-02 14:00:00"), f, date="2026-04-01") is False

    def test_date_range_within(self, tmp_path):
        f = tmp_path / "img.jpg"
        f.touch()
        assert (
            passes_date_filter(
                self._exif("2026-04-15 10:00:00"),
                f,
                date_from="2026-04-01",
                date_to="2026-04-30",
            )
            is True
        )

    def test_date_range_before(self, tmp_path):
        f = tmp_path / "img.jpg"
        f.touch()
        assert (
            passes_date_filter(
                self._exif("2026-03-15 10:00:00"),
                f,
                date_from="2026-04-01",
                date_to="2026-04-30",
            )
            is False
        )

    def test_date_range_after(self, tmp_path):
        f = tmp_path / "img.jpg"
        f.touch()
        assert (
            passes_date_filter(
                self._exif("2026-05-15 10:00:00"),
                f,
                date_from="2026-04-01",
                date_to="2026-04-30",
            )
            is False
        )

    def test_no_exif_date_uses_file(self, tmp_path):
        f = tmp_path / "img.jpg"
        f.touch()
        # File date is today, so a wide range should match
        assert passes_date_filter(self._exif(), f, date_from="2020-01-01") is True

    def test_no_date_at_all(self, tmp_path):
        f = tmp_path / "img.jpg"
        # File doesn't exist -> no date -> filter fails
        assert passes_date_filter(self._exif(), f, date="2026-04-01") is False

    def test_invalid_exif_date_falls_back_to_file_mtime(self, tmp_path):
        # date_original is set but matches neither supported format —
        # both strptime attempts raise ValueError (lines 47-48 in filters.py)
        # and the function falls back to file mtime.
        f = tmp_path / "img.jpg"
        f.touch()
        exif = self._exif(date="not-a-valid-date-format")
        # File was just created, so a wide date_from well in the past should pass.
        assert passes_date_filter(exif, f, date_from="2000-01-01") is True
