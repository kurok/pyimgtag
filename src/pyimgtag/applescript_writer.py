"""Write tags and description back to Apple Photos via AppleScript."""

from __future__ import annotations

import shutil
import subprocess


def _escape_applescript_string(value: str) -> str:
    """Escape a string for safe embedding in an AppleScript string literal.

    Replaces backslashes first, then double-quotes, then strips newlines.
    """
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    # Newlines are not valid inside AppleScript string literals; replace with a space.
    value = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return value


def _build_applescript(file_name: str, tags: list[str], summary: str | None) -> str:
    """Build the AppleScript source to set keywords (and optionally description) on a photo.

    Args:
        file_name: The bare filename (no directory path) used to locate the photo.
        tags: List of keyword strings to set.
        summary: Optional description string. Omitted from the script when None.

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

    script = (
        'tell application "Photos"\n'
        f'    set theItems to media items whose filename is "{safe_name}"\n'
        "    if (count of theItems) > 0 then\n"
        "        set theItem to item 1 of theItems\n"
        f"        set keywords of theItem to {tag_list}"
        f"{description_line}\n"
        "    else\n"
        f'        error "No Photos item found with filename: {safe_name}"\n'
        "    end if\n"
        "end tell"
    )
    return script


def write_to_photos(file_path: str, tags: list[str], summary: str | None) -> str | None:
    """Set keywords and description on a photo in Apple Photos via AppleScript.

    Uses the bare filename extracted from ``file_path`` to locate the photo in
    Photos. This is a best-effort match: if multiple library items share the
    same filename the first match is updated; if none are found an error is
    returned.

    Args:
        file_path: Full path to the image (only the basename is used for lookup).
        tags: List of keyword strings to assign to the photo.
        summary: Optional description/caption text. Skipped when ``None``.

    Returns:
        ``None`` on success, or an error message string on failure.
    """
    if not is_applescript_available():
        return "osascript is not available on this system"

    import os

    file_name = os.path.basename(file_path)
    script = _build_applescript(file_name, tags, summary)

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


def is_applescript_available() -> bool:
    """Return True if osascript is available on this system."""
    return shutil.which("osascript") is not None
