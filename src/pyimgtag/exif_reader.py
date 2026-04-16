"""EXIF metadata reader with GPS and date extraction.

Uses exiftool subprocess as primary backend (handles JPEG + HEIC reliably on
macOS), exifread as a pure-Python middle tier (good for JPEG/TIFF/PNG without
system deps), and falls back to Pillow when neither is available.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

from PIL import Image

from pyimgtag.models import ExifData

try:
    import exifread
except ImportError:
    exifread = None  # type: ignore[assignment]

with contextlib.suppress(ImportError):
    import pillow_heif

    pillow_heif.register_heif_opener()


def read_exif(file_path: str | Path) -> ExifData:
    """Read EXIF GPS and date from an image file.

    Tries backends in order: exiftool → exifread → Pillow.
    """
    path = Path(file_path)
    result = _read_exiftool(path)
    if result is not None:
        return result
    result = _read_exifread(path)
    if result is not None:
        return result
    return _read_pillow(path)


# ---------------------------------------------------------------------------
# exiftool backend
# ---------------------------------------------------------------------------


def _read_exiftool(path: Path) -> ExifData | None:
    try:
        proc = subprocess.run(
            [
                "exiftool",
                "-json",
                "-n",
                "-GPSLatitude",
                "-GPSLongitude",
                "-DateTimeOriginal",
                "-CreateDate",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        if not data:
            return None
        info = data[0]

        lat = info.get("GPSLatitude")
        lon = info.get("GPSLongitude")
        date_str = info.get("DateTimeOriginal") or info.get("CreateDate")
        date_iso = _parse_exif_date(date_str) if date_str else None
        has_gps = lat is not None and lon is not None

        return ExifData(
            gps_lat=float(lat) if lat is not None else None,
            gps_lon=float(lon) if lon is not None else None,
            date_original=date_iso,
            has_gps=has_gps,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# exifread backend (pure Python, zero system deps)
# ---------------------------------------------------------------------------


def _read_exifread(path: Path) -> ExifData | None:
    """Read EXIF via exifread.  Returns None if exifread is not installed or fails."""
    if exifread is None:
        return None
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
        if not tags:
            return None

        lat, lon, has_gps = _exifread_gps(tags)

        date_str = None
        date_tag = tags.get("EXIF DateTimeOriginal") or tags.get("EXIF DateTimeDigitized")
        if date_tag:
            date_str = str(date_tag)
        date_iso = _parse_exif_date(date_str) if date_str else None
        if date_iso is None:
            date_iso = _get_file_date(path)

        return ExifData(gps_lat=lat, gps_lon=lon, date_original=date_iso, has_gps=has_gps)
    except Exception:
        return None


def _exifread_gps(tags: dict) -> tuple[float | None, float | None, bool]:
    """Extract GPS lat/lon from exifread tags."""
    lat_tag = tags.get("GPS GPSLatitude")
    lat_ref = tags.get("GPS GPSLatitudeRef")
    lon_tag = tags.get("GPS GPSLongitude")
    lon_ref = tags.get("GPS GPSLongitudeRef")
    if lat_tag is None or lon_tag is None:
        return None, None, False
    try:
        lat = _exifread_dms_to_decimal(lat_tag.values, str(lat_ref) if lat_ref else "N")
        lon = _exifread_dms_to_decimal(lon_tag.values, str(lon_ref) if lon_ref else "E")
        return lat, lon, True
    except (TypeError, ValueError, ZeroDivisionError, IndexError, AttributeError):
        return None, None, False


def _exifread_dms_to_decimal(values: list, ref: str) -> float:
    """Convert exifread DMS Ratio values to decimal degrees."""
    d = float(values[0])
    m = float(values[1])
    s = float(values[2])
    decimal = d + m / 60.0 + s / 3600.0
    if ref.strip().upper() in ("S", "W"):
        decimal = -decimal
    return round(decimal, 6)


# ---------------------------------------------------------------------------
# Pillow backend
# ---------------------------------------------------------------------------


def _read_pillow(path: Path) -> ExifData:
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return ExifData(date_original=_get_file_date(path))

            gps_ifd = exif.get_ifd(0x8825)
            lat, lon, has_gps = _parse_gps_ifd(gps_ifd)

            exif_ifd = exif.get_ifd(0x8769)
            date_str = exif_ifd.get(36867) or exif_ifd.get(36868)
            date_iso = _parse_exif_date(date_str) if date_str else None
            if date_iso is None:
                date_iso = _get_file_date(path)

            return ExifData(gps_lat=lat, gps_lon=lon, date_original=date_iso, has_gps=has_gps)
    except Exception:
        return ExifData(date_original=_get_file_date(path))


def _parse_gps_ifd(gps_ifd: dict) -> tuple[float | None, float | None, bool]:
    if not gps_ifd:
        return None, None, False
    lat_dms = gps_ifd.get(2)
    lat_ref = gps_ifd.get(1, "N")
    lon_dms = gps_ifd.get(4)
    lon_ref = gps_ifd.get(3, "E")
    if lat_dms is None or lon_dms is None:
        return None, None, False
    try:
        lat = _dms_to_decimal(lat_dms, lat_ref)
        lon = _dms_to_decimal(lon_dms, lon_ref)
        return lat, lon, True
    except (TypeError, ValueError, ZeroDivisionError):
        return None, None, False


def _dms_to_decimal(dms: tuple, ref: str) -> float:
    d, m, s = (float(x) for x in dms)
    decimal = d + m / 60.0 + s / 3600.0
    if ref in ("S", "W"):
        decimal = -decimal
    return round(decimal, 6)


# ---------------------------------------------------------------------------
# date helpers
# ---------------------------------------------------------------------------


def _parse_exif_date(date_str: str | None) -> str | None:
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _get_file_date(path: Path) -> str | None:
    try:
        stat = path.stat()
        ts = getattr(stat, "st_birthtime", None) or stat.st_mtime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return None
