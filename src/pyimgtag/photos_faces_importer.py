"""Import named persons from Apple Photos into the faces DB.

Requires the [photos] extra: pip install pyimgtag[photos]
Only available on macOS with Apple Photos access.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING

from pyimgtag.applescript_writer import is_applescript_available

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)


# Cap how long we wait for the bulk AppleScript to finish. Apple Photos can
# take many minutes to enumerate a 20k+ photo library; we'd rather block
# here (with the user-visible "asking Photos…" message) than fail fast and
# silently re-run osascript per photo. 30 minutes is a generous ceiling.
_BULK_APPLESCRIPT_TIMEOUT_SECONDS = 1800

# Field separator used inside the bulk AppleScript output. Must not occur in
# UUIDs (hex) or person names; pipe is rare in real names but we still split
# defensively.
_PERSON_NAME_SEPARATOR = "|"

# How often the photoscript fallback path emits a progress line. Matches the
# bulk path's "every 200 items" cadence so the user sees the same heartbeat
# regardless of which path runs.
_PROGRESS_EVERY = 200


@lru_cache(maxsize=None)
def _has_photoscript() -> bool:
    """Return True if photoscript is installed (checked via find_spec, cached).

    Uses importlib.util.find_spec() to avoid triggering module execution,
    which can crash the process on some macOS configurations.
    """
    import importlib.util

    return importlib.util.find_spec("photoscript") is not None


def _default_progress(message: str) -> None:
    """Emit a progress line to stderr.

    Uses ``\\r`` so periodic counters overwrite themselves rather than
    spamming scrollback. The startup banner and the final summary use
    plain ``\\n`` newlines (handed in by the caller already).
    """
    print(message, file=sys.stderr, flush=True)


def import_photos_persons(
    db: ProgressDB,
    *,
    progress: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """Import named persons from Apple Photos into the faces DB.

    For each named person Apple Photos has tagged on a photo:
    - Creates a person row with ``source='photos'``, ``trusted=True``,
      ``confirmed=True``.
    - For photos with exactly one detected face in the local faces DB:
      assigns that face to the person.
    - For photos with multiple detected faces: person is created but the
      photo's faces are left unassigned (logged as skipped — requires
      manual review).
    - Photos not yet in the faces DB are ignored.

    Two enumeration paths exist:

    1. **Bulk AppleScript** (preferred). One ``osascript`` call returns
       every ``(uuid, persons)`` pair from Photos. This avoids
       photoscript's per-photo ``photoExists`` validation, which makes
       the photoscript path take many minutes on a 20k+ library while
       producing zero output.

    2. **photoscript fallback**. Used when ``osascript`` is missing or
       the bulk script fails. Same behaviour as before.

    Args:
        db: ProgressDB instance (faces must be scanned first via
            ``faces scan``).
        progress: Optional callable receiving status strings (banner +
            periodic heartbeat + final summary). When ``None`` the
            messages go to stderr by default — never silenced, because
            silence-by-default is exactly the bug this guards against.

    Returns:
        Tuple of ``(imported_count, skipped_count)`` where
        ``imported_count`` is the number of person rows created and
        ``skipped_count`` is the number of multi-face photos that
        could not be auto-assigned.

    Raises:
        RuntimeError: If neither the AppleScript path nor photoscript is
            available.
    """
    emit = progress if progress is not None else _default_progress

    emit("Scanning Photos library… (this can take several minutes for large libraries)")

    name_to_uuids = _collect_name_to_uuids(emit)

    return _materialize_persons(db, name_to_uuids, emit)


def _collect_name_to_uuids(emit: Callable[[str], None]) -> dict[str, list[str]]:
    """Build the ``name -> [uuid, ...]`` map, preferring the bulk AppleScript.

    Falls back to photoscript only when osascript is unavailable or the
    bulk script returns nothing usable. Either path emits at least one
    progress line so the user sees activity.
    """
    if is_applescript_available():
        try:
            return _collect_via_bulk_applescript(emit)
        except _BulkAppleScriptUnavailable as exc:
            logger.warning("Bulk AppleScript path unavailable: %s — falling back", exc)

    if not _has_photoscript():
        raise RuntimeError(
            "Neither osascript nor photoscript is available. Install the [photos] extra "
            "(pip install pyimgtag[photos]) or run on macOS with osascript."
        )

    return _collect_via_photoscript(emit)


class _BulkAppleScriptUnavailable(Exception):
    """Raised when the bulk AppleScript path can't run; caller falls back."""


def _bulk_applescript() -> str:
    """The single AppleScript that returns ``<uuid>\\t<persons>\\n`` per photo.

    Persons are joined by ``|`` (a character that does not occur in
    Photos UUIDs and is rare in real names). Photos with no persons
    still emit a row — the trailing field is empty — so the parser can
    skip them with a single check.
    """
    return (
        'tell application "Photos"\n'
        '    set out to ""\n'
        "    set lf to ASCII character 10\n"
        "    set ht to ASCII character 9\n"
        "    repeat with p in (get media items)\n"
        '        set ks to ""\n'
        "        try\n"
        "            repeat with pers in (persons of p)\n"
        f'                set ks to ks & (name of pers) & "{_PERSON_NAME_SEPARATOR}"\n'
        "            end repeat\n"
        "        end try\n"
        "        try\n"
        "            set out to out & (id of p) & ht & ks & lf\n"
        "        end try\n"
        "    end repeat\n"
        "    return out\n"
        "end tell"
    )


def _collect_via_bulk_applescript(emit: Callable[[str], None]) -> dict[str, list[str]]:
    """Run one osascript call, parse the output, and group by name.

    Raises :class:`_BulkAppleScriptUnavailable` (caller falls back) when
    the subprocess can't be launched, times out, or returns a non-zero
    status. A successful run with empty output is *not* an error — the
    library may simply have no photos.
    """
    emit("Asking Photos for the full id→persons map (one AppleScript call)…")

    started = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603
            ["/usr/bin/osascript", "-e", _bulk_applescript()],
            capture_output=True,
            text=True,
            timeout=_BULK_APPLESCRIPT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise _BulkAppleScriptUnavailable(
            f"osascript timed out after {_BULK_APPLESCRIPT_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise _BulkAppleScriptUnavailable(f"failed to launch osascript: {exc}") from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise _BulkAppleScriptUnavailable(
            f"osascript exit {proc.returncode}: {stderr}"
            if stderr
            else f"osascript exit {proc.returncode}"
        )

    name_to_uuids: dict[str, list[str]] = {}
    processed = 0
    persons_found: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        # Defensive: blank lines and rows without the tab separator are
        # silently skipped so one weird photo doesn't blow up the import.
        if not line:
            continue
        if "\t" not in line:
            continue
        uuid, raw_names = line.split("\t", 1)
        uuid = uuid.strip()
        if not uuid:
            continue
        processed += 1
        names = [n.strip() for n in raw_names.split(_PERSON_NAME_SEPARATOR) if n.strip()]
        for name in names:
            name_to_uuids.setdefault(name, []).append(uuid)
            persons_found.add(name)

        if processed % _PROGRESS_EVERY == 0:
            elapsed = int(time.monotonic() - started)
            emit(
                f"\r[faces] processed {processed} photos · "
                f"{len(persons_found)} persons found · elapsed {elapsed}s"
            )

    elapsed = int(time.monotonic() - started)
    emit(
        f"\r[faces] processed {processed} photos · "
        f"{len(persons_found)} persons found · elapsed {elapsed}s"
    )
    # Newline so the next print starts on its own line, not after the \r.
    emit("")
    return name_to_uuids


def _collect_via_photoscript(emit: Callable[[str], None]) -> dict[str, list[str]]:
    """Per-photo photoscript fallback. Slow, but works without osascript."""
    import photoscript  # local import; only runs when photoscript is actually available

    library = photoscript.PhotosLibrary()
    photos = _list_photos(library)

    started = time.monotonic()
    name_to_uuids: dict[str, list[str]] = {}
    processed = 0
    persons_found: set[str] = set()

    for photo in photos:
        names = _photo_person_names(photo)
        processed += 1
        if names:
            try:
                uuid = photo.uuid
            except Exception as exc:  # noqa: BLE001 — photoscript wraps AppleScript errors
                logger.debug("Skipping photo with unreadable uuid: %s", exc)
            else:
                for name in names:
                    cleaned = name.strip()
                    if cleaned:
                        name_to_uuids.setdefault(cleaned, []).append(uuid)
                        persons_found.add(cleaned)

        if processed % _PROGRESS_EVERY == 0:
            elapsed = int(time.monotonic() - started)
            emit(
                f"\r[faces] processed {processed} photos · "
                f"{len(persons_found)} persons found · elapsed {elapsed}s"
            )

    elapsed = int(time.monotonic() - started)
    emit(
        f"\r[faces] processed {processed} photos · "
        f"{len(persons_found)} persons found · elapsed {elapsed}s"
    )
    emit("")
    return name_to_uuids


def _materialize_persons(
    db: ProgressDB,
    name_to_uuids: dict[str, list[str]],
    emit: Callable[[str], None],
) -> tuple[int, int]:
    """Create person rows + assign single-face photos. Pure DB work."""
    imported = 0
    skipped = 0

    for name, uuids in name_to_uuids.items():
        # Idempotent: a previous import for this name leaves the row in
        # place; the second import is a no-op.
        if db.has_photos_person(name):
            continue

        person_id = db.create_person(label=name, confirmed=True, source="photos", trusted=True)
        imported += 1

        for uuid in uuids:
            faces = db.get_faces_by_uuid(uuid)
            if len(faces) == 1:
                db.set_person_id(faces[0]["id"], person_id)
            elif len(faces) > 1:
                logger.warning(
                    "Photos person %r: photo %s has %d detected faces — skipping auto-assign",
                    name,
                    uuid,
                    len(faces),
                )
                skipped += 1

    emit(
        f"[faces] import complete: {imported} new person(s), {skipped} multi-face photo(s) skipped"
    )
    return imported, skipped


def _list_photos(library: object) -> list:
    """Return every photo in the library, or an empty list if the call fails.

    photoscript's ``PhotosLibrary.photos()`` is the single supported way
    to enumerate the library; older versions sometimes returned a
    generator and newer ones a list. We hand back a concrete list either
    way and surface OS errors as a warning rather than a traceback.
    """
    try:
        return list(library.photos())  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — bubbled from AppleScript/Photos
        logger.error("Could not enumerate Photos library: %s", exc)
        return []


def _photo_person_names(photo: object) -> list[str]:
    """Read the named-person list off a photoscript Photo, defensively.

    photoscript exposes person tags as ``Photo.persons`` (a list of
    strings). Some versions raise on photos with no person metadata or
    on iCloud-only items the script bridge can't resolve; treat any
    failure as "no persons here".
    """
    try:
        names = photo.persons  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read persons for photo: %s", exc)
        return []
    if not names:
        return []
    if isinstance(names, str):
        # Defensive: some bridges return a single-name string instead of
        # a one-element list.
        return [names]
    return [str(n) for n in names]
