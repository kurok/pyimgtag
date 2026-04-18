"""Import named persons from Apple Photos into the faces DB.

Requires the [photos] extra: pip install pyimgtag[photos]
Only available on macOS with Apple Photos access.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    import photoscript
except ImportError:
    photoscript = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)


def import_photos_persons(db: ProgressDB) -> tuple[int, int]:
    """Import named persons from Apple Photos into the faces DB.

    For each named person in Apple Photos:
    - Creates a person row with source='photos', trusted=True, confirmed=True.
    - For photos with exactly one detected face: assigns that face to the person.
    - For photos with multiple detected faces: person is created but the photo's
      faces are left unassigned (logged as skipped — requires manual review).
    - Photos not yet in the faces DB are ignored.

    Args:
        db: ProgressDB instance (faces must be scanned first via 'faces scan').

    Returns:
        Tuple of (imported_count, skipped_count) where imported_count is the
        number of person rows created and skipped_count is the number of
        multi-face photos that could not be auto-assigned.

    Raises:
        RuntimeError: If photoscript is not installed.
    """
    if photoscript is None:
        raise RuntimeError(
            "photoscript is not installed. Install the [photos] extra: pip install pyimgtag[photos]"
        )

    library = photoscript.PhotosLibrary()
    imported = 0
    skipped = 0

    for person in library.persons():
        name: str = person.name or ""
        if not name.strip():
            continue

        # Skip if already imported from Photos (idempotency)
        existing = db._conn.execute(
            "SELECT id FROM persons WHERE label = ? AND source = 'photos'",
            (name,),
        ).fetchone()
        if existing is not None:
            continue

        person_id = db.create_person(label=name, confirmed=True, source="photos", trusted=True)
        imported += 1

        for photo in person.photos():
            uuid = photo.uuid
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
