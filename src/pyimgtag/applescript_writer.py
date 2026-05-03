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
import time
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


# Cold-start retry policy for the very first AppleScript / photoscript
# call into Photos.app: the bridge returns None until Photos finishes
# launching, so a one-shot retry after a short sleep turns "append mode:
# failed to read existing keywords" on the first image into a successful
# read for everything that follows.
_READ_RETRY_DELAY_SECONDS = 1.5
_READ_RETRY_ATTEMPTS = 2  # initial call + one retry


def read_keywords_from_photos(file_path: str) -> list[str] | None:
    """Read the current keyword list for a photo from Apple Photos.

    **macOS only.** Returns ``None`` on non-macOS or on any error.
    Returns ``[]`` when the photo exists but has no keywords.

    The first call into the Photos AppleScript bridge often fails on
    a cold-started Photos.app — append-mode write-back used to print
    "failed to read existing keywords, write aborted" for the very
    first image even when subsequent calls worked. This wrapper
    retries once after a short sleep so the cold-start failure no
    longer aborts the first write.

    Args:
        file_path: Full path to the image (only the basename is used for lookup).

    Returns:
        List of keyword strings, ``[]`` if no keywords, or ``None`` on failure.
    """
    if not _IS_MACOS:
        return None
    file_name = PurePosixPath(file_path).name
    reader = _read_via_photoscript if _use_photoscript() else _read_via_osascript

    last: list[str] | None = None
    for attempt in range(_READ_RETRY_ATTEMPTS):
        last = reader(file_name)
        if last is not None:
            return last
        # The first call hit Photos before it was ready; pause briefly
        # before retrying. Skip the sleep on the final attempt.
        if attempt < _READ_RETRY_ATTEMPTS - 1:
            time.sleep(_READ_RETRY_DELAY_SECONDS)
    return last


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


def _build_reveal_applescript(file_name: str) -> str:
    """Build AppleScript that activates Photos and reveals the matching item."""
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
        + "    activate\n"
        + lookup
        + "    spotlight theItem\n"
        + "end tell"
    )


def reveal_in_photos(file_path: str) -> str | None:
    """Activate Apple Photos and reveal the matching item.

    **macOS only.** Returns ``None`` on success, or an error message string
    on failure (non-macOS, osascript missing, photo not found, AppleScript
    error). Photo lookup mirrors the writer path: UUID stems try ``media
    item id`` first, then fall back to a filename scan; non-UUID stems go
    straight to the filename scan.
    """
    if not _IS_MACOS:
        return "Apple Photos reveal is only available on macOS"
    if not is_applescript_available():
        return "osascript is not available on this system"

    file_name = PurePosixPath(file_path).name
    script = _build_reveal_applescript(file_name)

    try:
        proc = subprocess.run(  # noqa: S603
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return "osascript timed out while revealing photo"
    except OSError as exc:
        return f"Failed to launch osascript: {exc}"

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        return (
            f"AppleScript error (exit {proc.returncode}): {stderr}"
            if stderr
            else f"AppleScript failed with exit code {proc.returncode}"
        )
    return None


def _build_membership_applescript() -> str:
    """Bulk AppleScript that returns the full ``id\\tfilename`` membership map.

    One call dumps every media item in the library so the drift cleanup
    can decide presence in O(1) Python lookups rather than spawning one
    osascript per DB row. Each output line is ``<id>\\t<filename>``;
    callers drop blank lines defensively.

    A per-item ``try`` block keeps a single misbehaving photo (iCloud-
    only, broken alias, etc.) from killing the whole traversal.
    """
    return (
        'tell application "Photos"\n'
        '    set out to ""\n'
        "    set lf to ASCII character 10\n"
        "    set ht to ASCII character 9\n"
        "    repeat with p in (get media items)\n"
        "        try\n"
        "            set out to out & (id of p) & ht & (filename of p) & lf\n"
        "        end try\n"
        "    end repeat\n"
        "    return out\n"
        "end tell"
    )


# Cap the bulk membership scan generously — Apple Photos can take many
# minutes to enumerate a 20k+ library. Aligns with the ceiling used by
# the faces import flow (see photos_faces_importer).
_MEMBERSHIP_TIMEOUT_SECONDS = 1800


def fetch_photos_membership(
    timeout: int = _MEMBERSHIP_TIMEOUT_SECONDS,
) -> tuple[set[str], str | None]:
    """Return a set of every Photos.app media-item id + filename.

    The set conflates UUIDs with bare filenames so the caller can
    answer "is this DB row's file known to Photos?" with a single
    ``in`` test no matter whether the on-disk stem is a UUID
    (``ABCD-…-EF.jpg``) or a free-form name (``IMG_1234.HEIC``).

    Args:
        timeout: Subprocess timeout in seconds. Defaults to 30 minutes
            so very large libraries do not trip a spurious failure.

    Returns:
        Tuple of ``(membership, error)``. ``membership`` is the union
        of media-item ids and filenames the script returned. ``error``
        is ``None`` on success and a short category string on failure
        — ``"platform_unsupported"`` (non-macOS), ``"osascript_missing"``,
        ``"timeout"``, ``"parse_error"`` (the ``-2741`` flake), or
        ``"applescript_failed"`` for any other non-zero exit.

        On any non-``None`` error the membership set is empty and the
        caller should degrade to the disk-only check.
    """
    if not _IS_MACOS:
        return set(), "platform_unsupported"
    if not is_applescript_available():
        return set(), "osascript_missing"

    script = _build_membership_applescript()

    try:
        proc = subprocess.run(  # noqa: S603
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return set(), "timeout"
    except OSError:
        return set(), "osascript_missing"

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        # The ``-2741`` parse flake is the Photos.app dictionary issue
        # the user has hit before; surface a dedicated category so the
        # drift command can degrade rather than spinning forever.
        if "(-2741)" in stderr or "syntax error" in stderr.lower():
            return set(), "parse_error"
        return set(), "applescript_failed"

    membership: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        if not line or "\t" not in line:
            continue
        item_id, filename = line.split("\t", 1)
        item_id = item_id.strip()
        filename = filename.strip()
        if item_id:
            membership.add(item_id)
        if filename:
            membership.add(filename)
    return membership, None


def _build_delete_applescript(file_name: str) -> str:
    """Build AppleScript that deletes the matching media item from Photos.

    Apple Photos.app's ``delete`` AppleScript verb is declared in the
    dictionary but has been broken since Catalina — it consistently
    returns ``-10000 AppleEvent handler failed``. The reliable
    workaround is UI scripting: spotlight the item, then send
    ``Cmd+Delete`` via System Events so the Photos UI itself performs
    the deletion. The item then routes through Photos' standard flow
    into the *Recently Deleted* album (30-day undo).

    Requirements for the UI-scripting path:
      - The terminal/IDE running pyimgtag needs Accessibility
        permission (System Settings → Privacy & Security →
        Accessibility). Without it, ``System Events`` keystroke
        delivery fails silently.
      - Photos → Settings → "Show Deletion Confirmation" should be
        OFF for unattended bulk deletes; otherwise each photo
        triggers a confirmation dialog. The script tries to dismiss
        any prompt by pressing Return, but this is best-effort.
    """
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
        # 1. Resolve theItem and ask Photos to spotlight it (selects in UI).
        'tell application "Photos"\n'
        "    activate\n"
        + lookup
        + "    spotlight theItem\n"
        + "end tell\n"
        # 2. Tiny delay so Photos' selection settles before we keystroke.
        + "delay 0.25\n"
        # 3. Cmd+Delete via System Events (the UI shortcut for "move
        #    to Recently Deleted"). ASCII 127 = Forward Delete; with
        #    `command down` Photos accepts it as the Delete shortcut.
        + 'tell application "System Events"\n'
        + '    tell process "Photos"\n'
        + "        keystroke (ASCII character 127) using command down\n"
        + "    end tell\n"
        + "end tell\n"
        # 4. Best-effort confirm-dialog dismiss. Photos shows a
        #    confirmation dialog when "Show Deletion Confirmation" is
        #    on; pressing Return clicks the default "Delete" button.
        #    Wrapped in `try` so a missing dialog doesn't fail the run.
        + "delay 0.15\n"
        + 'tell application "System Events"\n'
        + '    tell process "Photos"\n'
        + "        try\n"
        + "            key code 36\n"  # 36 = Return
        + "        end try\n"
        + "    end tell\n"
        + "end tell"
    )


def delete_from_photos(file_path: str) -> str | None:
    """Delete a media item from Apple Photos (moves to Recently Deleted).

    **macOS only.** Photos.app automatically routes the deletion to its
    *Recently Deleted* album, where it stays recoverable for 30 days —
    this helper deliberately does **not** empty that bin. Photo lookup
    mirrors :func:`reveal_in_photos` and :func:`write_to_photos`: UUID
    stems try ``media item id`` first and fall back to a filename scan,
    non-UUID stems go straight to the filename scan.

    Args:
        file_path: Full path to the image (only the basename is used for lookup).

    Returns:
        ``None`` on success, or an error message string on failure
        (non-macOS host, ``osascript`` unavailable, photo not found, or
        any AppleScript error).
    """
    if not _IS_MACOS:
        return "Apple Photos delete is only available on macOS"
    if not is_applescript_available():
        return "osascript is not available on this system"

    file_name = PurePosixPath(file_path).name
    script = _build_delete_applescript(file_name)

    try:
        # The UI-scripting path includes two short ``delay`` calls
        # plus two System Events keystrokes; budget conservatively so
        # a slow Photos.app launch (cold start, big library) doesn't
        # trip a spurious timeout.
        proc = subprocess.run(  # noqa: S603
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return "osascript timed out while deleting photo"
    except OSError as exc:
        return f"Failed to launch osascript: {exc}"

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        return (
            f"AppleScript error (exit {proc.returncode}): {stderr}"
            if stderr
            else f"AppleScript failed with exit code {proc.returncode}"
        )
    return None
