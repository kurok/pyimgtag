"""EXIF metadata reader with GPS and date extraction.

Uses exiftool subprocess as primary backend (handles JPEG + HEIC reliably on
macOS) and falls back to Pillow when exiftool is not installed.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from PIL import Image

from pyimgtag.models import ExifData

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass


def read_exif(file_path: str | Path) -> ExifData:
    """Read EXIF GPS and date from an image file."""
    path = Path(file_path)
    result = _read_exiftool(path)
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
