"""Tests for EXIF reader — GPS parsing and date handling."""

from __future__ import annotations

from pyimgtag.exif_reader import _dms_to_decimal, _parse_exif_date


class TestDmsToDecimal:
    def test_north_east(self):
        # 37 deg 46' 30" N  -> 37.775
        lat = _dms_to_decimal((37.0, 46.0, 30.0), "N")
        assert abs(lat - 37.775) < 0.001

    def test_south(self):
        lat = _dms_to_decimal((33.0, 51.0, 54.0), "S")
        assert lat < 0
        assert abs(lat - (-33.865)) < 0.001

    def test_west(self):
        lon = _dms_to_decimal((122.0, 25.0, 10.0), "W")
        assert lon < 0

    def test_zero(self):
        assert _dms_to_decimal((0.0, 0.0, 0.0), "N") == 0.0


class TestParseExifDate:
    def test_standard_exif(self):
        assert _parse_exif_date("2026:04:01 14:30:00") == "2026-04-01 14:30:00"

    def test_iso_format(self):
        assert _parse_exif_date("2026-04-01 14:30:00") == "2026-04-01 14:30:00"

    def test_iso_t_format(self):
        assert _parse_exif_date("2026-04-01T14:30:00") == "2026-04-01 14:30:00"

    def test_none(self):
        assert _parse_exif_date(None) is None

    def test_empty(self):
        assert _parse_exif_date("") is None

    def test_garbage(self):
        assert _parse_exif_date("not a date") is None
