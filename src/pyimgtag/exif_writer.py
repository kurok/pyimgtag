"""Write description and keywords back to image EXIF metadata via exiftool.

Writes to all standard metadata fields for maximum compatibility across photo
managers: EXIF, IPTC, XMP, and Windows XP fields.  Preserves existing date
fields to prevent silent timestamp corruption.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

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

# File extensions that support direct in-file metadata writes via exiftool.
SUPPORTED_DIRECT_WRITE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".heic",
        ".png",
        ".tiff",
        ".tif",
        ".dng",
    }
)

# RAW formats that must always use an XMP sidecar — never in-file writes.
# These formats have proprietary binary structures; exiftool can read them
# but writing metadata directly risks corruption.  DNG is excluded because
# it is a standardised, exiftool-safe format (it's in SUPPORTED_DIRECT_WRITE_EXTENSIONS).
RAW_SIDECAR_ONLY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".cr2",
        ".cr3",
        ".nef",
        ".nrw",
        ".arw",
        ".sr2",
        ".srf",
        ".raf",
        ".orf",
        ".rw2",
        ".pef",
        ".3fr",
        ".fff",
        ".rwl",
    }
)


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
    *,
    fmt: str = "auto",
    merge: bool = False,
) -> str | None:
    """Write description and/or keywords to image EXIF using exiftool.

    Sets metadata fields according to the chosen format for maximum
    compatibility across photo managers.  Preserves all date fields to
    prevent silent timestamp corruption.

    Args:
        file_path: Path to the image file.
        description: Description text to write. Skipped when None.
        keywords: List of keyword strings. Skipped when None or empty.
        fmt: Metadata standard to write. One of ``"auto"``, ``"xmp"``,
            ``"iptc"``, or ``"exif"``. ``"auto"`` writes all compatible
            fields (default).
        merge: When True, existing keywords are preserved and new keywords
            are added alongside them. When False (default), existing
            keywords are cleared before writing.

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

    _write_xmp = fmt in ("auto", "xmp")
    _write_iptc = fmt in ("auto", "iptc")
    _write_exif_fields = fmt in ("auto", "exif")

    if description is not None:
        if _write_exif_fields:
            args.append(f"-ImageDescription={description}")
            args.append(f"-UserComment={description}")
        if _write_xmp:
            args.append(f"-XMP:Description={description}")
        if _write_iptc:
            args.append(f"-IPTC:Caption-Abstract={description}")

    if keywords:
        if _write_iptc:
            if not merge:
                args.append("-IPTC:Keywords=")
            for kw in keywords:
                args.append(f"-IPTC:Keywords={kw}")
        if _write_xmp:
            if not merge:
                args.append("-XMP:Subject=")
            for kw in keywords:
                args.append(f"-XMP:Subject={kw}")
        if _write_exif_fields:
            if not merge:
                args.append("-XPKeywords=")
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


def write_xmp_sidecar(
    file_path: str,
    description: str | None = None,
    keywords: list[str] | None = None,
) -> str | None:
    """Write description and keywords to an XMP sidecar file.

    Creates or updates a ``.xmp`` companion file at the same path as
    *file_path*.  The original image is never modified.

    When creating a new sidecar the source image is used as input so that
    any existing metadata is preserved in the sidecar alongside the new
    AI-generated fields.  When updating an existing sidecar the file is
    modified in-place.

    Args:
        file_path: Path to the source image file.
        description: Description text to write. Skipped when None.
        keywords: List of keyword strings. Skipped when None or empty.

    Returns:
        None on success, or an error message string on failure.
    """
    if description is None and not keywords:
        return None

    if not is_exiftool_available():
        return "exiftool is not available on this system"

    sidecar_path = Path(file_path).with_suffix(".xmp")

    args = ["exiftool"]

    if description is not None:
        args.append(f"-XMP:Description={description}")

    if keywords:
        # Clear existing Subject tags then set new ones for idempotency
        args.append("-XMP:Subject=")
        for kw in keywords:
            args.append(f"-XMP:Subject={kw}")

    if sidecar_path.exists():
        # Update existing sidecar in-place
        args += ["-overwrite_original", str(sidecar_path)]
    else:
        # Create new sidecar from source file (preserves other source metadata)
        args += ["-o", str(sidecar_path), file_path]

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


def read_existing_metadata(file_path: str) -> dict[str, object]:
    """Read current description and keywords from an image or its XMP sidecar.

    Checks for a ``.xmp`` sidecar first; falls back to the image file itself.

    Args:
        file_path: Path to the image file.

    Returns:
        Dict with keys ``"description"`` (``str | None``) and
        ``"keywords"`` (``list[str]``).  Returns empty values on any error.
    """
    sidecar = Path(file_path).with_suffix(".xmp")
    target = str(sidecar) if sidecar.exists() else file_path

    try:
        proc = subprocess.run(  # noqa: S603
            [
                "exiftool",
                "-json",
                "-Description",
                "-ImageDescription",
                "-Keywords",
                "-Subject",
                target,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return {"description": None, "keywords": []}
        data = json.loads(proc.stdout)
        if not data:
            return {"description": None, "keywords": []}
        record = data[0]
        desc: str | None = record.get("Description") or record.get("ImageDescription") or None
        kws = record.get("Keywords") or record.get("Subject") or []
        if isinstance(kws, str):
            kws = [kws]
        return {"description": desc, "keywords": list(kws)}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return {"description": None, "keywords": []}


def diff_metadata(
    file_path: str,
    description: str | None = None,
    keywords: list[str] | None = None,
) -> list[str]:
    """Return human-readable lines describing pending metadata changes.

    Compares proposed *description* and *keywords* against what is currently
    stored in the image or its XMP sidecar.

    Args:
        file_path: Path to the image file.
        description: Proposed description text.
        keywords: Proposed keyword list.

    Returns:
        List of change-description strings.  Empty when no changes are
        detected or nothing is proposed.
    """
    if not is_exiftool_available():
        return ["(exiftool unavailable — cannot compute diff)"]

    existing = read_existing_metadata(file_path)
    changes: list[str] = []

    if description is not None:
        curr: str = existing.get("description") or ""  # type: ignore[assignment]
        if curr != description:
            curr_repr = f'"{curr[:60]}"' if curr else "(empty)"
            new_repr = f'"{description[:60]}"'
            changes.append(f"  description: {curr_repr} -> {new_repr}")

    if keywords:
        curr_kws: list[str] = existing.get("keywords") or []  # type: ignore[assignment]
        new_set = set(keywords)
        old_set = set(curr_kws)
        added = sorted(new_set - old_set)
        removed = sorted(old_set - new_set)
        if added:
            changes.append(f"  keywords add:    {', '.join(added)}")
        if removed:
            changes.append(f"  keywords remove: {', '.join(removed)}")

    return changes


def is_exiftool_available() -> bool:
    """Return True if exiftool is available on this system."""
    return shutil.which("exiftool") is not None
