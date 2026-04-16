"""Write tags and description back to Apple Photos.

Uses photoscript (Python wrapper around Photos AppleScript) when available,
falls back to raw osascript subprocess.
"""

from __future__ import annotations

import shutil
import subprocess

try:
    import photoscript
except ImportError:
    photoscript = None  # type: ignore[assignment]

_HAS_PHOTOSCRIPT = photoscript is not None


def _escape_applescript_string(value: str) -> str:
    """Escape a string for safe embedding in an AppleScript string literal.

    Replaces backslashes first, then double-quotes, then strips newlines.
    """
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    # Newlines are not valid inside AppleScript string literals; replace with a space.
    value = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return value


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
    safe_name = _escape_applescript_string(file_name)

    # Build AppleScript list literal: {"tag1", "tag2", ...}
    escaped_tags = [f'"{_escape_applescript_string(t)}"' for t in tags]
    tag_list = "{" + ", ".join(escaped_tags) + "}"

    description_line = ""
    if summary is not None:
        safe_summary = _escape_applescript_string(summary)
        description_line = f'\n        set description of theItem to "{safe_summary}"'

    title_line = ""
    if title is not None:
        safe_title = _escape_applescript_string(title)
        title_line = f'\n        set name of theItem to "{safe_title}"'

    script = (
        'tell application "Photos"\n'
        f'    set theItems to media items whose filename is "{safe_name}"\n'
        "    if (count of theItems) > 0 then\n"
        "        set theItem to item 1 of theItems\n"
        f"        set keywords of theItem to {tag_list}"
        f"{description_line}"
        f"{title_line}\n"
        "    else\n"
        f'        error "No Photos item found with filename: {safe_name}"\n'
        "    end if\n"
        "end tell"
    )
    return script


def _write_via_photoscript(
    file_name: str,
    tags: list[str],
    summary: str | None,
    title: str | None = None,
) -> str | None:
    """Write to Photos using photoscript library."""
    try:
        photos_app = photoscript.PhotosLibrary()
        results = photos_app.search(file_name)
        # Filter to exact filename match
        matched = [p for p in results if p.filename == file_name]
        if not matched:
            return f"No Photos item found with filename: {file_name}"
        photo = matched[0]
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


def write_to_photos(
    file_path: str,
    tags: list[str],
    summary: str | None,
    title: str | None = None,
) -> str | None:
    """Set keywords, description, and title on a photo in Apple Photos.

    Uses photoscript when installed (cleaner API, better error handling),
    falls back to raw AppleScript subprocess.

    Args:
        file_path: Full path to the image (only the basename is used for lookup).
        tags: List of keyword strings to assign to the photo.
        summary: Optional description/caption text. Skipped when ``None``.
        title: Optional title text. Skipped when ``None``.

    Returns:
        ``None`` on success, or an error message string on failure.
    """
    import os

    file_name = os.path.basename(file_path)

    if _HAS_PHOTOSCRIPT:
        return _write_via_photoscript(file_name, tags, summary, title=title)
    return _write_via_osascript(file_name, tags, summary, title=title)


def is_applescript_available() -> bool:
    """Return True if osascript is available on this system."""
    return shutil.which("osascript") is not None
