"""Import named persons from Apple Photos into the faces DB.

Requires the [photos] extra: pip install pyimgtag[photos]
Only available on macOS with Apple Photos access.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _has_photoscript() -> bool:
    """Return True if photoscript is installed (checked via find_spec, cached).

    Uses importlib.util.find_spec() to avoid triggering module execution,
    which can crash the process on some macOS configurations.
    """
    import importlib.util

    return importlib.util.find_spec("photoscript") is not None


def import_photos_persons(db: ProgressDB) -> tuple[int, int]:
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

    The photoscript ``PhotosLibrary`` object does not expose a
    ``persons()`` / ``people()`` method, so we walk the library's
    photos and read each photo's ``persons`` attribute (a list of
    name strings) to discover every named person that appears in any
    photo. This is O(library size) rather than O(named persons), but
    photoscript offers no faster, supported alternative.

    Args:
        db: ProgressDB instance (faces must be scanned first via
            ``faces scan``).

    Returns:
        Tuple of ``(imported_count, skipped_count)`` where
        ``imported_count`` is the number of person rows created and
        ``skipped_count`` is the number of multi-face photos that
        could not be auto-assigned.

    Raises:
        RuntimeError: If photoscript is not installed.
    """
    if not _has_photoscript():
        raise RuntimeError(
            "photoscript is not installed. Install the [photos] extra: pip install pyimgtag[photos]"
        )

    import photoscript  # local import; only runs when photoscript is actually available

    library = photoscript.PhotosLibrary()
    imported = 0
    skipped = 0

    # Phase 1: walk every photo, collecting (name, uuid) pairs. Errors
    # accessing a single photo are logged and skipped so one bad row
    # doesn't take down the whole import.
    name_to_uuids: dict[str, list[str]] = {}
    photos = _list_photos(library)
    for photo in photos:
        names = _photo_person_names(photo)
        if not names:
            continue
        try:
            uuid = photo.uuid
        except Exception as exc:  # noqa: BLE001 — photoscript wraps AppleScript errors
            logger.debug("Skipping photo with unreadable uuid: %s", exc)
            continue
        for name in names:
            cleaned = name.strip()
            if cleaned:
                name_to_uuids.setdefault(cleaned, []).append(uuid)

    # Phase 2: create one person row per distinct name, then assign the
    # uniquely-identifiable single-face photos.
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
