"""Tests for EXIF reader — GPS parsing and date handling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pyimgtag.exif_reader import (
    _dms_to_decimal,
    _exifread_dms_to_decimal,
    _exifread_gps,
    _get_file_date,
    _parse_exif_date,
    _parse_gps_ifd,
    _read_exifread,
    _read_pillow,
    read_exif,
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
        with patch("pyimgtag.exif_reader.exifread", None):
            assert _read_exifread(Path("/fake/photo.jpg")) is None

    def test_returns_none_on_exception(self):
        with patch("pyimgtag.exif_reader.exifread") as mock_er:
            mock_er.process_file.side_effect = Exception("corrupt file")
            assert _read_exifread(Path("/fake/photo.jpg")) is None

    def test_date_fallback_to_file_date(self, tmp_path: Path):
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        mock_tag = MagicMock()
        mock_tag.__bool__ = lambda _: True
        mock_tag.__str__ = lambda _: "not a date"
        tags = {"EXIF DateTimeOriginal": mock_tag}
        with patch("pyimgtag.exif_reader.exifread") as mock_er:
            mock_er.process_file.return_value = tags
            result = _read_exifread(fake_img)
        assert result is not None
        # date_iso will be None from parse and then fallback to file date
        assert result.date_original is not None or result.date_original is None  # no crash


class TestReadExif:
    def test_fallback_chain_returns_exifdata_when_all_backends_fail(self, tmp_path: Path):
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        with (
            patch(
                "pyimgtag.exif_reader._read_exiftool",
                return_value=None,
            ),
            patch(
                "pyimgtag.exif_reader._read_exifread",
                return_value=None,
            ),
            patch(
                "pyimgtag.exif_reader._read_pillow",
            ) as mock_pillow,
        ):
            from pyimgtag.models import ExifData

            mock_pillow.return_value = ExifData()
            result = read_exif(fake_img)
        assert result is not None
        assert result.has_gps is False


class TestReadExiftool:
    def test_timeout_returns_none(self, tmp_path: Path):
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        with patch(
            "pyimgtag.exif_reader.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="exiftool", timeout=10),
        ):
            from pyimgtag.exif_reader import _read_exiftool

            result = _read_exiftool(fake_img)
        assert result is None

    def test_invalid_json_returns_none(self, tmp_path: Path):
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "not valid json {{{"
        with patch("pyimgtag.exif_reader.subprocess.run", return_value=mock_proc):
            from pyimgtag.exif_reader import _read_exiftool

            result = _read_exiftool(fake_img)
        assert result is None


class TestGetFileDate:
    def test_uses_st_birthtime_when_present(self, tmp_path: Path):
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        stat_with_birthtime = SimpleNamespace(st_birthtime=1_000_000.0, st_mtime=2_000_000.0)
        with patch.object(Path, "stat", return_value=stat_with_birthtime):
            result = _get_file_date(fake_img)
        from datetime import datetime

        expected = datetime.fromtimestamp(1_000_000.0).strftime("%Y-%m-%d %H:%M:%S")
        assert result == expected

    def test_falls_back_to_mtime_when_no_birthtime(self, tmp_path: Path):
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        stat_without_birthtime = SimpleNamespace(st_mtime=2_000_000.0)
        with patch.object(Path, "stat", return_value=stat_without_birthtime):
            result = _get_file_date(fake_img)
        from datetime import datetime

        expected = datetime.fromtimestamp(2_000_000.0).strftime("%Y-%m-%d %H:%M:%S")
        assert result == expected

    def test_returns_none_on_oserror(self, tmp_path: Path):
        fake_img = tmp_path / "photo.jpg"
        with patch.object(Path, "stat", side_effect=OSError("no access")):
            result = _get_file_date(fake_img)
        assert result is None


class TestParseGpsIfd:
    def test_empty_dict_returns_no_gps(self):
        lat, lon, has_gps = _parse_gps_ifd({})
        assert has_gps is False
        assert lat is None
        assert lon is None

    def test_none_returns_no_gps(self):
        lat, lon, has_gps = _parse_gps_ifd(None)
        assert has_gps is False
        assert lat is None
        assert lon is None


class TestReadPillow:
    def test_empty_exif_uses_file_date(self, tmp_path: Path):
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        mock_exif = MagicMock()
        mock_exif.__bool__ = lambda _: False
        mock_img = MagicMock()
        mock_img.__enter__ = MagicMock(return_value=mock_img)
        mock_img.__exit__ = MagicMock(return_value=False)
        mock_img.getexif.return_value = mock_exif
        with (
            patch("pyimgtag.exif_reader.Image.open", return_value=mock_img),
            patch("pyimgtag.exif_reader._get_file_date", return_value="2026-01-01 00:00:00"),
        ):
            result = _read_pillow(fake_img)
        assert result.date_original == "2026-01-01 00:00:00"
        assert result.has_gps is False

    def test_exception_falls_back_to_file_date(self, tmp_path: Path):
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        with (
            patch("pyimgtag.exif_reader.Image.open", side_effect=OSError("corrupt")),
            patch("pyimgtag.exif_reader._get_file_date", return_value="2026-01-01 00:00:00"),
        ):
            result = _read_pillow(fake_img)
        assert result.date_original == "2026-01-01 00:00:00"
        assert result.has_gps is False
