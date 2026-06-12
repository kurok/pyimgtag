"""Import named persons from Apple Photos into the faces DB.

Requires the [photos] extra: pip install pyimgtag[photos]
Only available on macOS with Apple Photos access.
"""

from __future__ import annotations

import logging
import subprocess  # nosec B404
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

# Multi-face (group) photo auto-linking. A detected face is linked to a Photos
# person only when its 128-d encoding is within this euclidean distance of the
# person's reference centroid. face_recognition treats <0.6 as the same person;
# we stay tighter so group shots are not mis-assigned. The face must also be
# clearer than the next candidate by _MULTI_FACE_MATCH_MARGIN, else the photo is
# left for manual review.
_MULTI_FACE_MATCH_THRESHOLD = 0.5
_MULTI_FACE_MATCH_MARGIN = 0.05


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
    print(message, file=sys.stderr, flush=True, end="" if message.startswith("\r") else "\n")


def import_photos_persons(
    db: ProgressDB,
    *,
    progress: Callable[[str], None] | None = None,
    library_path: str | None = None,
) -> tuple[int, int]:
    """Import named persons from Apple Photos into the faces DB.

    For each named person Apple Photos has tagged on a photo:
    - Creates a person row with ``source='photos'``, ``trusted=True``,
      ``confirmed=True``.
    - For photos with exactly one detected face in the local faces DB:
      assigns that face to the person.
    - For photos with multiple detected faces (group shots): links the face
      whose embedding best matches the person's reference faces, when the
      match is confident; otherwise leaves the photo for manual review
      (logged as skipped). See :func:`_assign_faces_to_person`.
    - Photos not yet in the faces DB are ignored.

    Enumeration paths, in preference order:

    1. **Photos library DB via osxphotos** (preferred when installed). Reads
       the library's SQLite directly for each person's exact name + photo
       UUIDs — no AppleScript, so it works on builds where the ``person``
       class isn't scriptable (the ``-2741`` failure) and returns names
       verbatim (Cyrillic etc.) rather than via fragile OCR.
    2. **Bulk AppleScript**. One ``osascript`` call returns every
       ``(uuid, persons)`` pair from the running Photos app.
    3. **photoscript fallback**. Used when osascript is missing or fails.

    Args:
        db: ProgressDB instance (faces must be scanned first via
            ``faces scan``).
        progress: Optional callable receiving status strings (banner +
            periodic heartbeat + final summary). When ``None`` the
            messages go to stderr by default — never silenced, because
            silence-by-default is exactly the bug this guards against.
        library_path: Optional path to a ``.photoslibrary`` for the
            osxphotos reader. ``None`` auto-detects the system library.

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

    name_to_uuids = _collect_name_to_uuids(emit, library_path=library_path)

    return _materialize_persons(db, name_to_uuids, emit)


def _collect_name_to_uuids(
    emit: Callable[[str], None], library_path: str | None = None
) -> dict[str, list[str]]:
    """Build the ``name -> [uuid, ...]`` map from the most reliable source.

    Prefers reading the Photos library DB via osxphotos (exact names, no
    AppleScript), then the bulk AppleScript against the running Photos app,
    then photoscript. Each path emits at least one progress line.
    """
    try:
        return _collect_via_osxphotos(emit, library_path)
    except _OsxphotosUnavailable as exc:
        logger.info("osxphotos path unavailable (%s); trying AppleScript", exc)

    if is_applescript_available():
        try:
            return _collect_via_bulk_applescript(emit)
        except _BulkAppleScriptUnavailable as exc:
            logger.warning("Bulk AppleScript path unavailable: %s — falling back", exc)

    if not _has_photoscript():
        raise RuntimeError(
            "Could not read the Photos library. Install the [photos-db] extra for the "
            "reliable reader (pip install 'pyimgtag[photos-db]'), or the [photos] extra "
            "(pip install 'pyimgtag[photos]') / run on macOS with osascript."
        )

    return _collect_via_photoscript(emit)


class _OsxphotosUnavailable(Exception):
    """Raised when the osxphotos DB reader can't run; caller falls back."""


class _BulkAppleScriptUnavailable(Exception):
    """Raised when the bulk AppleScript path can't run; caller falls back."""


def _collect_via_osxphotos(
    emit: Callable[[str], None], library_path: str | None = None
) -> dict[str, list[str]]:
    """Read ``name -> [photo uuid]`` straight from the Photos library DB.

    The reliable enumeration path: osxphotos reads the library's SQLite
    read-only (the Photos app need not be running) and returns each person's
    exact name and the UUIDs of their photos — no AppleScript (so it is immune
    to the ``-2741`` "Expected class name" failure) and no OCR. The UUIDs match
    the ``originals/X/<uuid>.<ext>`` stems that ``faces scan`` records, so the
    existing UUID linking in :func:`_assign_faces_to_person` ties faces to the
    named person.

    Raises:
        _OsxphotosUnavailable: osxphotos is not installed or the library
            cannot be opened — the caller then falls back to AppleScript.
    """
    try:
        import osxphotos
    except ImportError as exc:
        raise _OsxphotosUnavailable(
            "osxphotos not installed (pip install 'pyimgtag[photos-db]')"
        ) from exc

    emit("Reading the Apple Photos library database (osxphotos)…")
    try:
        db = osxphotos.PhotosDB(library_path) if library_path else osxphotos.PhotosDB()
    except Exception as exc:  # noqa: BLE001 — many failure modes; degrade to AppleScript
        raise _OsxphotosUnavailable(f"could not open Photos library: {exc}") from exc

    name_to_uuids: dict[str, list[str]] = {}
    for person in db.person_info:
        name = (getattr(person, "name", None) or "").strip()
        if not name or name == "_UNKNOWN_":
            continue
        uuids = [ph.uuid for ph in person.photos if getattr(ph, "uuid", None)]
        if uuids:
            name_to_uuids.setdefault(name, []).extend(uuids)
    emit(f"Photos library DB: {len(name_to_uuids)} named person(s).")
    return name_to_uuids


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


# Stderr substrings that identify the ``-2741`` "Expected class name but
# found identifier" compile failure; matched against osascript's stderr.
_PARSE_ERROR_MARKERS = ("(-2741)", "syntax error", "Expected class name")


def _run_bulk_osascript(script: str) -> "subprocess.CompletedProcess[str]":
    """Invoke osascript with ``script`` and return the completed process.

    Raises :class:`_BulkAppleScriptUnavailable` for failures the caller
    can't act on (timeout, missing binary). Non-zero exits are returned
    so the caller can inspect stderr and decide whether to retry with
    an alternate script.
    """
    try:
        return subprocess.run(  # noqa: S603  # nosec B603
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
        # Photos 5+ media-item ids are ``<UUID>/L0/001``; keep only the bare
        # UUID so ``get_faces_by_uuid`` can match image path stems.
        uuid = uuid.split("/", 1)[0]
        if not uuid:
            continue
        is_new = uuid not in seen_uuids
        if is_new:
            processed += 1
            seen_uuids.add(uuid)
        names = [n.strip() for n in raw_names.split(_PERSON_NAME_SEPARATOR) if n.strip()]
        for name in names:
            name_to_uuids.setdefault(name, []).append(uuid)
            persons_found.add(name)

        if is_new and processed % _PROGRESS_EVERY == 0:
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


def _assign_faces_to_person(
    db: ProgressDB,
    person_id: int,
    uuids: list[str],
    *,
    match_threshold: float = _MULTI_FACE_MATCH_THRESHOLD,
    margin: float = _MULTI_FACE_MATCH_MARGIN,
) -> int:
    """Assign this person's faces across their tagged photos.

    Single-face photos are unambiguous and assigned directly. Multi-face
    (group) photos are resolved by embedding similarity: the unassigned face
    closest to the person's reference centroid is linked, but only when it is
    within ``match_threshold`` *and* clearly closer than the next-best
    candidate (by ``margin``). Otherwise the photo is left for manual review —
    a wrong auto-assignment in someone's library is worse than an empty slot.

    The reference centroid is seeded from the person's existing assignments
    (covering re-import and the all-group-photo case) plus the single-face
    photos assigned in this pass, so a person who appears in group photos but
    has at least one solo/portrait shot still gets linked. When there is no
    usable reference at all, multi-face photos are skipped, not guessed.

    Candidate faces are those that are unassigned **or** sitting in an auto
    cluster (``trusted=0 AND confirmed=0``). The latter matters because the
    background re-cluster during a ``faces scan`` grabs freshly-detected faces
    into ``Person N`` clusters before ``import-photos`` runs; the Apple Photos
    UUID tag is authoritative, so such a face is reclaimed into the named
    person. Faces already assigned to a trusted/confirmed person are never
    touched. Returns the number of photos left unassigned (skipped) for manual
    review.
    """
    import numpy as np

    # Faces in an auto cluster may be reclaimed by an authoritative UUID match;
    # trusted/confirmed assignments must never be disturbed.
    reclaimable = db.get_auto_person_ids()

    # Collect this person's candidate faces (unassigned or auto-clustered),
    # grouped per photo, alongside each photo's *total* detected-face count —
    # "single-face photo" must mean one face in the photo, not one candidate.
    per_photo: list[tuple[str, list[int], int]] = []
    candidate_ids: list[int] = []
    for uuid in uuids:
        all_faces = db.get_faces_by_uuid(uuid)
        candidates = [
            f["id"] for f in all_faces if f["person_id"] is None or f["person_id"] in reclaimable
        ]
        if candidates:
            per_photo.append((uuid, candidates, len(all_faces)))
            candidate_ids.extend(candidates)
    if not per_photo:
        return 0

    embeddings = db.get_embeddings_for_faces(candidate_ids)
    # Reference embeddings: faces already confirmed for this person, grown with
    # the unambiguous single-face shots assigned below.
    seeds = db.get_person_embeddings(person_id)

    # Phase 1 — unambiguous single-face photos. Group photos with a single
    # *remaining* candidate are NOT unambiguous (the leftover face may belong
    # to a stranger), so they must pass the Phase 2 threshold+margin check.
    multi: list[tuple[str, list[int]]] = []
    for uuid, ids, total in per_photo:
        if total == 1:
            db.set_person_id(ids[0], person_id)
            if ids[0] in embeddings:
                seeds.append(embeddings[ids[0]])
        else:
            multi.append((uuid, ids))
    if not multi:
        return 0

    # Phase 2 — resolve group photos against a fixed reference centroid.
    centroid = np.mean(np.stack(seeds), axis=0) if seeds else None
    skipped = 0
    for uuid, ids in multi:
        ranked: list[tuple[int, float]] = []
        if centroid is not None:
            ranked = sorted(
                (
                    (fid, float(np.linalg.norm(embeddings[fid] - centroid)))
                    for fid in ids
                    if fid in embeddings
                ),
                key=lambda t: t[1],
            )
        if not ranked:
            skipped += 1
            logger.warning(
                "Photos person id=%d: photo %s has %d unassigned faces and no usable "
                "reference — leaving for manual review",
                person_id,
                uuid,
                len(ids),
            )
            continue
        best_id, best_dist = ranked[0]
        next_dist = ranked[1][1] if len(ranked) > 1 else float("inf")
        if best_dist <= match_threshold and (next_dist - best_dist) >= margin:
            db.set_person_id(best_id, person_id)
        else:
            skipped += 1
            logger.warning(
                "Photos person id=%d: photo %s ambiguous (closest dist=%.3f, "
                "threshold=%.2f, next=%.3f) — leaving for manual review",
                person_id,
                uuid,
                best_dist,
                match_threshold,
                next_dist,
            )
    return skipped


def _materialize_persons(
    db: ProgressDB,
    name_to_uuids: dict[str, list[str]],
    emit: Callable[[str], None],
) -> tuple[int, int]:
    """Create person rows + assign single-face photos. Pure DB work."""
    imported = 0
    skipped = 0

    for name, uuids in name_to_uuids.items():
        if db.has_photos_person(name):
            # Person already exists — check local DB for newly-scanned faces
            # that can now be assigned (covers the case where import-photos ran
            # before faces scan and left the person with 0 faces).
            existing_id = db.get_photos_person_id(name)
            if existing_id is not None:
                skipped += _assign_faces_to_person(db, existing_id, uuids)
            continue

        person_id = db.create_person(label=name, confirmed=True, source="photos", trusted=True)
        imported += 1
        skipped += _assign_faces_to_person(db, person_id, uuids)

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
