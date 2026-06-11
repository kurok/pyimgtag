"""Face domain: detections, embeddings, person clusters, scan cache.

Owns queries against the ``faces``, ``persons``, and ``face_scanned_images``
tables (schema and migrations live in
:class:`pyimgtag.db.progress_db.ProgressDB`).
"""

from __future__ import annotations

import sqlite3
import struct
from collections import defaultdict
from typing import TYPE_CHECKING

from pyimgtag.models import FaceDetection, PersonCluster

if TYPE_CHECKING:
    import numpy as np


class FaceDB:
    """Face/person-cluster queries over a shared SQLite connection.

    The connection (including schema and migrations) is owned by
    :class:`pyimgtag.db.progress_db.ProgressDB`; this class only issues
    domain queries against it.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the domain helper to the facade's open connection."""
        self._conn = conn

    @staticmethod
    def _embedding_to_blob(embedding: np.ndarray) -> bytes:
        """Pack a 128-d float64 numpy array into a compact bytes blob."""
        return struct.pack(f"{len(embedding)}d", *embedding.tolist())

    @staticmethod
    def _blob_to_embedding(blob: bytes) -> np.ndarray:
        """Unpack a blob back into a numpy float64 array."""
        import numpy as np

        count = len(blob) // struct.calcsize("d")
        return np.array(struct.unpack(f"{count}d", blob), dtype=np.float64)

    def insert_face(
        self,
        image_path: str,
        detection: FaceDetection,
        embedding: np.ndarray | None = None,
    ) -> int:
        """Insert a detected face.

        Args:
            image_path: Path to the source image.
            detection: Bounding box and confidence for the face.
            embedding: Optional face encoding — a 1-D float64 numpy array
                (128-d in practice) stored as a packed float64 blob.

        Returns:
            The new face row id.
        """
        blob = self._embedding_to_blob(embedding) if embedding is not None else None
        cur = self._conn.execute(
            """INSERT INTO faces (image_path, bbox_x, bbox_y, bbox_w, bbox_h, confidence, embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                image_path,
                detection.bbox_x,
                detection.bbox_y,
                detection.bbox_w,
                detection.bbox_h,
                detection.confidence,
                blob,
            ),
        )
        self._conn.commit()
        if cur.lastrowid is None:
            raise RuntimeError("INSERT into faces did not return a row id")
        return cur.lastrowid

    def get_faces_by_uuid(self, uuid: str) -> list[dict]:
        """Return all face rows whose image path stem matches the given UUID.

        Handles both full paths (``/library/abc123.jpg``) and bare filenames
        (``abc123.jpg``) by using two LIKE patterns.
        """
        rows = self._conn.execute(
            "SELECT id, image_path, bbox_x, bbox_y, bbox_w, bbox_h, confidence, person_id "
            "FROM faces WHERE image_path LIKE ? OR image_path LIKE ?",
            (f"%/{uuid}.%", f"{uuid}.%"),
        ).fetchall()
        return [
            {
                "id": r[0],
                "image_path": r[1],
                "bbox_x": r[2],
                "bbox_y": r[3],
                "bbox_w": r[4],
                "bbox_h": r[5],
                "confidence": r[6],
                "person_id": r[7],
            }
            for r in rows
        ]

    def has_photos_person(self, label: str) -> bool:
        """Return True if a person with this label was already imported from Photos."""
        return (
            self._conn.execute(
                "SELECT 1 FROM persons WHERE label = ? AND source = 'photos'",
                (label,),
            ).fetchone()
            is not None
        )

    def get_photos_person_id(self, label: str) -> int | None:
        """Return the person_id for an already-imported Photos.app person, or None."""
        row = self._conn.execute(
            "SELECT id FROM persons WHERE label = ? AND source = 'photos'",
            (label,),
        ).fetchone()
        return row[0] if row else None

    def mark_face_scanned(self, image_path: str) -> None:
        """Record that an image has been fully face-scanned (even if 0 faces found)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO face_scanned_images (image_path) VALUES (?)",
            (image_path,),
        )
        self._conn.commit()

    def is_face_scanned(self, image_path: str) -> bool:
        """Return True if the image has already been face-scanned."""
        return bool(
            self._conn.execute(
                "SELECT 1 FROM face_scanned_images WHERE image_path = ?", (image_path,)
            ).fetchone()
        )

    def get_faces_for_image(self, image_path: str) -> list[dict]:
        """Return all face rows for an image path."""
        rows = self._conn.execute(
            "SELECT id, bbox_x, bbox_y, bbox_w, bbox_h, confidence, person_id "
            "FROM faces WHERE image_path = ?",
            (image_path,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "bbox_x": r[1],
                "bbox_y": r[2],
                "bbox_w": r[3],
                "bbox_h": r[4],
                "confidence": r[5],
                "person_id": r[6],
            }
            for r in rows
        ]

    def get_all_embeddings(self) -> list[tuple[int, np.ndarray]]:
        """Return (face_id, embedding) for all faces that have embeddings."""
        rows = self._conn.execute(
            "SELECT id, embedding FROM faces WHERE embedding IS NOT NULL"
        ).fetchall()
        return [(r[0], self._blob_to_embedding(r[1])) for r in rows]

    def get_clusterable_embeddings(self) -> list[tuple[int, np.ndarray]]:
        """Return (face_id, embedding) for faces eligible for auto-clustering.

        Only unassigned, non-ignored faces are returned. Faces already assigned
        to a person (notably trusted / Photos-imported or manually confirmed
        people) and trashed faces are excluded, so clustering never steals a
        face away from a named person nor pulls a dismissed face back into a
        cluster. ``clear_auto_persons`` releases auto-cluster faces back to the
        unassigned pool before a recluster, so this set is exactly the faces
        that should be (re)grouped.
        """
        rows = self._conn.execute(
            "SELECT id, embedding FROM faces "
            "WHERE embedding IS NOT NULL AND person_id IS NULL AND ignored = 0"
        ).fetchall()
        return [(r[0], self._blob_to_embedding(r[1])) for r in rows]

    def get_embeddings_for_faces(self, face_ids: list[int]) -> dict[int, np.ndarray]:
        """Return ``{face_id: embedding}`` for the given ids that have one.

        Ids without a stored embedding (or unknown ids) are simply absent from
        the result, so callers can look up by membership.
        """
        if not face_ids:
            return {}
        placeholders = ",".join("?" * len(face_ids))
        rows = self._conn.execute(
            f"SELECT id, embedding FROM faces WHERE id IN ({placeholders}) "  # nosec B608
            "AND embedding IS NOT NULL",
            list(face_ids),
        ).fetchall()
        return {r[0]: self._blob_to_embedding(r[1]) for r in rows}

    def get_person_embeddings(self, person_id: int) -> list[np.ndarray]:
        """Return the embeddings of the faces currently assigned to a person."""
        rows = self._conn.execute(
            "SELECT embedding FROM faces WHERE person_id = ? AND embedding IS NOT NULL",
            (person_id,),
        ).fetchall()
        return [self._blob_to_embedding(r[0]) for r in rows]

    def set_person_id(self, face_id: int, person_id: int) -> None:
        """Assign a face to a person cluster."""
        self._conn.execute("UPDATE faces SET person_id = ? WHERE id = ?", (person_id, face_id))
        self._conn.commit()

    def create_person(
        self,
        label: str = "",
        confirmed: bool = False,
        source: str = "auto",
        trusted: bool = False,
    ) -> int:
        """Create a new person entry. Returns the person id."""
        cur = self._conn.execute(
            "INSERT INTO persons (label, confirmed, source, trusted) VALUES (?, ?, ?, ?)",
            (label, 1 if confirmed else 0, source, 1 if trusted else 0),
        )
        self._conn.commit()
        if cur.lastrowid is None:
            raise RuntimeError("INSERT into persons did not return a row id")
        return cur.lastrowid

    def get_persons(self) -> list[PersonCluster]:
        """Return all persons with their assigned face ids."""
        persons = self._conn.execute(
            "SELECT id, label, confirmed, source, trusted FROM persons"
        ).fetchall()
        # Fetch all face->person mappings in a single round-trip.
        face_rows = self._conn.execute(
            "SELECT person_id, id FROM faces WHERE person_id IS NOT NULL"
        ).fetchall()
        faces_by_person: dict[int, list[int]] = defaultdict(list)
        for person_id, face_id in face_rows:
            faces_by_person[person_id].append(face_id)
        result = []
        for pid, label, confirmed, source, trusted in persons:
            result.append(
                PersonCluster(
                    person_id=pid,
                    label=label,
                    confirmed=bool(confirmed),
                    face_ids=faces_by_person[pid],
                    source=source or "auto",
                    trusted=bool(trusted),
                )
            )
        return result

    def update_person_label(self, person_id: int, label: str) -> None:
        """Update the display label for a person.

        A non-empty label is treated as a manual confirmation — the person is
        also marked ``confirmed=1, trusted=1`` so it survives re-clustering.
        """
        if label:
            self._conn.execute(
                "UPDATE persons SET label = ?, confirmed = 1, trusted = 1 WHERE id = ?",
                (label, person_id),
            )
        else:
            self._conn.execute("UPDATE persons SET label = ? WHERE id = ?", (label, person_id))
        self._conn.commit()

    def confirm_person(self, person_id: int) -> None:
        """Mark a person cluster as confirmed and trusted.

        Confirmed persons survive ``clear_auto_persons`` re-clustering and
        their badge changes from AUTO → TRUSTED in the UI.
        """
        self._conn.execute(
            "UPDATE persons SET confirmed = 1, trusted = 1 WHERE id = ?", (person_id,)
        )
        self._conn.commit()

    def merge_persons(self, source_id: int, target_id: int) -> None:
        """Reassign all faces from source_id to target_id, then delete source_id.

        A non-existent ``source_id`` is a no-op (the UPDATE/DELETE simply affect
        zero rows).

        Raises:
            ValueError: If ``target_id`` does not exist.
        """
        # Merging a person into itself would reassign its faces to the same id
        # (a no-op) and then delete that very person — orphaning every face.
        # Treat it as a no-op instead.
        if source_id == target_id:
            return
        if not self._conn.execute("SELECT 1 FROM persons WHERE id = ?", (target_id,)).fetchone():
            raise ValueError(f"merge target person {target_id} does not exist")
        self._conn.execute(
            "UPDATE faces SET person_id = ? WHERE person_id = ?", (target_id, source_id)
        )
        self._conn.execute("DELETE FROM persons WHERE id = ?", (source_id,))
        self._conn.commit()

    def delete_person(self, person_id: int) -> None:
        """Delete a person and set person_id to NULL on all their faces."""
        self._conn.execute("UPDATE faces SET person_id = NULL WHERE person_id = ?", (person_id,))
        self._conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        self._conn.commit()

    def confirm_persons(self, person_ids: list[int]) -> int:
        """Mark several person clusters as confirmed and trusted in one transaction.

        Args:
            person_ids: Person ids to confirm. An empty list is a no-op.

        Returns:
            The number of person rows updated.
        """
        if not person_ids:
            return 0
        placeholders = ",".join("?" * len(person_ids))
        cur = self._conn.execute(
            f"UPDATE persons SET confirmed = 1, trusted = 1 WHERE id IN ({placeholders})",  # nosec B608
            person_ids,
        )
        self._conn.commit()
        return cur.rowcount

    def delete_persons(self, person_ids: list[int]) -> int:
        """Delete several persons and unassign their faces in one transaction.

        Args:
            person_ids: Person ids to delete. An empty list is a no-op.

        Returns:
            The number of person rows deleted.
        """
        if not person_ids:
            return 0
        placeholders = ",".join("?" * len(person_ids))
        self._conn.execute(
            f"UPDATE faces SET person_id = NULL WHERE person_id IN ({placeholders})",  # nosec B608
            person_ids,
        )
        cur = self._conn.execute(
            f"DELETE FROM persons WHERE id IN ({placeholders})",  # nosec B608
            person_ids,
        )
        self._conn.commit()
        return cur.rowcount

    def clear_auto_persons(self) -> None:
        """Delete all auto-clustered persons that are not trusted or confirmed.

        Resets person_id to NULL on their faces so they can be re-clustered.
        Persons imported from Photos (trusted=1) or manually confirmed are preserved.
        """
        auto_ids = [
            r[0]
            for r in self._conn.execute(
                "SELECT id FROM persons WHERE trusted = 0 AND confirmed = 0"
            ).fetchall()
        ]
        if not auto_ids:
            return
        placeholders = ",".join("?" * len(auto_ids))
        self._conn.execute(
            f"UPDATE faces SET person_id = NULL WHERE person_id IN ({placeholders})",  # nosec B608
            auto_ids,
        )
        self._conn.execute(f"DELETE FROM persons WHERE id IN ({placeholders})", auto_ids)  # nosec B608
        self._conn.commit()

    def reset_all_faces(self, *, dry_run: bool = False) -> dict[str, int]:
        """Delete every face, every person, and the face-scan cache.

        The most destructive faces reset: a subsequent ``faces scan`` starts
        from zero, re-detecting and re-clustering everything. Trusted and
        Photos-imported persons are **not** spared. Image tagging/geocoding
        progress in ``processed_images`` is untouched.

        Args:
            dry_run: When True, only count what would be removed; delete nothing.

        Returns:
            ``{"faces": n, "persons": n, "scanned_images": n}`` — rows removed
            (or that would be removed, for ``dry_run``).
        """
        counts = {
            "faces": self._conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0],
            "persons": self._conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0],
            "scanned_images": self._conn.execute(
                "SELECT COUNT(*) FROM face_scanned_images"
            ).fetchone()[0],
        }
        if dry_run:
            return counts
        self._conn.execute("DELETE FROM faces")
        self._conn.execute("DELETE FROM persons")
        self._conn.execute("DELETE FROM face_scanned_images")
        self._conn.commit()
        return counts

    def reset_untrusted_faces(self, *, dry_run: bool = False) -> dict[str, int]:
        """Delete non-trusted faces and clusters, preserving trusted people.

        Removes every person that is neither trusted nor confirmed, and every
        non-ignored face not assigned to a surviving trusted person, then
        prunes the face-scan cache for images that have no face left so the
        next scan re-detects them. Kept: trusted / Photos-imported persons and
        the faces assigned to them, AND ignored ("trash") faces — those are
        explicit user curation, so they survive here; use ``reset_all_faces``
        to clear them too. (An image that still holds a trusted or trashed
        face stays cached, so its faces are not re-detected — nor the kept
        face duplicated — on the next scan.)

        Args:
            dry_run: When True, only count what would be removed; delete nothing.

        Returns:
            ``{"faces": n, "persons": n, "scanned_images": n}``.
        """
        # All SQL below is constant (no interpolated values). A face is deleted
        # only when it is NOT ignored AND not assigned to a trusted/confirmed
        # person. The scan-cache survivor set is images that still have a
        # trusted OR an ignored face after the delete.
        counts = {
            "faces": self._conn.execute(
                "SELECT COUNT(*) FROM faces WHERE ignored = 0 AND (person_id IS NULL "
                "OR person_id NOT IN (SELECT id FROM persons WHERE trusted = 1 OR confirmed = 1))"
            ).fetchone()[0],
            "persons": self._conn.execute(
                "SELECT COUNT(*) FROM persons WHERE trusted = 0 AND confirmed = 0 "
                "AND id NOT IN (SELECT person_id FROM faces "
                "WHERE ignored = 1 AND person_id IS NOT NULL)"
            ).fetchone()[0],
            "scanned_images": self._conn.execute(
                "SELECT COUNT(*) FROM face_scanned_images WHERE image_path NOT IN "
                "(SELECT DISTINCT image_path FROM faces WHERE ignored = 1 OR person_id IN "
                "(SELECT id FROM persons WHERE trusted = 1 OR confirmed = 1))"
            ).fetchone()[0],
        }
        if dry_run:
            return counts
        self._conn.execute(
            "DELETE FROM faces WHERE ignored = 0 AND (person_id IS NULL "
            "OR person_id NOT IN (SELECT id FROM persons WHERE trusted = 1 OR confirmed = 1))"
        )
        # Keep any untrusted person that still owns a surviving (ignored/trash)
        # face — deleting it would leave that face pointing at a non-existent
        # person (SQLite FK enforcement is off, so the dangling row is silent).
        # ``ignored = 1`` faces are never touched by the face delete above, so
        # this predicate matches the count computed before the deletes.
        self._conn.execute(
            "DELETE FROM persons WHERE trusted = 0 AND confirmed = 0 "
            "AND id NOT IN (SELECT person_id FROM faces "
            "WHERE ignored = 1 AND person_id IS NOT NULL)"
        )
        # Only trusted and ignored faces survive now, so "no face left" is just
        # "no face row left" for the image.
        self._conn.execute(
            "DELETE FROM face_scanned_images WHERE image_path NOT IN "
            "(SELECT DISTINCT image_path FROM faces)"
        )
        self._conn.commit()
        return counts

    def count_auto_persons(self) -> int:
        """Return the number of auto-clustered persons (trusted=0 AND confirmed=0)."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM persons WHERE trusted = 0 AND confirmed = 0"
        ).fetchone()[0]

    def get_auto_person_ids(self) -> set[int]:
        """Return the ids of auto-clustered persons (trusted=0 AND confirmed=0).

        These are the only assignments a UUID-authoritative re-link (Photos
        import) may reclaim a face from; trusted/confirmed assignments are
        never disturbed.
        """
        return {
            r[0]
            for r in self._conn.execute(
                "SELECT id FROM persons WHERE trusted = 0 AND confirmed = 0"
            ).fetchall()
        }

    def ignore_face(self, face_id: int) -> None:
        """Mark a face as ignored so it is excluded from auto-clustering."""
        self._conn.execute(
            "UPDATE faces SET ignored = 1, person_id = NULL WHERE id = ?", (face_id,)
        )
        self._conn.commit()

    def restore_face(self, face_id: int) -> None:
        """Remove the ignored flag from a face, returning it to the unassigned pool."""
        self._conn.execute("UPDATE faces SET ignored = 0 WHERE id = ?", (face_id,))
        self._conn.commit()

    def get_ignored_faces(self) -> list[dict]:
        """Return all faces marked as ignored (the trash bin)."""
        rows = self._conn.execute(
            "SELECT id, image_path, bbox_x, bbox_y, bbox_w, bbox_h, confidence "
            "FROM faces WHERE ignored = 1"
        ).fetchall()
        return [
            {
                "id": r[0],
                "image_path": r[1],
                "bbox_x": r[2],
                "bbox_y": r[3],
                "bbox_w": r[4],
                "bbox_h": r[5],
                "confidence": r[6],
            }
            for r in rows
        ]

    def get_unassigned_faces(self) -> list[dict]:
        """Return faces that have no person assignment and are not ignored."""
        rows = self._conn.execute(
            "SELECT id, image_path, bbox_x, bbox_y, bbox_w, bbox_h, confidence "
            "FROM faces WHERE person_id IS NULL AND ignored = 0"
        ).fetchall()
        return [
            {
                "id": r[0],
                "image_path": r[1],
                "bbox_x": r[2],
                "bbox_y": r[3],
                "bbox_w": r[4],
                "bbox_h": r[5],
                "confidence": r[6],
            }
            for r in rows
        ]

    def unassign_face(self, face_id: int) -> None:
        """Remove the person assignment from a face."""
        self._conn.execute("UPDATE faces SET person_id = NULL WHERE id = ?", (face_id,))
        self._conn.commit()

    def get_faces_for_person(self, person_id: int) -> list[dict]:
        """Return all faces assigned to a person."""
        rows = self._conn.execute(
            "SELECT id, image_path, bbox_x, bbox_y, bbox_w, bbox_h, confidence "
            "FROM faces WHERE person_id = ?",
            (person_id,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "image_path": r[1],
                "bbox_x": r[2],
                "bbox_y": r[3],
                "bbox_w": r[4],
                "bbox_h": r[5],
                "confidence": r[6],
            }
            for r in rows
        ]

    def get_assigned_faces(self) -> list[dict]:
        """Return all faces that have a person assignment."""
        rows = self._conn.execute(
            "SELECT id, image_path FROM faces WHERE person_id IS NOT NULL"
        ).fetchall()
        return [{"id": r[0], "image_path": r[1]} for r in rows]

    def get_face_count(self) -> int:
        """Return total number of detected faces."""
        return self._conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]

    def get_face_by_id(self, face_id: int) -> dict | None:
        """Return a single face record by id, or None if not found."""
        row = self._conn.execute(
            "SELECT id, image_path, bbox_x, bbox_y, bbox_w, bbox_h, confidence "
            "FROM faces WHERE id = ?",
            (face_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "image_path": row[1],
            "bbox_x": row[2],
            "bbox_y": row[3],
            "bbox_w": row[4],
            "bbox_h": row[5],
            "confidence": row[6],
        }
