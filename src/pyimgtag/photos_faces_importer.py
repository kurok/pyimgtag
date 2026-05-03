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


def _bulk_applescript_every_person() -> str:
    """Bulk AppleScript using the ``every person of p`` element traversal.

    Returns ``<uuid>\\t<persons>\\n`` rows. Persons are joined by ``|``
    (a character that does not occur in Photos UUIDs and is rare in
    real names). Photos with no persons still emit a row so the parser
    can skip them with a single check.

    Uses the photoscript-canonical ``name of every person of p`` form.
    Apple Photos.app's AppleScript dictionary exposes ``person`` as an
    element class on a media item, so ``every person of p`` works on
    all macOS versions that ship with the documented dictionary.

    On *some* Photos.app builds (locale variants, betas, particular
    macOS releases) the dictionary does **not** terminologise
    ``person`` as a class — osascript then refuses to compile the
    script with ``-2741: Expected class name but found identifier``.
    The caller falls back to :func:`_bulk_applescript_persons_property`
    in that case.
    """
    return (
        'tell application "Photos"\n'
        '    set out to ""\n'
        "    set lf to ASCII character 10\n"
        "    set ht to ASCII character 9\n"
        "    repeat with p in (get media items)\n"
        '        set ks to ""\n'
        # Per-photo ``try`` / ``on error`` keeps a single problem item
        # from killing the whole bulk traversal (iCloud-only photos
        # and AppleScript-broken rows are real). On error we set an
        # empty name list — the photo still produces a row in ``out``.
        "        try\n"
        "            set name_list to (name of every person of p)\n"
        "        on error\n"
        "            set name_list to {}\n"
        "        end try\n"
        "        repeat with nm in name_list\n"
        f'            set ks to ks & nm & "{_PERSON_NAME_SEPARATOR}"\n'
        "        end repeat\n"
        "        try\n"
        "            set out to out & (id of p) & ht & ks & lf\n"
        "        end try\n"
        "    end repeat\n"
        "    return out\n"
        "end tell"
    )


def _bulk_applescript_persons_property() -> str:
    """Bulk AppleScript fallback that avoids the ``person`` class identifier.

    Same row format as :func:`_bulk_applescript_every_person` but uses
    only the ``persons`` *property* on a media item plus index-based
    access. AppleScript treats ``persons`` as a plain identifier, so
    the script compiles even when Photos.app's dictionary doesn't
    terminologise ``person`` as a class (see ``-2741`` from osascript
    on some installs). ``name of <ref>`` works on any object, so we
    can read each person's name without naming the class anywhere.
    """
    return (
        'tell application "Photos"\n'
        '    set out to ""\n'
        "    set lf to ASCII character 10\n"
        "    set ht to ASCII character 9\n"
        "    repeat with p in (get media items)\n"
        '        set ks to ""\n'
        "        try\n"
        "            set _persons to persons of p\n"
        "            repeat with i from 1 to count of _persons\n"
        "                try\n"
        "                    set _nm to name of (item i of _persons)\n"
        f'                    set ks to ks & _nm & "{_PERSON_NAME_SEPARATOR}"\n'
        "                end try\n"
        "            end repeat\n"
        "        end try\n"
        "        try\n"
        "            set out to out & (id of p) & ht & ks & lf\n"
        "        end try\n"
        "    end repeat\n"
        "    return out\n"
        "end tell"
    )


def _bulk_applescript_app_people() -> str:
    """Bulk AppleScript that walks the **application-level** persons collection.

    Some macOS Photos.app builds expose persons only as a top-level
    collection on the application object — the user's "People"
    sidebar shows named items but no ``person``-of-media-item
    accessor surfaces them. Walk each person and read their attached
    photos:

        repeat with p in (people of application "Photos")
            repeat with _photo in (photos of p)
                emit (id of _photo) <TAB> (name of p) <LF>

    Output format is ``<uuid>\\t<single_name>\\n`` (one row per
    photo×person), distinct from the per-media-item scripts which
    emit ``<uuid>\\t<pipe-joined-names>\\n``. The Python parser
    handles both shapes.

    Probes three identifiers in turn (``every person`` at the app
    level, ``persons`` plural property, ``people`` UI-facing
    terminology). Each probe is its own ``try`` block so unknown
    identifiers don't poison the script.
    """
    return (
        'tell application "Photos"\n'
        '    set out to ""\n'
        "    set lf to ASCII character 10\n"
        "    set ht to ASCII character 9\n"
        "    set _people_list to {}\n"
        "    try\n"
        "        set _people_list to (every person)\n"
        "    end try\n"
        "    if _people_list is {} then\n"
        "        try\n"
        "            set _people_list to persons\n"
        "        end try\n"
        "    end if\n"
        "    if _people_list is {} then\n"
        "        try\n"
        "            set _people_list to people\n"
        "        end try\n"
        "    end if\n"
        "    repeat with p in _people_list\n"
        "        try\n"
        "            set _name to name of p\n"
        "            try\n"
        "                set _photo_list to photos of p\n"
        "                repeat with _photo in _photo_list\n"
        "                    try\n"
        "                        set out to out & (id of _photo) & ht & _name & lf\n"
        "                    end try\n"
        "                end repeat\n"
        "            end try\n"
        "        end try\n"
        "    end repeat\n"
        "    return out\n"
        "end tell"
    )


# Back-compat alias — older tests import the original name directly.
def _bulk_applescript() -> str:
    """Default bulk script (``every person`` form)."""
    return _bulk_applescript_every_person()


# osascript exit-code that means "the script could not be parsed"; used to
# detect the ``-2741`` "Expected class name but found identifier" failure
# without string-matching the entire stderr.
_PARSE_ERROR_MARKERS = ("(-2741)", "syntax error", "Expected class name")


def _run_bulk_osascript(script: str) -> "subprocess.CompletedProcess[str]":
    """Invoke osascript with ``script`` and return the completed process.

    Raises :class:`_BulkAppleScriptUnavailable` for failures the caller
    can't act on (timeout, missing binary). Non-zero exits are returned
    so the caller can inspect stderr and decide whether to retry with
    an alternate script.
    """
    try:
        return subprocess.run(  # noqa: S603
            ["/usr/bin/osascript", "-e", script],
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


def _collect_via_bulk_applescript(emit: Callable[[str], None]) -> dict[str, list[str]]:
    """Run one osascript call, parse the output, and group by name.

    Tries the ``every person of p`` form first (works on most macOS
    Photos.app builds). On the ``-2741`` "Expected class name but
    found identifier" parse failure — Photos.app on this install
    doesn't terminologise ``person`` as a class — falls back to the
    ``persons`` property + index-iteration form, which compiles
    without naming the class anywhere.

    Raises :class:`_BulkAppleScriptUnavailable` (caller falls back to
    photoscript) when the subprocess can't be launched, times out, or
    both scripts fail. A successful run with empty output is *not* an
    error — the library may simply have no photos.
    """
    emit("Asking Photos for the full id→persons map (one AppleScript call)…")

    started = time.monotonic()

    proc = _run_bulk_osascript(_bulk_applescript_every_person())
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if any(marker in stderr for marker in _PARSE_ERROR_MARKERS):
            emit(
                "Photos.app does not expose 'person' as a scriptable class on "
                "this install (osascript -2741); retrying with 'persons' property…"
            )
            logger.warning(
                "bulk AppleScript 'every person' failed to compile; retrying "
                "via 'persons' property: %s",
                stderr,
            )
            proc = _run_bulk_osascript(_bulk_applescript_persons_property())

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise _BulkAppleScriptUnavailable(
            f"osascript exit {proc.returncode}: {stderr}"
            if stderr
            else f"osascript exit {proc.returncode}"
        )

    name_to_uuids = _parse_bulk_output(proc.stdout or "", started, emit)

    # If the per-media-item scripts ran but reported zero persons across
    # the whole library, the user's Photos.app may simply not expose
    # persons at the media-item level even though the People sidebar
    # shows them. Walk Photos' application-level ``people`` collection
    # instead and merge any rows it returns.
    if not name_to_uuids:
        emit(
            "Per-photo person accessor returned 0 persons; "
            "retrying via the application-level 'people' collection…"
        )
        logger.warning(
            "bulk AppleScript per-photo paths returned 0 persons; "
            "retrying via app-level people collection"
        )
        app_proc = _run_bulk_osascript(_bulk_applescript_app_people())
        if app_proc.returncode == 0:
            name_to_uuids = _parse_bulk_output(app_proc.stdout or "", started, emit)
        else:
            logger.warning(
                "app-level 'people' walk also failed: %s",
                (app_proc.stderr or "").strip(),
            )

    return name_to_uuids


def _parse_bulk_output(
    stdout: str,
    started: float,
    emit: Callable[[str], None],
) -> dict[str, list[str]]:
    """Parse the ``<uuid>\\t<names>\\n`` output of any bulk script.

    Accepts both row formats:
      - per-photo (per-media-item scripts): pipe-joined names per row,
        one row per photo.
      - per-(photo, person) pair (app-level walker): one row per pair,
        same line format but a single name in the second field.

    Both shapes accumulate into ``name -> [uuid, ...]`` because the
    parser splits on ``|`` (no-op for single-name rows) and treats
    each (uuid, name) pair as additive.
    """
    name_to_uuids: dict[str, list[str]] = {}
    processed = 0
    persons_found: set[str] = set()
    seen_uuids: set[str] = set()
    for line in stdout.splitlines():
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
        if uuid not in seen_uuids:
            processed += 1
            seen_uuids.add(uuid)
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
