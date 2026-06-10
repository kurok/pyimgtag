"""Tests for EXIF reader — GPS parsing and date handling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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


class TestExifreadGpsError:
    """Conversion failure inside _exifread_gps returns no-GPS (lines 148-149)."""

    def test_bad_values_returns_false(self):
        lat_tag = MagicMock()
        lat_tag.values = ["not", "a", "number"]  # float() will raise ValueError
        lat_ref = MagicMock()
        lat_ref.__str__ = lambda _: "N"
        lon_tag = MagicMock()
        lon_tag.values = ["x", "y", "z"]
        lon_ref = MagicMock()
        lon_ref.__str__ = lambda _: "E"
        tags = {
            "GPS GPSLatitude": lat_tag,
            "GPS GPSLatitudeRef": lat_ref,
            "GPS GPSLongitude": lon_tag,
            "GPS GPSLongitudeRef": lon_ref,
        }
        lat, lon, has_gps = _exifread_gps(tags)
        assert not has_gps
        assert lat is None
        assert lon is None


class TestReadExifread:
    def test_returns_none_when_exifread_unavailable(self):
        with patch("pyimgtag.exif_reader.exifread", None):
            assert _read_exifread(Path("/fake/photo.jpg")) is None

    def test_empty_tags_returns_none(self, tmp_path: Path):
        """exifread returning an empty tag dict yields None (line 119)."""
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        with patch("pyimgtag.exif_reader.exifread") as mock_er:
            mock_er.process_file.return_value = {}
            assert _read_exifread(fake_img) is None

    def test_full_exifread_with_gps_and_date(self, tmp_path: Path):
        """Successful exifread read with GPS + date returns populated ExifData."""
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")

        lat_tag = MagicMock()
        lat_tag.values = [48.0, 51.0, 29.0]
        lat_ref = MagicMock()
        lat_ref.__str__ = lambda _: "N"
        lon_tag = MagicMock()
        lon_tag.values = [2.0, 21.0, 7.0]
        lon_ref = MagicMock()
        lon_ref.__str__ = lambda _: "E"
        date_tag = MagicMock()
        date_tag.__bool__ = lambda _: True
        date_tag.__str__ = lambda _: "2026:04:01 14:30:00"
        tags = {
            "GPS GPSLatitude": lat_tag,
            "GPS GPSLatitudeRef": lat_ref,
            "GPS GPSLongitude": lon_tag,
            "GPS GPSLongitudeRef": lon_ref,
            "EXIF DateTimeOriginal": date_tag,
        }
        with patch("pyimgtag.exif_reader.exifread") as mock_er:
            mock_er.process_file.return_value = tags
            result = _read_exifread(fake_img)
        assert result is not None
        assert result.has_gps
        assert result.gps_lat is not None and result.gps_lat > 48
        assert result.date_original == "2026-04-01 14:30:00"

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
        with (
            patch("pyimgtag.exif_reader.exifread") as mock_er,
            patch("pyimgtag.exif_reader._get_file_date", return_value="2026-01-01 00:00:00"),
        ):
            mock_er.process_file.return_value = tags
            result = _read_exifread(fake_img)
        assert result is not None
        # date parse fails ("not a date") → falls back to the file date
        assert result.date_original == "2026-01-01 00:00:00"


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


class TestReadExifBackendReturns:
    """Exercise the early-return branches in read_exif (lines 48, 51)."""

    def test_exiftool_result_short_circuits(self, tmp_path: Path):
        from pyimgtag.models import ExifData

        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        sentinel = ExifData(gps_lat=1.0, gps_lon=2.0, has_gps=True)
        with (
            patch("pyimgtag.exif_reader._read_exiftool", return_value=sentinel),
            patch("pyimgtag.exif_reader._read_exifread") as mock_er,
            patch("pyimgtag.exif_reader._read_pillow") as mock_pillow,
        ):
            result = read_exif(fake_img)
        assert result is sentinel
        mock_er.assert_not_called()
        mock_pillow.assert_not_called()

    def test_exifread_result_used_when_exiftool_none(self, tmp_path: Path):
        from pyimgtag.models import ExifData

        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        sentinel = ExifData(gps_lat=3.0, gps_lon=4.0, has_gps=True)
        with (
            patch("pyimgtag.exif_reader._read_exiftool", return_value=None),
            patch("pyimgtag.exif_reader._read_exifread", return_value=sentinel),
            patch("pyimgtag.exif_reader._read_pillow") as mock_pillow,
        ):
            result = read_exif(fake_img)
        assert result is sentinel
        mock_pillow.assert_not_called()


class TestReadExiftool:
    def test_success_returns_exifdata(self, tmp_path: Path):
        """exiftool happy path: GPS + date parsed into ExifData."""
        import json

        from pyimgtag.exif_reader import _read_exiftool

        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(
            [
                {
                    "GPSLatitude": 48.8566,
                    "GPSLongitude": 2.3522,
                    "DateTimeOriginal": "2026:04:01 14:30:00",
                }
            ]
        )
        with patch("pyimgtag.exif_reader.subprocess.run", return_value=mock_proc):
            result = _read_exiftool(fake_img)
        assert result is not None
        assert result.has_gps
        assert abs(result.gps_lat - 48.8566) < 1e-6
        assert result.date_original == "2026-04-01 14:30:00"

    def test_no_exif_date_falls_back_to_file_date(self, tmp_path: Path):
        """exiftool tier must apply the same file-date fallback as the
        exifread and Pillow tiers when the image has no EXIF date."""
        import json

        from pyimgtag.exif_reader import _read_exiftool

        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps([{"SourceFile": str(fake_img)}])
        with (
            patch("pyimgtag.exif_reader.subprocess.run", return_value=mock_proc),
            patch("pyimgtag.exif_reader._get_file_date", return_value="2026-01-01 00:00:00"),
        ):
            result = _read_exiftool(fake_img)
        assert result is not None
        assert result.date_original == "2026-01-01 00:00:00"

    def test_nonzero_returncode_returns_none(self, tmp_path: Path):
        """exiftool exit != 0 returns None (line 78)."""
        from pyimgtag.exif_reader import _read_exiftool

        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        with patch("pyimgtag.exif_reader.subprocess.run", return_value=mock_proc):
            result = _read_exiftool(fake_img)
        assert result is None

    def test_empty_json_array_returns_none(self, tmp_path: Path):
        """exiftool returning '[]' returns None (line 81)."""
        from pyimgtag.exif_reader import _read_exiftool

        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "[]"
        with patch("pyimgtag.exif_reader.subprocess.run", return_value=mock_proc):
            result = _read_exiftool(fake_img)
        assert result is None

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

    def test_non_numeric_gps_returns_none_not_crash(self, tmp_path: Path):
        """exiftool returning a non-numeric GPS value must return None, not raise ValueError."""
        import json

        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps([{"GPSLatitude": "N/A", "GPSLongitude": "W/A"}])
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


class TestReadPillowGpsMocked:
    """GPS extraction in _read_pillow without requiring piexif (lines 175-184)."""

    def _make_img(self, gps_ifd, exif_ifd):
        exif = MagicMock()
        exif.__bool__ = lambda _: True

        def get_ifd(tag):
            if tag == 0x8825:
                return gps_ifd
            if tag == 0x8769:
                return exif_ifd
            return {}

        exif.get_ifd.side_effect = get_ifd
        img = MagicMock()
        img.__enter__ = MagicMock(return_value=img)
        img.__exit__ = MagicMock(return_value=False)
        img.getexif.return_value = exif
        return img

    def test_extracts_gps_and_exif_date(self, tmp_path: Path):
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        gps_ifd = {1: "N", 2: (37.0, 46.0, 30.0), 3: "W", 4: (122.0, 25.0, 10.0)}
        exif_ifd = {36867: "2026:04:01 14:30:00"}
        img = self._make_img(gps_ifd, exif_ifd)
        with patch("pyimgtag.exif_reader.Image.open", return_value=img):
            result = _read_pillow(fake_img)
        assert result.has_gps
        assert result.gps_lat is not None and result.gps_lat > 37
        assert result.gps_lon is not None and result.gps_lon < -122
        assert result.date_original == "2026-04-01 14:30:00"

    def test_no_exif_date_falls_back_to_file_date(self, tmp_path: Path):
        """When the EXIF date IFD has no usable date, fall back to file date (lines 181-182)."""
        fake_img = tmp_path / "photo.jpg"
        fake_img.write_bytes(b"fake")
        gps_ifd = {1: "N", 2: (37.0, 46.0, 30.0), 3: "E", 4: (2.0, 21.0, 7.0)}
        exif_ifd = {}  # no date tags
        img = self._make_img(gps_ifd, exif_ifd)
        with (
            patch("pyimgtag.exif_reader.Image.open", return_value=img),
            patch("pyimgtag.exif_reader._get_file_date", return_value="2026-01-01 00:00:00"),
        ):
            result = _read_pillow(fake_img)
        assert result.has_gps
        assert result.date_original == "2026-01-01 00:00:00"


class TestParseGpsIfdConversionError:
    """_parse_gps_ifd swallows conversion errors (lines 202-203)."""

    def test_bad_dms_values_returns_false(self):
        # dms tuples whose elements cannot be coerced to float → ValueError
        gps_ifd = {1: "N", 2: ("a", "b", "c"), 3: "E", 4: ("d", "e", "f")}
        lat, lon, has_gps = _parse_gps_ifd(gps_ifd)
        assert not has_gps
        assert lat is None
        assert lon is None


class TestParsGpsIfd:
    def test_valid_north_east(self):
        gps_ifd = {
            1: "N",
            2: (48.0, 51.0, 29.0),
            3: "E",
            4: (2.0, 21.0, 7.0),
        }
        lat, lon, has_gps = _parse_gps_ifd(gps_ifd)
        assert has_gps
        assert lat is not None and lat > 48
        assert lon is not None and lon > 2

    def test_south_west_gives_negative(self):
        gps_ifd = {
            1: "S",
            2: (33.0, 51.0, 54.0),
            3: "W",
            4: (70.0, 0.0, 0.0),
        }
        lat, lon, has_gps = _parse_gps_ifd(gps_ifd)
        assert has_gps
        assert lat is not None and lat < 0
        assert lon is not None and lon < 0

    def test_empty_dict_returns_false(self):
        lat, lon, has_gps = _parse_gps_ifd({})
        assert not has_gps
        assert lat is None
        assert lon is None

    def test_missing_lat_dms_returns_false(self):
        gps_ifd = {1: "N", 3: "E", 4: (2.0, 0.0, 0.0)}
        lat, lon, has_gps = _parse_gps_ifd(gps_ifd)
        assert not has_gps


class TestReadPillowGpsPath:
    """Tests for _read_pillow GPS extraction (lines 166-175)."""

    def test_pillow_gps_path_extracts_lat_lon(self, tmp_path):
        """_read_pillow must parse GPS from the Pillow EXIF IFD when present."""

        piexif = pytest.importorskip("piexif")

        from PIL import Image

        from pyimgtag.exif_reader import _read_pillow

        # Build a minimal JPEG with GPS EXIF using piexif
        gps_ifd = {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((37, 1), (46, 1), (30, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"W",
            piexif.GPSIFD.GPSLongitude: ((122, 1), (25, 1), (10, 1)),
        }
        exif_dict = {"GPS": gps_ifd, "0th": {}, "Exif": {}}
        exif_bytes = piexif.dump(exif_dict)

        img_path = tmp_path / "gps.jpg"
        img = Image.new("RGB", (10, 10))
        img.save(str(img_path), exif=exif_bytes)

        result = _read_pillow(img_path)
        assert result.has_gps
        assert result.gps_lat is not None and result.gps_lat > 37
        assert result.gps_lon is not None and result.gps_lon < -122

    def test_read_pillow_via_fallback_chain(self, tmp_path):
        """GPS is returned when exiftool and exifread are both unavailable."""
        piexif = pytest.importorskip("piexif")

        from PIL import Image

        gps_ifd = {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((51, 1), (30, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((0, 1), (7, 1), (39, 1)),
        }
        exif_dict = {"GPS": gps_ifd, "0th": {}, "Exif": {}}
        exif_bytes = piexif.dump(exif_dict)

        img_path = tmp_path / "london.jpg"
        img = Image.new("RGB", (10, 10))
        img.save(str(img_path), exif=exif_bytes)

        with (
            patch("pyimgtag.exif_reader._read_exiftool", return_value=None),
            patch("pyimgtag.exif_reader._read_exifread", return_value=None),
        ):
            result = read_exif(img_path)

        assert result.has_gps
        assert result.gps_lat is not None and result.gps_lat > 51
