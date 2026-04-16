"""Write description and keywords back to image EXIF metadata via exiftool.

Writes to all standard metadata fields for maximum compatibility across photo
managers: EXIF, IPTC, XMP, and Windows XP fields.  Preserves existing date
fields to prevent silent timestamp corruption.
"""

from __future__ import annotations

import json
import shutil
import subprocess

# Date tags that exiftool might silently update when writing other fields.
_DATE_TAGS = [
    "DateTimeOriginal",
    "CreateDate",
    "ModifyDate",
    "DateCreated",
    "DigitalCreationDate",
    "DigitalCreationTime",
    "TimeCreated",
]


def _read_date_fields(file_path: str) -> dict[str, str] | None:
    """Read existing date fields from the image so we can restore them after writing."""
    try:
        args = ["exiftool", "-json", "-n"] + [f"-{tag}" for tag in _DATE_TAGS] + [file_path]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=10)  # noqa: S603
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        if not data:
            return None
        # Return only fields that have actual values
        return {k: v for k, v in data[0].items() if k in _DATE_TAGS and v}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def write_exif_description(
    file_path: str,
    description: str | None = None,
    keywords: list[str] | None = None,
) -> str | None:
    """Write description and/or keywords to image EXIF using exiftool.

    Sets the following EXIF/IPTC/XMP/Windows fields for maximum compatibility:
    - ImageDescription, XMP:Description, IPTC:Caption-Abstract (description)
    - UserComment (description — visible in many viewers)
    - IPTC:Keywords, XMP:Subject, XPKeywords (keywords)

    Preserves all date fields to prevent silent timestamp corruption.

    Args:
        file_path: Path to the image file.
        description: Description text to write. Skipped when None.
        keywords: List of keyword strings. Skipped when None or empty.

    Returns:
        None on success, or an error message string on failure.
    """
    if description is None and not keywords:
        return None

    if not is_exiftool_available():
        return "exiftool is not available on this system"

    # Read date fields before writing so we can restore them
    saved_dates = _read_date_fields(file_path)

    args = ["exiftool", "-overwrite_original"]

    if description is not None:
        args.append(f"-ImageDescription={description}")
        args.append(f"-XMP:Description={description}")
        args.append(f"-IPTC:Caption-Abstract={description}")
        args.append(f"-UserComment={description}")

    if keywords:
        # Clear existing keywords first, then set new ones
        args.append("-IPTC:Keywords=")
        args.append("-XMP:Subject=")
        args.append("-XPKeywords=")
        for kw in keywords:
            args.append(f"-IPTC:Keywords={kw}")
            args.append(f"-XMP:Subject={kw}")
        # XPKeywords is a semicolon-separated single value
        args.append(f"-XPKeywords={';'.join(keywords)}")

    # Restore date fields to prevent silent timestamp changes
    if saved_dates:
        for tag, value in saved_dates.items():
            args.append(f"-{tag}={value}")

    args.append(file_path)

    try:
        proc = subprocess.run(  # noqa: S603
            args,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "exiftool timed out after 30 seconds"
    except OSError as exc:
        return f"Failed to launch exiftool: {exc}"

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        return (
            f"exiftool error (exit {proc.returncode}): {stderr}"
            if stderr
            else f"exiftool failed with exit code {proc.returncode}"
        )

    return None


def is_exiftool_available() -> bool:
    """Return True if exiftool is available on this system."""
    return shutil.which("exiftool") is not None
