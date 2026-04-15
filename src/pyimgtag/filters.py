"""CLI filter logic for date ranges, GPS presence, and limit."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pyimgtag.models import ExifData


def parse_date(date_str: str) -> datetime:
    """Parse a ``YYYY-MM-DD`` string into a :class:`datetime`."""
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
