"""Write tags and description back to Apple Photos.

Uses the osascript subprocess path by default. The in-process photoscript
path is faster but imports photoscript in the main process, which can
trigger a macOS hiservices crash on some systems — opt in via the
``PYIMGTAG_USE_PHOTOSCRIPT`` env var when you know the host is stable.
Only available on macOS.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import PurePosixPath

_IS_MACOS = sys.platform == "darwin"


@lru_cache(maxsize=None)
def _has_photoscript() -> bool:
    """Return True if photoscript is installed (checked via find_spec, cached).

    Uses importlib.util.find_spec() to avoid triggering module execution.
    On some macOS configurations importing photoscript can crash the process;
    find_spec only inspects the package metadata, making it safe to call anywhere.
    """
    import importlib.util

    return importlib.util.find_spec("photoscript") is not None


def _use_photoscript() -> bool:
    """Return True if the in-process photoscript path should be used.

    Default is False — the safer osascript subprocess path is used. Set
    ``PYIMGTAG_USE_PHOTOSCRIPT=1`` to opt in on hosts where importing
    photoscript is known to be stable. The env var is read on every call
    so tests and users can flip it without restarting the process.
    """
    if not _has_photoscript():
        return False
    return os.environ.get("PYIMGTAG_USE_PHOTOSCRIPT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# Standard UUID pattern: 8-4-4-4-12 hex digits.
# Photos uses the filename stem as media item id only when it matches this format.
# Non-matching stems go straight to the O(n) filename scan, avoiding a slow
# AppleScript timeout that occurs when media item id is called with an unknown id.
_UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


def _looks_like_uuid(stem: str) -> bool:
    """Return True if *stem* matches the 8-4-4-4-12 hex UUID format used by Photos."""
    return bool(_UUID_RE.match(stem))


def is_applescript_available() -> bool:
    """Return True if AppleScript write-back is available (macOS only).

    AppleScript functionality requires:
    1. Running on macOS
    2. osascript being available on the system path
    """
    if not _IS_MACOS:
        return False
    return shutil.which("osascript") is not None


def _escape_applescript_string(value: str) -> str:
    """Escape a string for safe embedding in an AppleScript string literal.

    Replaces backslashes first, then double-quotes, then strips newlines.
    """
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    # Newlines are not valid inside AppleScript string literals; replace with a space.
    value = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return value


def _filename_scan_block(safe_file_name: str, indent: str = "    ") -> str:
    """Return an AppleScript fragment that locates a photo by filename."""
    i = indent
    return (
        f'{i}set _results to (every media item whose filename = "{safe_file_name}")\n'
        f"{i}if (count of _results) is 0 then\n"
        f'{i}    error "Photo not found: {safe_file_name}"\n'
        f"{i}end if\n"
        f"{i}set theItem to item 1 of _results\n"
    )


def _build_applescript(
    file_name: str,
    tags: list[str],
    summary: str | None,
    title: str | None = None,
) -> str:
    """Build the AppleScript source to set keywords, description, and title on a photo.

    Args:
        file_name: The bare filename (no directory path) used to locate the photo.
        tags: List of keyword strings to set.
        summary: Optional description string. Omitted from the script when None.
        title: Optional title string. Omitted from the script when None.

    Returns:
        AppleScript source string ready to pass to ``osascript -e``.
    """
    stem = PurePosixPath(file_name).stem
    safe_file_name = _escape_applescript_string(file_name)

    escaped_tags = [f'"{_escape_applescript_string(t)}"' for t in tags]
    tag_list = "{" + ", ".join(escaped_tags) + "}"

    description_line = ""
    if summary is not None:
        safe_summary = _escape_applescript_string(summary)
        description_line = f'\n    set description of theItem to "{safe_summary}"'

    title_line = ""
    if title is not None:
        safe_title = _escape_applescript_string(title)
        title_line = f'\n    set name of theItem to "{safe_title}"'

    if _looks_like_uuid(stem):
        # UUID-format stem: try O(1) media item id first, fall back to filename scan.
        # Non-UUID stems skip the media item id call entirely — Photos takes several
        # seconds to confirm an unknown id, making each lookup slow.
        uuid = _escape_applescript_string(stem)
        lookup = (
            "    try\n"
            f'        set theItem to media item id "{uuid}"\n'
            "    on error\n"
            + _filename_scan_block(safe_file_name, indent="        ")
            + "    end try\n"
        )
    else:
        lookup = _filename_scan_block(safe_file_name)

    return (
        'tell application "Photos"\n'
        + lookup
        + f"    set keywords of theItem to {tag_list}"
        + description_line
        + title_line
        + "\nend tell"
    )


def _write_via_photoscript(
    file_name: str,
    tags: list[str],
    summary: str | None,
    title: str | None = None,
) -> str | None:
    """Write to Photos using photoscript library."""
    import photoscript as _ps

    try:
        photos_app = _ps.PhotosLibrary()
        stem = PurePosixPath(file_name).stem
        if not _looks_like_uuid(stem):
            # Non-UUID filename: skip photoscript UUID lookup to avoid a slow timeout.
            # The caller will fall through to osascript filename scan.
            return f"No Photos item found with filename: {file_name}"
        try:
            photo = photos_app.photo(uuid=stem)
        except Exception:
            return f"No Photos item found with filename: {file_name}"
        photo.keywords = tags
        if summary is not None:
            photo.description = summary
        if title is not None:
            photo.title = title
        return None
    except Exception as exc:
        return f"photoscript error: {exc}"


def _write_via_osascript(
    file_name: str,
    tags: list[str],
    summary: str | None,
    title: str | None = None,
) -> str | None:
    """Write to Photos using raw osascript subprocess."""
    if not is_applescript_available():
        return "osascript is not available on this system"

    script = _build_applescript(file_name, tags, summary, title=title)

    try:
        proc = subprocess.run(  # noqa: S603
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "osascript timed out after 30 seconds"
    except OSError as exc:
        return f"Failed to launch osascript: {exc}"

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        return (
            f"AppleScript error (exit {proc.returncode}): {stderr}"
            if stderr
            else (f"AppleScript failed with exit code {proc.returncode}")
        )

    return None


def _build_read_applescript(file_name: str) -> str:
    """Build AppleScript to read keywords list from a photo, returning newline-separated."""
    stem = PurePosixPath(file_name).stem
    safe_file_name = _escape_applescript_string(file_name)

    if _looks_like_uuid(stem):
        uuid = _escape_applescript_string(stem)
        lookup = (
            "    try\n"
            f'        set theItem to media item id "{uuid}"\n'
            "    on error\n"
            + _filename_scan_block(safe_file_name, indent="        ")
            + "    end try\n"
        )
    else:
        lookup = _filename_scan_block(safe_file_name)

    return (
        'tell application "Photos"\n'
        + lookup
        + "    set kws to keywords of theItem\n"
        + "    set AppleScript's text item delimiters to (ASCII character 10)\n"
        + "    return kws as text\n"
        + "end tell"
    )


def _read_via_photoscript(file_name: str) -> list[str] | None:
    """Read keywords from Photos using photoscript library."""
    import photoscript as _ps

    try:
        photos_app = _ps.PhotosLibrary()
        stem = PurePosixPath(file_name).stem
        if not _looks_like_uuid(stem):
            return None
        try:
            photo = photos_app.photo(uuid=stem)
        except Exception:
            return None  # photo not found — cannot safely append
        return list(photo.keywords or [])
    except Exception:
        return None


def _read_via_osascript(file_name: str) -> list[str] | None:
    """Read keywords from Photos via raw osascript subprocess."""
    if not is_applescript_available():
        return None
    script = _build_read_applescript(file_name)
    try:
        proc = subprocess.run(  # noqa: S603
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    if not raw:
        return []
    return [k.strip() for k in raw.split("\n") if k.strip()]


def read_keywords_from_photos(file_path: str) -> list[str] | None:
    """Read the current keyword list for a photo from Apple Photos.

    **macOS only.** Returns ``None`` on non-macOS or on any error.
    Returns ``[]`` when the photo exists but has no keywords.

    Args:
        file_path: Full path to the image (only the basename is used for lookup).

    Returns:
        List of keyword strings, ``[]`` if no keywords, or ``None`` on failure.
    """
    if not _IS_MACOS:
        return None
    file_name = PurePosixPath(file_path).name
    if _use_photoscript():
        return _read_via_photoscript(file_name)
    return _read_via_osascript(file_name)


def write_to_photos(
    file_path: str,
    tags: list[str],
    summary: str | None,
    title: str | None = None,
    mode: str = "overwrite",
) -> str | None:
    """Set keywords, description, and title on a photo in Apple Photos.

    **macOS only.** Returns an error on non-macOS systems.

    Args:
        file_path: Full path to the image (only the basename is used for lookup).
        tags: List of keyword strings to assign to the photo.
        summary: Optional description/caption text. Skipped when ``None``.
        title: Optional title text. Skipped when ``None``.
        mode: ``"overwrite"`` (default) replaces all keywords; ``"append"`` reads
            existing keywords, removes any ``score:*`` entry, then merges with *tags*.

    Returns:
        ``None`` on success, or an error message string on failure.
    """
    if not _IS_MACOS:
        return "Apple Photos write-back is only available on macOS"

    file_name = PurePosixPath(file_path).name

    final_tags = tags
    if mode == "append":
        existing = read_keywords_from_photos(file_path)
        if existing is None:
            return "append mode: failed to read existing keywords, write aborted"
        cleaned_existing = [k for k in existing if not k.lower().startswith("score:")]
        seen: set[str] = set(t.lower() for t in tags)
        merged = list(tags)
        for k in cleaned_existing:
            if k.lower() not in seen:
                seen.add(k.lower())
                merged.append(k)
        final_tags = merged

    if _use_photoscript():
        result = _write_via_photoscript(file_name, final_tags, summary, title=title)
        if result is None:
            return None
    return _write_via_osascript(file_name, final_tags, summary, title=title)
