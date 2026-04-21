"""SQLite progress database for incremental image processing."""

from __future__ import annotations

import json
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pyimgtag.models import FaceDetection, ImageResult, PersonCluster

if TYPE_CHECKING:
    import numpy as np

    from pyimgtag.models import JudgeResult


class ProgressDB:
    """Track which images have been processed to enable incremental re-runs."""

    # Each entry is (target_version, sql_statement).
    # Version 1 is the baseline schema created by _create_table().
    # Migrations are applied in ascending version order on every open.
    _MIGRATIONS: tuple[tuple[int, str], ...] = (
        (2, "ALTER TABLE processed_images ADD COLUMN scene_category TEXT"),
        (2, "ALTER TABLE processed_images ADD COLUMN emotional_tone TEXT"),
        (2, "ALTER TABLE processed_images ADD COLUMN cleanup_class TEXT"),
        (2, "ALTER TABLE processed_images ADD COLUMN has_text INTEGER DEFAULT 0"),
        (2, "ALTER TABLE processed_images ADD COLUMN text_summary TEXT"),
        (2, "ALTER TABLE processed_images ADD COLUMN event_hint TEXT"),
        (2, "ALTER TABLE processed_images ADD COLUMN significance TEXT"),
        (
            3,
            """CREATE TABLE IF NOT EXISTS persons (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                label     TEXT NOT NULL DEFAULT '',
                confirmed INTEGER NOT NULL DEFAULT 0
            )""",
        ),
        (
            3,
            """CREATE TABLE IF NOT EXISTS faces (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                image_path TEXT NOT NULL,
                bbox_x     INTEGER NOT NULL DEFAULT 0,
                bbox_y     INTEGER NOT NULL DEFAULT 0,
                bbox_w     INTEGER NOT NULL DEFAULT 0,
                bbox_h     INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0.0,
                embedding  BLOB,
                person_id  INTEGER REFERENCES persons(id)
            )""",
        ),
        (3, "CREATE INDEX IF NOT EXISTS idx_faces_image ON faces(image_path)"),
        (3, "CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id)"),
        (4, "ALTER TABLE processed_images ADD COLUMN nearest_city TEXT"),
        (4, "ALTER TABLE processed_images ADD COLUMN nearest_region TEXT"),
        (4, "ALTER TABLE processed_images ADD COLUMN nearest_country TEXT"),
        (
            5,
            """CREATE TABLE IF NOT EXISTS judge_scores (
                file_path          TEXT PRIMARY KEY,
                scored_at          TEXT NOT NULL,
                weighted_score     REAL NOT NULL,
                core_score         REAL NOT NULL,
                visible_score      REAL NOT NULL,
                verdict            TEXT,
                impact             REAL,
                story_subject      REAL,
                composition_center REAL,
                lighting           REAL,
                creativity_style   REAL,
                color_mood         REAL,
                presentation_crop  REAL,
                technical_excellence REAL,
                focus_sharpness    REAL,
                exposure_tonal     REAL,
                noise_cleanliness  REAL,
                subject_separation REAL,
                edit_integrity     REAL
            )""",
        ),
        (6, "ALTER TABLE persons ADD COLUMN source TEXT NOT NULL DEFAULT 'auto'"),
        (6, "ALTER TABLE persons ADD COLUMN trusted INTEGER NOT NULL DEFAULT 0"),
    )

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".cache" / "pyimgtag" / "progress.db"
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()

    def _create_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_images (
                file_path    TEXT PRIMARY KEY,
                file_size    INTEGER,
                file_mtime   REAL,
                tags         TEXT,
                scene_summary TEXT,
                processed_at TEXT,
                status       TEXT,
                error_message TEXT
            )
            """
        )
        self._conn.commit()
        # Mark a brand-new database (user_version == 0) as version 1 so that
        # existing migration entries for version > 1 still apply on first open.
        current: int = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if current == 0:
            self._conn.execute("PRAGMA user_version = 1")
        self._migrate()

    def _migrate(self) -> None:
        """Apply any pending versioned migrations and update user_version."""
        current: int = self._conn.execute("PRAGMA user_version").fetchone()[0]
        # Collect all target versions that are still pending.
        pending_versions = sorted({ver for ver, _ in self._MIGRATIONS if ver > current})
        if not pending_versions:
            return
        for target_ver in pending_versions:
            stmts = [sql for ver, sql in self._MIGRATIONS if ver == target_ver]
            for sql in stmts:
                try:
                    self._conn.execute(sql)
                except sqlite3.OperationalError as exc:
                    # Tolerate "duplicate column name" so re-running migrations
                    # on an already-migrated DB is safe.
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if not isinstance(target_ver, int):
                raise TypeError(f"Migration version must be int, got {type(target_ver)}")
            self._conn.execute(f"PRAGMA user_version = {target_ver}")  # nosec B608
        self._conn.commit()

    def is_processed(self, file_path: Path) -> bool:
        """Return True if the file was already successfully processed and hasn't changed.

        Rows with status != 'ok' (e.g. transient Ollama failures) are treated as
        not processed so they will be retried on the next run.
        """
        row = self._conn.execute(
            "SELECT file_size, file_mtime, status FROM processed_images WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
        if row is None:
            return False
        if row[2] != "ok":
            return False
        try:
            stat = file_path.stat()
        except OSError:
            return False
        return row[0] == stat.st_size and row[1] == stat.st_mtime

    def mark_done(self, file_path: Path, result: ImageResult) -> None:
        """Record a processed image result."""
        try:
            stat = file_path.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            size = 0
            mtime = 0.0
        self._conn.execute(
            """
            INSERT OR REPLACE INTO processed_images
                (file_path, file_size, file_mtime, tags, scene_summary,
                 processed_at, status, error_message,
                 scene_category, emotional_tone, cleanup_class, has_text,
                 text_summary, event_hint, significance,
                 nearest_city, nearest_region, nearest_country)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(file_path),
                size,
                mtime,
                json.dumps(result.tags),
                result.scene_summary,
                datetime.now(timezone.utc).isoformat(),
                result.processing_status,
                result.error_message,
                result.scene_category,
                result.emotional_tone,
                result.cleanup_class,
                1 if result.has_text else 0,
                result.text_summary,
                result.event_hint,
                result.significance,
                result.nearest_city,
                result.nearest_region,
                result.nearest_country,
            ),
        )
        self._conn.commit()

    def get_stats(self) -> dict:
        """Return counts of processed images by status."""
        total = self._conn.execute("SELECT COUNT(*) FROM processed_images").fetchone()[0]
        ok = self._conn.execute(
            "SELECT COUNT(*) FROM processed_images WHERE status = 'ok'"
        ).fetchone()[0]
        error = self._conn.execute(
            "SELECT COUNT(*) FROM processed_images WHERE status = 'error'"
        ).fetchone()[0]
        return {"total": total, "ok": ok, "error": error}

    def reset_all(self) -> int:
        """Delete all rows from the processed_images table. Returns count deleted."""
        cur = self._conn.execute("SELECT COUNT(*) FROM processed_images")
        count = cur.fetchone()[0]
        self._conn.execute("DELETE FROM processed_images")
        self._conn.commit()
        return count

    def reset_by_status(self, status: str) -> int:
        """Delete rows with the given status (e.g. 'error'). Returns count deleted."""
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM processed_images WHERE status = ?", (status,)
        )
        count = cur.fetchone()[0]
        self._conn.execute("DELETE FROM processed_images WHERE status = ?", (status,))
        self._conn.commit()
        return count

    def query_images(
        self,
        tag: str | None = None,
        has_text: bool | None = None,
        cleanup_class: str | None = None,
        scene_category: str | None = None,
        city: str | None = None,
        country: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Query images with advanced filters.

        Args:
            tag: Case-insensitive substring match against any tag value.
            has_text: True = only images with text; False = only without; None = any.
            cleanup_class: Exact match against cleanup_class ('delete', 'review', etc.).
            scene_category: Exact match against scene_category.
            city: Case-insensitive substring match against nearest_city.
            country: Case-insensitive substring match against nearest_country.
            status: Exact match against status ('ok', 'error').
            limit: Max rows to return. None = no limit.

        Returns:
            List of image metadata dicts.
        """
        conditions: list[str] = []
        params: list[object] = []

        if tag is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM json_each(tags) WHERE LOWER(value) LIKE LOWER(?))"
            )
            params.append(f"%{tag}%")
        if has_text is True:
            conditions.append("has_text = 1")
        elif has_text is False:
            conditions.append("(has_text = 0 OR has_text IS NULL)")
        if cleanup_class is not None:
            conditions.append("cleanup_class = ?")
            params.append(cleanup_class)
        if scene_category is not None:
            conditions.append("scene_category = ?")
            params.append(scene_category)
        if city is not None:
            conditions.append("LOWER(nearest_city) LIKE LOWER(?)")
            params.append(f"%{city}%")
        if country is not None:
            conditions.append("LOWER(nearest_country) LIKE LOWER(?)")
            params.append(f"%{country}%")
        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
        query = (  # nosec B608
            "SELECT file_path, tags, scene_summary, processed_at, status, "
            "cleanup_class, scene_category, emotional_tone, event_hint, significance, "
            "nearest_city, nearest_region, nearest_country "
            "FROM processed_images "
            + where  # nosec B608
            + " ORDER BY file_path "
            + limit_clause
        )
        rows = self._conn.execute(query, params).fetchall()
        return [self._query_row_to_dict(r) for r in rows]

    @staticmethod
    def _query_row_to_dict(row: tuple) -> dict:
        """Convert a query_images SELECT row (13 cols) to a metadata dict."""
        file_path: str = row[0]
        tags_raw: str | None = row[1]
        try:
            tags_list: list[str] = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags_list = []
        return {
            "file_path": file_path,
            "file_name": Path(file_path).name,
            "tags_list": tags_list,
            "scene_summary": row[2],
            "processed_at": row[3],
            "status": row[4],
            "cleanup_class": row[5],
            "scene_category": row[6],
            "emotional_tone": row[7],
            "event_hint": row[8],
            "significance": row[9],
            "nearest_city": row[10],
            "nearest_region": row[11],
            "nearest_country": row[12],
        }

    def get_tag_counts(self) -> list[tuple[str, int]]:
        """Return (tag, count) pairs sorted by count descending.

        Counts how many images have each distinct tag.

        Returns:
            List of (tag_name, image_count) tuples.
        """
        rows = self._conn.execute(
            "SELECT LOWER(value), COUNT(*) AS cnt "
            "FROM processed_images, json_each(tags) "
            "WHERE tags IS NOT NULL "
            "GROUP BY LOWER(value) "
            "ORDER BY cnt DESC, LOWER(value)"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def rename_tag(self, old_tag: str, new_tag: str) -> int:
        """Rename a tag across all images.

        Replaces every occurrence of *old_tag* (case-insensitive) with *new_tag*
        (lowercased). Images that already contain *new_tag* are de-duplicated so
        the tag only appears once.

        Args:
            old_tag: The tag to rename.
            new_tag: The replacement tag (will be lowercased).

        Returns:
            Number of images updated.
        """
        old_lower = old_tag.lower()
        new_lower = new_tag.lower()
        rows = self._conn.execute(
            "SELECT file_path, tags FROM processed_images WHERE tags IS NOT NULL"
        ).fetchall()
        updated = 0
        with self._conn:
            for file_path, tags_raw in rows:
                try:
                    tags: list[str] = json.loads(tags_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                tags_lower = [t.lower() for t in tags]
                if old_lower not in tags_lower:
                    continue
                new_tags: list[str] = []
                seen: set[str] = set()
                for t in tags:
                    val = t.lower()
                    replacement = new_lower if val == old_lower else val
                    if replacement not in seen:
                        seen.add(replacement)
                        new_tags.append(replacement)
                self._conn.execute(
                    "UPDATE processed_images SET tags = ? WHERE file_path = ?",
                    (json.dumps(new_tags), file_path),
                )
                updated += 1
        return updated

    def delete_tag(self, tag: str) -> int:
        """Remove a tag from all images that have it.

        Args:
            tag: The tag to delete (case-insensitive match).

        Returns:
            Number of images updated.
        """
        tag_lower = tag.lower()
        rows = self._conn.execute(
            "SELECT file_path, tags FROM processed_images WHERE tags IS NOT NULL"
        ).fetchall()
        updated = 0
        with self._conn:
            for file_path, tags_raw in rows:
                try:
                    tags: list[str] = json.loads(tags_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                new_tags = [t for t in tags if t.lower() != tag_lower]
                if len(new_tags) == len(tags):
                    continue
                self._conn.execute(
                    "UPDATE processed_images SET tags = ? WHERE file_path = ?",
                    (json.dumps(new_tags), file_path),
                )
                updated += 1
        return updated

    def merge_tags(self, source_tag: str, target_tag: str) -> int:
        """Merge *source_tag* into *target_tag*.

        For every image that has *source_tag*, removes it and adds *target_tag*
        (if not already present). The *target_tag* is lowercased.

        Args:
            source_tag: The tag to replace (case-insensitive match).
            target_tag: The tag to add (will be lowercased).

        Returns:
            Number of images updated.
        """
        src_lower = source_tag.lower()
        tgt_lower = target_tag.lower()
        rows = self._conn.execute(
            "SELECT file_path, tags FROM processed_images WHERE tags IS NOT NULL"
        ).fetchall()
        updated = 0
        with self._conn:
            for file_path, tags_raw in rows:
                try:
                    tags: list[str] = json.loads(tags_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                tags_lower = [t.lower() for t in tags]
                if src_lower not in tags_lower:
                    continue
                new_tags = [t for t in tags if t.lower() != src_lower]
                if tgt_lower not in [t.lower() for t in new_tags]:
                    new_tags.append(tgt_lower)
                self._conn.execute(
                    "UPDATE processed_images SET tags = ? WHERE file_path = ?",
                    (json.dumps(new_tags), file_path),
                )
                updated += 1
        return updated

    def get_cleanup_candidates(self, include_review: bool = False) -> list[dict]:
        """Return photos flagged for cleanup.

        Returns list of dicts with keys: file_path, file_name (basename),
        cleanup_class, tags, scene_summary, image_date, nearest_city, nearest_country.
        Always includes cleanup_class='delete'. If include_review=True, also includes 'review'.
        Orders by file_path.
        """
        try:
            if include_review:
                placeholders = "?, ?"
                params: tuple = ("delete", "review")
            else:
                placeholders = "?"
                params = ("delete",)
            # Parameterized query; placeholders are code-controlled literals
            query = (  # nosec B608
                "SELECT file_path, tags, scene_summary, processed_at, cleanup_class "
                "FROM processed_images "
                "WHERE cleanup_class IN ("
                + placeholders  # nosec B608
                + ") "
                "ORDER BY file_path"
            )
            rows = self._conn.execute(query, params).fetchall()
        except sqlite3.Error:
            return []

        result = []
        for row in rows:
            file_path = row[0]
            result.append(
                {
                    "file_path": file_path,
                    "file_name": Path(file_path).name,
                    "tags": row[1],
                    "scene_summary": row[2],
                    "image_date": row[3],
                    "cleanup_class": row[4],
                    "nearest_city": None,
                    "nearest_country": None,
                }
            )
        return result

    # --- review UI query/update methods ---

    def get_images(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        cleanup_class: str | None = None,
    ) -> list[dict]:
        """Return paginated image records with all metadata.

        Args:
            limit: Max rows to return.
            offset: Row offset for pagination.
            status: Optional filter by status ('ok', 'error').
            cleanup_class: Optional filter by cleanup_class ('delete', 'review').

        Returns:
            List of image metadata dicts.
        """
        conditions: list[str] = []
        params: list[object] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if cleanup_class is not None:
            conditions.append("cleanup_class = ?")
            params.append(cleanup_class)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = (  # nosec B608
            "SELECT file_path, tags, scene_summary, processed_at, status, "
            "cleanup_class, scene_category, emotional_tone, event_hint, significance "
            "FROM processed_images "
            + where  # nosec B608
            + " ORDER BY file_path LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        rows = self._conn.execute(query, params).fetchall()
        return [self._image_row_to_dict(r) for r in rows]

    def count_images(
        self,
        status: str | None = None,
        cleanup_class: str | None = None,
    ) -> int:
        """Count images matching optional filters."""
        conditions: list[str] = []
        params: list[object] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if cleanup_class is not None:
            conditions.append("cleanup_class = ?")
            params.append(cleanup_class)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = "SELECT COUNT(*) FROM processed_images " + where  # nosec B608
        return self._conn.execute(query, params).fetchone()[0]

    def get_image(self, file_path: str) -> dict | None:
        """Return metadata for a single image, or None if not found."""
        row = self._conn.execute(
            "SELECT file_path, tags, scene_summary, processed_at, status, "
            "cleanup_class, scene_category, emotional_tone, event_hint, significance "
            "FROM processed_images WHERE file_path = ?",
            (file_path,),
        ).fetchone()
        if row is None:
            return None
        return self._image_row_to_dict(row)

    def update_image_tags(self, file_path: str, tags: list[str]) -> None:
        """Overwrite the tags list for an image."""
        self._conn.execute(
            "UPDATE processed_images SET tags = ? WHERE file_path = ?",
            (json.dumps(tags), file_path),
        )
        self._conn.commit()

    def update_image_cleanup(self, file_path: str, cleanup_class: str | None) -> None:
        """Set or clear the cleanup_class for an image."""
        self._conn.execute(
            "UPDATE processed_images SET cleanup_class = ? WHERE file_path = ?",
            (cleanup_class, file_path),
        )
        self._conn.commit()

    @staticmethod
    def _image_row_to_dict(row: tuple) -> dict:
        """Convert a processed_images SELECT row to a metadata dict."""
        file_path: str = row[0]
        tags_raw: str | None = row[1]
        try:
            tags_list: list[str] = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags_list = []
        return {
            "file_path": file_path,
            "file_name": Path(file_path).name,
            "tags": tags_raw,
            "tags_list": tags_list,
            "scene_summary": row[2],
            "processed_at": row[3],
            "status": row[4],
            "cleanup_class": row[5],
            "scene_category": row[6],
            "emotional_tone": row[7],
            "event_hint": row[8],
            "significance": row[9],
        }

    # --- face pipeline methods ---

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
        """Insert a detected face. Returns the new face row id."""
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
        return cur.lastrowid  # type: ignore[return-value]

    def get_faces_by_uuid(self, uuid: str) -> list[dict]:
        """Return all face rows whose image path stem matches the given UUID.

        Handles both full paths (``/library/abc123.jpg``) and bare filenames
        (``abc123.jpg``) by using two LIKE patterns.
        """
        rows = self._conn.execute(
            "SELECT id, image_path, bbox_x, bbox_y, bbox_w, bbox_h, confidence "
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
        return cur.lastrowid  # type: ignore[return-value]

    def get_persons(self) -> list[PersonCluster]:
        """Return all persons with their assigned face ids."""
        persons = self._conn.execute(
            "SELECT id, label, confirmed, source, trusted FROM persons"
        ).fetchall()
        result = []
        for pid, label, confirmed, source, trusted in persons:
            face_ids = [
                r[0]
                for r in self._conn.execute(
                    "SELECT id FROM faces WHERE person_id = ?", (pid,)
                ).fetchall()
            ]
            result.append(
                PersonCluster(
                    person_id=pid,
                    label=label,
                    confirmed=bool(confirmed),
                    face_ids=face_ids,
                    source=source or "auto",
                    trusted=bool(trusted),
                )
            )
        return result

    def update_person_label(self, person_id: int, label: str) -> None:
        """Update the display label for a person."""
        self._conn.execute("UPDATE persons SET label = ? WHERE id = ?", (label, person_id))
        self._conn.commit()

    def merge_persons(self, source_id: int, target_id: int) -> None:
        """Reassign all faces from source_id to target_id, then delete source_id."""
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

    def get_unassigned_faces(self) -> list[dict]:
        """Return faces that have no person assignment."""
        rows = self._conn.execute(
            "SELECT id, image_path, bbox_x, bbox_y, bbox_w, bbox_h, confidence "
            "FROM faces WHERE person_id IS NULL"
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

    def save_judge_result(self, result: "JudgeResult") -> None:
        """Persist a judge scoring result. Replaces any existing entry for the same file."""
        s = result.scores
        self._conn.execute(
            """
            INSERT OR REPLACE INTO judge_scores
                (file_path, scored_at, weighted_score, core_score, visible_score, verdict,
                 impact, story_subject, composition_center, lighting, creativity_style,
                 color_mood, presentation_crop, technical_excellence, focus_sharpness,
                 exposure_tonal, noise_cleanliness, subject_separation, edit_integrity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.file_path,
                datetime.now(timezone.utc).isoformat(),
                result.weighted_score,
                result.core_score,
                result.visible_score,
                s.verdict,
                s.impact,
                s.story_subject,
                s.composition_center,
                s.lighting,
                s.creativity_style,
                s.color_mood,
                s.presentation_crop,
                s.technical_excellence,
                s.focus_sharpness,
                s.exposure_tonal,
                s.noise_cleanliness,
                s.subject_separation,
                s.edit_integrity,
            ),
        )
        self._conn.commit()

    def get_judge_result(self, file_path: str) -> dict | None:
        """Return judge scores for a file, or None if not found."""
        row = self._conn.execute(
            """SELECT weighted_score, core_score, visible_score, verdict,
                      impact, story_subject, composition_center, lighting,
                      creativity_style, color_mood, presentation_crop,
                      technical_excellence, focus_sharpness, exposure_tonal,
                      noise_cleanliness, subject_separation, edit_integrity, scored_at
               FROM judge_scores WHERE file_path = ?""",
            (file_path,),
        ).fetchone()
        if row is None:
            return None
        return {
            "file_path": file_path,
            "weighted_score": row[0],
            "core_score": row[1],
            "visible_score": row[2],
            "verdict": row[3],
            "scored_at": row[17],
            "scores": {
                "impact": row[4],
                "story_subject": row[5],
                "composition_center": row[6],
                "lighting": row[7],
                "creativity_style": row[8],
                "color_mood": row[9],
                "presentation_crop": row[10],
                "technical_excellence": row[11],
                "focus_sharpness": row[12],
                "exposure_tonal": row[13],
                "noise_cleanliness": row[14],
                "subject_separation": row[15],
                "edit_integrity": row[16],
            },
        }

    def get_all_judge_results(self, limit: int | None = 200) -> list[dict]:
        """Return all judge scores ordered by weighted_score descending.

        Args:
            limit: Max rows to return. None = no limit (caution: may be large).

        Returns:
            List of dicts with keys: file_path, file_name, weighted_score,
            core_score, visible_score, verdict, scored_at.
        """
        limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
        rows = self._conn.execute(
            "SELECT file_path, weighted_score, core_score, visible_score, verdict, scored_at "
            "FROM judge_scores "
            "ORDER BY weighted_score DESC "
            + limit_clause  # nosec B608
        ).fetchall()
        return [
            {
                "file_path": row[0],
                "file_name": Path(row[0]).name,
                "weighted_score": row[1],
                "core_score": row[2],
                "visible_score": row[3],
                "verdict": row[4],
                "scored_at": row[5],
            }
            for row in rows
        ]

    def is_fresh(self, file_path: Path) -> bool:
        """Return True if DB has a row for this file and size/mtime still match."""
        row = self._conn.execute(
            "SELECT file_size, file_mtime FROM processed_images WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
        if row is None:
            return False
        try:
            stat = file_path.stat()
        except OSError:
            return False
        return row[0] == stat.st_size and row[1] == stat.st_mtime

    def get_cached_result(self, file_path: Path) -> ImageResult | None:
        """Return an ImageResult built from the stored DB row, or None if not found."""
        row = self._conn.execute(
            """
            SELECT tags, scene_summary, status, error_message,
                   scene_category, emotional_tone, cleanup_class,
                   has_text, text_summary, event_hint, significance
            FROM processed_images
            WHERE file_path = ?
            """,
            (str(file_path),),
        ).fetchone()
        if row is None:
            return None
        (
            tags_raw,
            scene_summary,
            status,
            error_message,
            scene_category,
            emotional_tone,
            cleanup_class,
            has_text_int,
            text_summary,
            event_hint,
            significance,
        ) = row
        try:
            tags: list[str] = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        return ImageResult(
            file_path=str(file_path),
            file_name=file_path.name,
            tags=tags,
            scene_summary=scene_summary,
            processing_status=status if status is not None else "ok",
            error_message=error_message,
            scene_category=scene_category,
            emotional_tone=emotional_tone,
            cleanup_class=cleanup_class,
            has_text=bool(has_text_int),
            text_summary=text_summary,
            event_hint=event_hint,
            significance=significance,
        )

    def has_usable_model_result(self, file_path: Path) -> bool:
        """Return True if the DB has a fresh row with non-empty tags."""
        if not self.is_fresh(file_path):
            return False
        row = self._conn.execute(
            "SELECT tags FROM processed_images WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
        if row is None:
            return False
        try:
            tags: list[str] = json.loads(row[0]) if row[0] else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        return len(tags) > 0

    def update_missing_fields(self, file_path: Path, result: ImageResult) -> None:
        """Update only NULL columns from result; never overwrite existing non-null values."""
        self._conn.execute(
            """
            UPDATE processed_images SET
                scene_summary  = COALESCE(scene_summary,  ?),
                scene_category = COALESCE(scene_category, ?),
                emotional_tone = COALESCE(emotional_tone, ?),
                cleanup_class  = COALESCE(cleanup_class,  ?),
                has_text       = CASE WHEN has_text = 0 OR has_text IS NULL
                                      THEN ? ELSE has_text END,
                text_summary   = COALESCE(text_summary,   ?),
                event_hint     = COALESCE(event_hint,     ?),
                significance   = COALESCE(significance,   ?)
            WHERE file_path = ?
            """,
            (
                result.scene_summary,
                result.scene_category,
                result.emotional_tone,
                result.cleanup_class,
                1 if result.has_text else 0,
                result.text_summary,
                result.event_hint,
                result.significance,
                str(file_path),
            ),
        )
        self._conn.commit()

    def __enter__(self) -> "ProgressDB":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()
