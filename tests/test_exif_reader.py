"""Tests for EXIF reader — GPS parsing and date handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pyimgtag.exif_reader import (
    _dms_to_decimal,
    _exifread_dms_to_decimal,
    _exifread_gps,
    _parse_exif_date,
    _read_exifread,
)


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


class TestExifreadDmsToDecimal:
    def test_north_east(self):
        lat = _exifread_dms_to_decimal([37.0, 46.0, 30.0], "N")
        assert abs(lat - 37.775) < 0.001

    def test_south(self):
        lat = _exifread_dms_to_decimal([33.0, 51.0, 54.0], "S")
        assert lat < 0
        assert abs(lat - (-33.865)) < 0.001

    def test_west(self):
        lon = _exifread_dms_to_decimal([122.0, 25.0, 10.0], "W")
        assert lon < 0

    def test_ref_with_whitespace(self):
        lat = _exifread_dms_to_decimal([10.0, 0.0, 0.0], " S ")
        assert lat < 0


class TestExifreadGps:
    def test_valid_gps_tags(self):
        lat_tag = MagicMock()
        lat_tag.values = [48.0, 51.0, 29.0]
        lat_ref = MagicMock()
        lat_ref.__str__ = lambda _: "N"
        lon_tag = MagicMock()
        lon_tag.values = [2.0, 21.0, 7.0]
        lon_ref = MagicMock()
        lon_ref.__str__ = lambda _: "E"

        tags = {
            "GPS GPSLatitude": lat_tag,
            "GPS GPSLatitudeRef": lat_ref,
            "GPS GPSLongitude": lon_tag,
            "GPS GPSLongitudeRef": lon_ref,
        }
        lat, lon, has_gps = _exifread_gps(tags)
        assert has_gps
        assert lat is not None and lat > 48
        assert lon is not None and lon > 2

    def test_missing_gps_returns_false(self):
        lat, lon, has_gps = _exifread_gps({})
        assert not has_gps
        assert lat is None
        assert lon is None


class TestReadExifread:
    def test_returns_none_when_exifread_unavailable(self):
        from pathlib import Path

        with patch("pyimgtag.exif_reader.exifread", None):
            assert _read_exifread(Path("/fake/photo.jpg")) is None

    def test_returns_none_on_exception(self):
        from pathlib import Path

        with patch("pyimgtag.exif_reader.exifread") as mock_er:
            mock_er.process_file.side_effect = Exception("corrupt file")
            assert _read_exifread(Path("/fake/photo.jpg")) is None
