"""SQLite progress database for incremental image processing."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from pyimgtag.db.face_db import FaceDB
from pyimgtag.db.image_db import _DEFAULT_PATH_BATCH_SIZE, ImageDB
from pyimgtag.db.judge_db import _DEFAULT_JUDGE_RESULTS_LIMIT, JudgeDB
from pyimgtag.models import FaceDetection, ImageResult, PersonCluster

if TYPE_CHECKING:
    from collections.abc import Iterator

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
        # 0.10.0: simple-prompt judge stores the model's natural-language
        # justification next to the integer score.
        (7, "ALTER TABLE judge_scores ADD COLUMN reason TEXT"),
        # 0.12.0: persist the photo's EXIF capture timestamp so the Query
        # page can show "when the photo was taken" without re-reading EXIF.
        (8, "ALTER TABLE processed_images ADD COLUMN image_date TEXT"),
        # 0.13.6: indexes on filter/sort columns to speed up paginated queries.
        (9, "CREATE INDEX IF NOT EXISTS idx_pi_status ON processed_images(status)"),
        (9, "CREATE INDEX IF NOT EXISTS idx_pi_cleanup ON processed_images(cleanup_class)"),
        (9, "CREATE INDEX IF NOT EXISTS idx_pi_date ON processed_images(processed_at)"),
        # 0.16.6: faces can be explicitly dismissed so auto-clustering skips them.
        (10, "ALTER TABLE faces ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0"),
        (10, "CREATE INDEX IF NOT EXISTS idx_faces_ignored ON faces(ignored)"),
        # 0.16.6: track images already face-scanned so zero-face images are not
        # re-scanned on every subsequent faces scan run.
        (
            11,
            """CREATE TABLE IF NOT EXISTS face_scanned_images (
                image_path TEXT PRIMARY KEY
            )""",
        ),
    )

    def __init__(self, db_path: str | Path | None = None) -> None:
        """Open (creating if needed) the SQLite progress database.

        Args:
            db_path: Path to the database file. When ``None``, defaults to
                ``~/.cache/pyimgtag/progress.db``.

        The parent directory is created if missing, the connection is opened
        with ``check_same_thread=False`` (a background thread may read while the
        main thread writes) in WAL journal mode, and the schema plus any pending
        versioned migrations run on open.

        Raises:
            sqlite3.DatabaseError: If the file is not a usable database or a
                migration cannot be applied.
        """
        if db_path is None:
            db_path = Path.home() / ".cache" / "pyimgtag" / "progress.db"
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()

    @property
    def path(self) -> Path:
        """Filesystem path to the backing SQLite database file."""
        return self._path

    # Domain helpers are exposed as properties that bind to the *current*
    # ``self._conn`` on every access (construction is a single attribute
    # assignment). This keeps the long-standing test/caller pattern of
    # ``ProgressDB.__new__`` + injecting ``_conn`` working without
    # re-running ``__init__``.

    @property
    def _images(self) -> ImageDB:
        """Image-progress domain helper bound to the current connection."""
        return ImageDB(self._conn)

    @property
    def _faces(self) -> FaceDB:
        """Face/person domain helper bound to the current connection."""
        return FaceDB(self._conn)

    @property
    def _judge(self) -> JudgeDB:
        """Judge-score domain helper bound to the current connection."""
        return JudgeDB(self._conn)

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
            self._conn.execute(f"SAVEPOINT migrate_v{int(target_ver)}")  # nosec B608
            try:
                for sql in stmts:
                    try:
                        self._conn.execute(sql)
                    except sqlite3.OperationalError as exc:
                        # Tolerate "duplicate column name" so re-running migrations
                        # on an already-migrated DB is safe.
                        if "duplicate column name" not in str(exc).lower():
                            raise
                self._conn.execute(f"PRAGMA user_version = {int(target_ver)}")  # nosec B608
                self._conn.execute(f"RELEASE migrate_v{int(target_ver)}")  # nosec B608
            except Exception:  # noqa: BLE001 — roll back savepoint on any failure, then re-raise
                self._conn.execute(f"ROLLBACK TO migrate_v{int(target_ver)}")  # nosec B608
                raise
        self._conn.commit()

    # --- image progress methods (delegated to ImageDB) ---

    def is_processed(self, file_path: Path) -> bool:
        """Delegate to :meth:`ImageDB.is_processed`."""
        return self._images.is_processed(file_path)

    def mark_done(self, file_path: Path, result: ImageResult) -> None:
        """Delegate to :meth:`ImageDB.mark_done`."""
        self._images.mark_done(file_path, result)

    def get_stats(self) -> dict:
        """Delegate to :meth:`ImageDB.get_stats`."""
        return self._images.get_stats()

    def reset_all(self) -> int:
        """Delegate to :meth:`ImageDB.reset_all`."""
        return self._images.reset_all()

    def reset_by_status(self, status: str) -> int:
        """Delegate to :meth:`ImageDB.reset_by_status`."""
        return self._images.reset_by_status(status)

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
        min_judge_score: int | None = None,
        max_judge_score: int | None = None,
        judged: bool | None = None,
        sort: str = "path_asc",
    ) -> list[dict]:
        """Delegate to :meth:`ImageDB.query_images`."""
        return self._images.query_images(
            tag,
            has_text,
            cleanup_class,
            scene_category,
            city,
            country,
            status,
            limit,
            min_judge_score,
            max_judge_score,
            judged,
            sort,
        )

    def get_tag_counts(self) -> list[tuple[str, int]]:
        """Delegate to :meth:`ImageDB.get_tag_counts`."""
        return self._images.get_tag_counts()

    def rename_tag(self, old_tag: str, new_tag: str) -> int:
        """Delegate to :meth:`ImageDB.rename_tag`."""
        return self._images.rename_tag(old_tag, new_tag)

    def delete_tag(self, tag: str) -> int:
        """Delegate to :meth:`ImageDB.delete_tag`."""
        return self._images.delete_tag(tag)

    def merge_tags(self, source_tag: str, target_tag: str) -> int:
        """Delegate to :meth:`ImageDB.merge_tags`."""
        return self._images.merge_tags(source_tag, target_tag)

    def get_cleanup_candidates(self, include_review: bool = False) -> list[dict]:
        """Delegate to :meth:`ImageDB.get_cleanup_candidates`."""
        return self._images.get_cleanup_candidates(include_review)

    def get_images(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        cleanup_class: str | None = None,
        sort: str = "path_asc",
    ) -> list[dict]:
        """Delegate to :meth:`ImageDB.get_images`."""
        return self._images.get_images(limit, offset, status, cleanup_class, sort)

    def count_images(
        self,
        status: str | None = None,
        cleanup_class: str | None = None,
    ) -> int:
        """Delegate to :meth:`ImageDB.count_images`."""
        return self._images.count_images(status, cleanup_class)

    def get_image(self, file_path: str) -> dict | None:
        """Delegate to :meth:`ImageDB.get_image`."""
        return self._images.get_image(file_path)

    def get_known_file_path(self, file_path: str) -> str | None:
        """Delegate to :meth:`ImageDB.get_known_file_path`."""
        return self._images.get_known_file_path(file_path)

    def update_image_tags(self, file_path: str, tags: list[str]) -> None:
        """Delegate to :meth:`ImageDB.update_image_tags`."""
        self._images.update_image_tags(file_path, tags)

    def update_image_cleanup(self, file_path: str, cleanup_class: str | None) -> None:
        """Delegate to :meth:`ImageDB.update_image_cleanup`."""
        self._images.update_image_cleanup(file_path, cleanup_class)

    def delete_image(self, file_path: str) -> bool:
        """Delegate to :meth:`ImageDB.delete_image`."""
        return self._images.delete_image(file_path)

    def iter_image_paths(self, batch_size: int = _DEFAULT_PATH_BATCH_SIZE) -> "Iterator[str]":
        """Delegate to :meth:`ImageDB.iter_image_paths`."""
        return self._images.iter_image_paths(batch_size)

    def delete_image_rows(self, paths: list[str]) -> int:
        """Delegate to :meth:`ImageDB.delete_image_rows`."""
        return self._images.delete_image_rows(paths)

    # --- face pipeline methods (delegated to FaceDB) ---

    def insert_face(
        self,
        image_path: str,
        detection: FaceDetection,
        embedding: np.ndarray | None = None,
    ) -> int:
        """Delegate to :meth:`FaceDB.insert_face`."""
        return self._faces.insert_face(image_path, detection, embedding)

    def get_faces_by_uuid(self, uuid: str) -> list[dict]:
        """Delegate to :meth:`FaceDB.get_faces_by_uuid`."""
        return self._faces.get_faces_by_uuid(uuid)

    def has_photos_person(self, label: str) -> bool:
        """Delegate to :meth:`FaceDB.has_photos_person`."""
        return self._faces.has_photos_person(label)

    def get_photos_person_id(self, label: str) -> int | None:
        """Delegate to :meth:`FaceDB.get_photos_person_id`."""
        return self._faces.get_photos_person_id(label)

    def mark_face_scanned(self, image_path: str) -> None:
        """Delegate to :meth:`FaceDB.mark_face_scanned`."""
        self._faces.mark_face_scanned(image_path)

    def is_face_scanned(self, image_path: str) -> bool:
        """Delegate to :meth:`FaceDB.is_face_scanned`."""
        return self._faces.is_face_scanned(image_path)

    def get_faces_for_image(self, image_path: str) -> list[dict]:
        """Delegate to :meth:`FaceDB.get_faces_for_image`."""
        return self._faces.get_faces_for_image(image_path)

    def get_all_embeddings(self) -> list[tuple[int, np.ndarray]]:
        """Delegate to :meth:`FaceDB.get_all_embeddings`."""
        return self._faces.get_all_embeddings()

    def get_clusterable_embeddings(self) -> list[tuple[int, np.ndarray]]:
        """Delegate to :meth:`FaceDB.get_clusterable_embeddings`."""
        return self._faces.get_clusterable_embeddings()

    def get_embeddings_for_faces(self, face_ids: list[int]) -> dict[int, np.ndarray]:
        """Delegate to :meth:`FaceDB.get_embeddings_for_faces`."""
        return self._faces.get_embeddings_for_faces(face_ids)

    def get_person_embeddings(self, person_id: int) -> list[np.ndarray]:
        """Delegate to :meth:`FaceDB.get_person_embeddings`."""
        return self._faces.get_person_embeddings(person_id)

    def set_person_id(self, face_id: int, person_id: int) -> None:
        """Delegate to :meth:`FaceDB.set_person_id`."""
        self._faces.set_person_id(face_id, person_id)

    def create_person(
        self,
        label: str = "",
        confirmed: bool = False,
        source: str = "auto",
        trusted: bool = False,
    ) -> int:
        """Delegate to :meth:`FaceDB.create_person`."""
        return self._faces.create_person(label, confirmed, source, trusted)

    def get_persons(self) -> list[PersonCluster]:
        """Delegate to :meth:`FaceDB.get_persons`."""
        return self._faces.get_persons()

    def update_person_label(self, person_id: int, label: str) -> None:
        """Delegate to :meth:`FaceDB.update_person_label`."""
        self._faces.update_person_label(person_id, label)

    def confirm_person(self, person_id: int) -> None:
        """Delegate to :meth:`FaceDB.confirm_person`."""
        self._faces.confirm_person(person_id)

    def merge_persons(self, source_id: int, target_id: int) -> None:
        """Delegate to :meth:`FaceDB.merge_persons`."""
        self._faces.merge_persons(source_id, target_id)

    def delete_person(self, person_id: int) -> None:
        """Delegate to :meth:`FaceDB.delete_person`."""
        self._faces.delete_person(person_id)

    def confirm_persons(self, person_ids: list[int]) -> int:
        """Delegate to :meth:`FaceDB.confirm_persons`."""
        return self._faces.confirm_persons(person_ids)

    def delete_persons(self, person_ids: list[int]) -> int:
        """Delegate to :meth:`FaceDB.delete_persons`."""
        return self._faces.delete_persons(person_ids)

    def clear_auto_persons(self) -> None:
        """Delegate to :meth:`FaceDB.clear_auto_persons`."""
        self._faces.clear_auto_persons()

    def reset_all_faces(self, *, dry_run: bool = False) -> dict[str, int]:
        """Delegate to :meth:`FaceDB.reset_all_faces`."""
        return self._faces.reset_all_faces(dry_run=dry_run)

    def reset_untrusted_faces(self, *, dry_run: bool = False) -> dict[str, int]:
        """Delegate to :meth:`FaceDB.reset_untrusted_faces`."""
        return self._faces.reset_untrusted_faces(dry_run=dry_run)

    def count_auto_persons(self) -> int:
        """Delegate to :meth:`FaceDB.count_auto_persons`."""
        return self._faces.count_auto_persons()

    def get_auto_person_ids(self) -> set[int]:
        """Delegate to :meth:`FaceDB.get_auto_person_ids`."""
        return self._faces.get_auto_person_ids()

    def ignore_face(self, face_id: int) -> None:
        """Delegate to :meth:`FaceDB.ignore_face`."""
        self._faces.ignore_face(face_id)

    def restore_face(self, face_id: int) -> None:
        """Delegate to :meth:`FaceDB.restore_face`."""
        self._faces.restore_face(face_id)

    def get_ignored_faces(self) -> list[dict]:
        """Delegate to :meth:`FaceDB.get_ignored_faces`."""
        return self._faces.get_ignored_faces()

    def get_unassigned_faces(self) -> list[dict]:
        """Delegate to :meth:`FaceDB.get_unassigned_faces`."""
        return self._faces.get_unassigned_faces()

    def unassign_face(self, face_id: int) -> None:
        """Delegate to :meth:`FaceDB.unassign_face`."""
        self._faces.unassign_face(face_id)

    def get_faces_for_person(self, person_id: int) -> list[dict]:
        """Delegate to :meth:`FaceDB.get_faces_for_person`."""
        return self._faces.get_faces_for_person(person_id)

    def get_assigned_faces(self) -> list[dict]:
        """Delegate to :meth:`FaceDB.get_assigned_faces`."""
        return self._faces.get_assigned_faces()

    def get_face_count(self) -> int:
        """Delegate to :meth:`FaceDB.get_face_count`."""
        return self._faces.get_face_count()

    def get_face_by_id(self, face_id: int) -> dict | None:
        """Delegate to :meth:`FaceDB.get_face_by_id`."""
        return self._faces.get_face_by_id(face_id)

    def save_judge_result(self, result: "JudgeResult") -> None:
        """Delegate to :meth:`JudgeDB.save_judge_result`."""
        self._judge.save_judge_result(result)

    def get_judge_result(self, file_path: str) -> dict | None:
        """Delegate to :meth:`JudgeDB.get_judge_result`."""
        return self._judge.get_judge_result(file_path)

    def get_all_judge_results(self, limit: int | None = _DEFAULT_JUDGE_RESULTS_LIMIT) -> list[dict]:
        """Delegate to :meth:`JudgeDB.get_all_judge_results`."""
        return self._judge.get_all_judge_results(limit)

    def query_judge_results(
        self,
        offset: int = 0,
        limit: int = 50,
        sort: str = "rating_desc",
        min_rating: int | None = None,
        max_rating: int | None = None,
    ) -> dict:
        """Delegate to :meth:`JudgeDB.query_judge_results`."""
        return self._judge.query_judge_results(offset, limit, sort, min_rating, max_rating)

    def is_fresh(self, file_path: Path) -> bool:
        """Delegate to :meth:`ImageDB.is_fresh`."""
        return self._images.is_fresh(file_path)

    def get_cached_result(self, file_path: Path) -> ImageResult | None:
        """Delegate to :meth:`ImageDB.get_cached_result`."""
        return self._images.get_cached_result(file_path)

    def is_complete_cached(self, file_path: Path) -> bool:
        """Delegate to :meth:`ImageDB.is_complete_cached`."""
        return self._images.is_complete_cached(file_path)

    def has_usable_model_result(self, file_path: Path) -> bool:
        """Delegate to :meth:`ImageDB.has_usable_model_result`."""
        return self._images.has_usable_model_result(file_path)

    def update_missing_fields(self, file_path: Path, result: ImageResult) -> None:
        """Delegate to :meth:`ImageDB.update_missing_fields`."""
        self._images.update_missing_fields(file_path, result)

    def __enter__(self) -> "ProgressDB":
        """Enter the context manager, returning this instance."""
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Exit the context manager, closing the connection via :meth:`close`."""
        self.close()

    def close(self) -> None:
        """Close the underlying SQLite connection.

        The instance must not be used afterwards.
        """
        self._conn.close()
