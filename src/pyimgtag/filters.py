"""CLI filter logic for date ranges and geographic bounding boxes."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from pyimgtag.models import ExifData

_YEAR_RE = re.compile(r"^\d{4}$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DAY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    """Parse ``"lat1,lon1,lat2,lon2"`` into a validated coordinate tuple.

    Latitudes must be within ±90 and longitudes within ±180. The corners may
    be given in any order; the *longitude* order is significant and preserved,
    because ``lon1 > lon2`` is how a box crossing the antimeridian is
    expressed (e.g. ``--bbox -20,170,-10,-170``).

    Raises:
        ValueError: If the value is not four comma-separated numbers within
            the valid coordinate ranges.
    """
    parts = [p.strip() for p in str(value).split(",")]
    if len(parts) != 4:
        raise ValueError(f"expected 4 comma-separated numbers 'lat1,lon1,lat2,lon2', got {value!r}")
    try:
        lat1, lon1, lat2, lon2 = (float(p) for p in parts)
    except ValueError:
        raise ValueError(f"bbox values must be numbers, got {value!r}") from None
    for name, lat in (("lat1", lat1), ("lat2", lat2)):
        if not -90.0 <= lat <= 90.0:
            raise ValueError(f"{name} must be between -90 and 90, got {lat}")
    for name, lon in (("lon1", lon1), ("lon2", lon2)):
        if not -180.0 <= lon <= 180.0:
            raise ValueError(f"{name} must be between -180 and 180, got {lon}")
    return lat1, lon1, lat2, lon2


def parse_year(value: str) -> str:
    """Validate a ``YYYY`` year and return it as an ``image_date`` prefix."""
    text = str(value).strip()
    if not _YEAR_RE.match(text):
        raise ValueError(f"expected a 4-digit year 'YYYY', got {value!r}")
    return text


def parse_month(value: str) -> str:
    """Validate a ``YYYY-MM`` month and return it as an ``image_date`` prefix."""
    text = str(value).strip()
    if not _MONTH_RE.match(text):
        raise ValueError(f"expected a month 'YYYY-MM', got {value!r}")
    return text


def parse_day(value: str) -> str:
    """Validate a ``YYYY-MM-DD`` day and return it as an ``image_date`` prefix."""
    text = str(value).strip()
    if not _DAY_RE.match(text):
        raise ValueError(f"expected a day 'YYYY-MM-DD', got {value!r}")
    return text


def parse_date(date_str: str) -> datetime:
    """Parse a ``YYYY-MM-DD`` string into a :class:`datetime`.

    Raises:
        ValueError: If date_str is not in YYYY-MM-DD format.
    """
    return datetime.strptime(date_str, "%Y-%m-%d")


def passes_date_filter(
    exif: ExifData,
    file_path: Path,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> bool:
    """Return *True* if the image date satisfies the active date filters."""
    if date is None and date_from is None and date_to is None:
        return True

    img_date = _resolve_date(exif, file_path)
    if img_date is None:
        return False

    if date is not None:
        return img_date.date() == parse_date(date).date()

    if date_from is not None and img_date.date() < parse_date(date_from).date():
        return False
    if date_to is not None and img_date.date() > parse_date(date_to).date():
        return False
    return True


def _resolve_date(exif: ExifData, file_path: Path) -> datetime | None:
    """EXIF original date first, file creation/modification date as fallback."""
    if exif.date_original:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(exif.date_original, fmt)
            except ValueError:
                continue
    try:
        stat = file_path.stat()
        ts = getattr(stat, "st_birthtime", None) or stat.st_mtime
        return datetime.fromtimestamp(ts)
    except OSError:
        return None
