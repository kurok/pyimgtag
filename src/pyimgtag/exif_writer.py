"""Write description and keywords back to image EXIF metadata via exiftool.

Writes to all standard metadata fields for maximum compatibility across photo
managers: EXIF, IPTC, XMP, and Windows XP fields.  Preserves existing date
fields to prevent silent timestamp corruption.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
import tempfile
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


def _run_exiftool(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    """Invoke exiftool with *args*, handing the arguments over in a UTF-8 file.

    ``args[0]`` is the executable; everything after it is what exiftool would
    otherwise have been given on the command line.

    Values do not go on the command line any more. On Windows the C runtime
    converts the command line to the active code page before Perl reads argv,
    and every character that code page cannot represent becomes a literal
    ``?`` -- in the written file, unrecoverably. A geocoded ``\u00d3bidos``
    was stored as ``?bidos``. An argument file is read as UTF-8 while
    ``-charset UTF8`` is in force, so the bytes arrive intact on every
    platform, and file paths travel the same way for the same reason.

    One argument per line is literal in an argument file, so a value containing
    a newline -- a model-written description, typically -- would be read as a
    second argument and then as a filename, leaving the tag truncated while
    exiftool still reports files updated. Those values are spilled to their own
    file and passed as ``-TAG<=FILE``, which exiftool reads whole.

    stdout and stderr come back decoded as UTF-8 rather than by locale, for the
    same reason the input is written as UTF-8.
    """
    with tempfile.TemporaryDirectory(prefix="pyimgtag-exiftool-") as tmpdir:
        tmp = Path(tmpdir)
        lines: list[str] = []

        for index, arg in enumerate(args[1:]):
            tag, sep, value = arg.partition("=")
            # Only a plain assignment can be spilled: '+=' and '-=' are list
            # operators and '<=' is already the file form, so rewriting any of
            # them would change what the argument means.
            if sep and "\n" in value and not tag.endswith(("+", "-", "<")):
                value_file = tmp / f"value{index}"
                value_file.write_text(value, encoding="utf-8")
                lines.append(f"{tag}<={value_file}")
            else:
                lines.append(arg)

        argfile = tmp / "args.txt"
        argfile.write_text("\n".join(lines) + "\n", encoding="utf-8")

        proc = subprocess.run(  # noqa: S603  # nosec B603 B607
            [args[0], "-charset", "UTF8", "-@", str(argfile)],
            capture_output=True,
            timeout=timeout,
        )

    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def _read_date_fields(file_path: str) -> dict[str, str] | None:
    """Read existing date fields from the image so we can restore them after writing."""
    try:
        args = ["exiftool", "-json", "-n"] + [f"-{tag}" for tag in _DATE_TAGS] + [file_path]
        proc = _run_exiftool(args, timeout=10)
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
    hierarchical: list[str] | None = None,
    rating: int | None = None,
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
            fields (default). An unrecognized value writes none of the
            description/keyword fields (only date fields are restored).
            Whenever IPTC text goes out, ``IPTC:CodedCharacterSet`` is
            declared as UTF-8 alongside it: IIM has no default encoding, so
            undeclared non-ASCII is read as Latin-1 and arrives mangled.
        merge: When True, existing keywords are preserved and new keywords
            are added alongside them (XPKeywords is skipped — it is a flat
            semicolon-joined string that cannot be merged without reading
            it first). When False (default), existing keywords are cleared
            before writing.
        hierarchical: ``A|B|C`` keyword paths written to
            ``XMP-lr:HierarchicalSubject`` (Lightroom) and
            ``XMP-digiKam:TagsList`` (digiKam). Written alongside the flat
            ``keywords``, never instead of them: flat Subject is what every
            other tool reads, and dropping it to gain a tree would trade
            universal compatibility for two applications. Requires a format
            that includes XMP.
        rating: XMP star rating, 1-5. Skipped when None. See
            :func:`pyimgtag.hierarchy.stars_from_score` for the mapping from
            a 1-10 judge score.

    Returns:
        None on success, or an error message string on failure.
    """
    if description is None and not keywords and not hierarchical and rating is None:
        return None

    if not is_exiftool_available():
        return "exiftool is not available on this system"

    # Read date fields before writing so we can restore them
    saved_dates = _read_date_fields(file_path)

    args = ["exiftool", "-overwrite_original"]

    _write_xmp = fmt in ("auto", "xmp")
    _write_iptc = fmt in ("auto", "iptc")
    _write_exif_fields = fmt in ("auto", "exif")

    if _write_iptc and (description is not None or keywords):
        # IPTC IIM has no default encoding: a record with no CodedCharacterSet
        # is undeclared, and readers fall back to Latin-1. The strings here are
        # UTF-8, so without this a city like Obidos arrives as mojibake in any
        # reader that follows the spec. exiftool turns this into the ESC % G
        # escape the standard specifies. Only when IPTC text is actually going
        # out -- declaring a charset for a record with no text in it is noise.
        args.append("-codedcharacterset=utf8")

    if description is not None:
        if _write_exif_fields:
            args.append(f"-ImageDescription={description}")
            args.append(f"-UserComment={description}")
        if _write_xmp:
            args.append(f"-XMP:Description={description}")
        if _write_iptc:
            args.append(f"-IPTC:Caption-Abstract={description}")

    if keywords:
        # Plain '=' on a list tag always replaces the whole list, so merge mode
        # uses exiftool's idempotent add idiom: '-TAG-=kw' then '-TAG+=kw'
        # (remove-then-add avoids duplicate entries on re-runs).
        if _write_iptc:
            if merge:
                for kw in keywords:
                    args.append(f"-IPTC:Keywords-={kw}")
                    args.append(f"-IPTC:Keywords+={kw}")
            else:
                args.append("-IPTC:Keywords=")
                for kw in keywords:
                    args.append(f"-IPTC:Keywords={kw}")
        if _write_xmp:
            if merge:
                for kw in keywords:
                    args.append(f"-XMP:Subject-={kw}")
                    args.append(f"-XMP:Subject+={kw}")
            else:
                args.append("-XMP:Subject=")
                for kw in keywords:
                    args.append(f"-XMP:Subject={kw}")
        if _write_exif_fields and not merge:
            # XPKeywords is a semicolon-separated single string, not a list:
            # '+=' does not apply and rewriting it would drop existing
            # keywords, so it is skipped entirely in merge mode.
            args.append("-XPKeywords=")
            args.append(f"-XPKeywords={';'.join(keywords)}")

    if hierarchical and _write_xmp:
        # Both tags in the same pass. They are separate namespaces -- Lightroom
        # reads lr:HierarchicalSubject, digiKam reads digiKam:TagsList -- and
        # writing only one leaves the other application with a flat pile.
        for tag in ("XMP-lr:HierarchicalSubject", "XMP-digiKam:TagsList"):
            if merge:
                # Same remove-then-add idiom as the flat keywords above, so a
                # re-run does not accumulate duplicates.
                for path in hierarchical:
                    args.append(f"-{tag}-={path}")
                    args.append(f"-{tag}+={path}")
            else:
                args.append(f"-{tag}=")
                for path in hierarchical:
                    args.append(f"-{tag}={path}")

    if rating is not None and _write_xmp:
        args.append(f"-XMP:Rating={rating}")

    # Restore date fields to prevent silent timestamp changes
    if saved_dates:
        for tag, value in saved_dates.items():
            args.append(f"-{tag}={value}")

    args.append(file_path)

    try:
        proc = _run_exiftool(args, timeout=30)
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
    *,
    hierarchical: list[str] | None = None,
    rating: int | None = None,
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
        hierarchical: ``A|B|C`` keyword paths, written alongside the flat
            ``keywords``. RAW workflows live in sidecars, so a tree that only
            reached embedded metadata would miss exactly the users who care
            most about it.
        rating: XMP star rating, 1-5. Skipped when None.

    Returns:
        None on success, or an error message string on failure.
    """
    if description is None and not keywords and not hierarchical and rating is None:
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

    if hierarchical:
        for tag in ("XMP-lr:HierarchicalSubject", "XMP-digiKam:TagsList"):
            args.append(f"-{tag}=")
            for path in hierarchical:
                args.append(f"-{tag}={path}")

    if rating is not None:
        args.append(f"-XMP:Rating={rating}")

    if sidecar_path.exists():
        # Update existing sidecar in-place
        args += ["-overwrite_original", str(sidecar_path)]
    else:
        # Create new sidecar from source file (preserves other source metadata)
        args += ["-o", str(sidecar_path), file_path]

    try:
        proc = _run_exiftool(args, timeout=30)
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
        proc = _run_exiftool(
            [
                "exiftool",
                "-json",
                "-Description",
                "-ImageDescription",
                "-Keywords",
                "-Subject",
                target,
            ],
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
