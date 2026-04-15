"""Tests for SQLite progress database."""

from __future__ import annotations

import sqlite3
import time

from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB


class TestProgressDB:
    def test_creation_creates_table(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            row = db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_images'"
            ).fetchone()
            assert row is not None
        finally:
            db.close()

    def test_is_processed_unknown_file(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            assert db.is_processed(tmp_path / "nonexistent.jpg") is False
        finally:
            db.close()

    def test_mark_done_and_is_processed(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        try:
            result = ImageResult(
                file_path=str(img), file_name="photo.jpg", tags=["sunset", "beach"]
            )
            db.mark_done(img, result)
            assert db.is_processed(img) is True
        finally:
            db.close()

    def test_is_processed_false_when_size_changes(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        try:
            result = ImageResult(file_path=str(img), file_name="photo.jpg", tags=["tree"])
            db.mark_done(img, result)
            assert db.is_processed(img) is True
            # change file size
            img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 200)
            assert db.is_processed(img) is False
        finally:
            db.close()

    def test_is_processed_false_when_mtime_changes(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        try:
            result = ImageResult(file_path=str(img), file_name="photo.jpg", tags=["dog"])
            db.mark_done(img, result)
            assert db.is_processed(img) is True
            # touch to change mtime
            time.sleep(0.05)
            img.write_bytes(img.read_bytes())
            assert db.is_processed(img) is False
        finally:
            db.close()

    def test_get_stats(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img1 = tmp_path / "ok.jpg"
        img1.write_bytes(b"\x00" * 50)
        img2 = tmp_path / "err.jpg"
        img2.write_bytes(b"\x00" * 50)
        try:
            ok_result = ImageResult(file_path=str(img1), file_name="ok.jpg", tags=["a"])
            db.mark_done(img1, ok_result)

            err_result = ImageResult(
                file_path=str(img2),
                file_name="err.jpg",
                processing_status="error",
                error_message="fail",
            )
            db.mark_done(img2, err_result)

            stats = db.get_stats()
            assert stats == {"total": 2, "ok": 1, "error": 1}
        finally:
            db.close()

    def test_mark_done_error_result(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img = tmp_path / "bad.jpg"
        img.write_bytes(b"\x00" * 10)
        try:
            result = ImageResult(
                file_path=str(img),
                file_name="bad.jpg",
                processing_status="error",
                error_message="Ollama timeout",
            )
            db.mark_done(img, result)
            assert db.is_processed(img) is True
            stats = db.get_stats()
            assert stats["error"] == 1
        finally:
            db.close()

    def test_reset_all_empty_db(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            count = db.reset_all()
            assert count == 0
            assert db.get_stats() == {"total": 0, "ok": 0, "error": 0}
        finally:
            db.close()

    def test_reset_all_removes_all_rows(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img1 = tmp_path / "a.jpg"
        img1.write_bytes(b"\x00" * 10)
        img2 = tmp_path / "b.jpg"
        img2.write_bytes(b"\x00" * 10)
        try:
            db.mark_done(img1, ImageResult(file_path=str(img1), file_name="a.jpg", tags=[]))
            db.mark_done(img2, ImageResult(file_path=str(img2), file_name="b.jpg", tags=[]))
            assert db.get_stats()["total"] == 2

            count = db.reset_all()
            assert count == 2
            assert db.get_stats()["total"] == 0
        finally:
            db.close()

    def test_reset_by_status_empty_db(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            count = db.reset_by_status("error")
            assert count == 0
        finally:
            db.close()

    def test_reset_by_status_removes_only_matching(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img_ok = tmp_path / "ok.jpg"
        img_ok.write_bytes(b"\x00" * 10)
        img_err = tmp_path / "err.jpg"
        img_err.write_bytes(b"\x00" * 10)
        try:
            db.mark_done(
                img_ok, ImageResult(file_path=str(img_ok), file_name="ok.jpg", tags=["a"])
            )
            db.mark_done(
                img_err,
                ImageResult(
                    file_path=str(img_err),
                    file_name="err.jpg",
                    processing_status="error",
                    error_message="fail",
                ),
            )

            count = db.reset_by_status("error")
            assert count == 1

            stats = db.get_stats()
            assert stats["total"] == 1
            assert stats["ok"] == 1
            assert stats["error"] == 0
        finally:
            db.close()

    def test_reset_by_status_no_match_returns_zero(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img = tmp_path / "ok.jpg"
        img.write_bytes(b"\x00" * 10)
        try:
            db.mark_done(img, ImageResult(file_path=str(img), file_name="ok.jpg", tags=[]))
            count = db.reset_by_status("error")
            assert count == 0
            assert db.get_stats()["total"] == 1
        finally:
            db.close()


_NEW_COLUMN_NAMES = {
    "scene_category",
    "emotional_tone",
    "cleanup_class",
    "has_text",
    "text_summary",
    "event_hint",
    "significance",
}


class TestSchemaVersioning:
    """Tests for PRAGMA user_version tracking and incremental migrations."""

    def _column_names(self, conn: sqlite3.Connection) -> set[str]:
        return {row[1] for row in conn.execute("PRAGMA table_info(processed_images)").fetchall()}

    def _user_version(self, conn: sqlite3.Connection) -> int:
        return conn.execute("PRAGMA user_version").fetchone()[0]

    def test_fresh_db_is_at_version_2(self, tmp_path):
        """A brand-new database must be fully migrated to the latest version."""
        db = ProgressDB(db_path=tmp_path / "v2.db")
        try:
            assert self._user_version(db._conn) == 2
        finally:
            db.close()

    def test_fresh_db_has_all_new_columns(self, tmp_path):
        """All version-2 columns must be present in a fresh database."""
        db = ProgressDB(db_path=tmp_path / "v2.db")
        try:
            cols = self._column_names(db._conn)
            assert _NEW_COLUMN_NAMES.issubset(cols)
        finally:
            db.close()

    def test_v1_db_migrates_to_v2_on_open(self, tmp_path):
        """A database stuck at version 1 (missing new columns) must be upgraded on open."""
        db_path = tmp_path / "old.db"
        # Build a minimal version-1 schema without the new columns.
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE processed_images (
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
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

        db = ProgressDB(db_path=db_path)
        try:
            assert self._user_version(db._conn) == 2
            assert _NEW_COLUMN_NAMES.issubset(self._column_names(db._conn))
        finally:
            db.close()

    def test_user_version_is_set_correctly_after_migration(self, tmp_path):
        """PRAGMA user_version must equal 2 after migration from version 1."""
        db_path = tmp_path / "check_version.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE processed_images (
                file_path TEXT PRIMARY KEY,
                file_size INTEGER,
                file_mtime REAL,
                tags TEXT,
                scene_summary TEXT,
                processed_at TEXT,
                status TEXT,
                error_message TEXT
            )
            """
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

        db = ProgressDB(db_path=db_path)
        db.close()

        # Verify version directly via a raw connection — no ProgressDB involved.
        raw = sqlite3.connect(str(db_path))
        try:
            assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
        finally:
            raw.close()

    def test_migrations_are_idempotent(self, tmp_path):
        """Opening an already-migrated database a second time must not raise."""
        db_path = tmp_path / "idempotent.db"
        db = ProgressDB(db_path=db_path)
        db.close()

        # Second open — all migrations are already applied.
        db2 = ProgressDB(db_path=db_path)
        try:
            assert self._user_version(db2._conn) == 2
            assert _NEW_COLUMN_NAMES.issubset(self._column_names(db2._conn))
        finally:
            db2.close()


class TestGetCleanupCandidates:
    def _make_image(self, tmp_path, name: str):
        img = tmp_path / name
        img.write_bytes(b"\x00" * 10)
        return img

    def test_returns_only_delete_by_default(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img_del = self._make_image(tmp_path, "del.jpg")
        img_rev = self._make_image(tmp_path, "rev.jpg")
        img_keep = self._make_image(tmp_path, "keep.jpg")
        try:
            db.mark_done(
                img_del,
                ImageResult(
                    file_path=str(img_del), file_name="del.jpg", cleanup_class="delete", tags=["a"]
                ),
            )
            db.mark_done(
                img_rev,
                ImageResult(
                    file_path=str(img_rev), file_name="rev.jpg", cleanup_class="review", tags=["b"]
                ),
            )
            db.mark_done(
                img_keep,
                ImageResult(
                    file_path=str(img_keep),
                    file_name="keep.jpg",
                    cleanup_class="keep",
                    tags=["c"],
                ),
            )
            candidates = db.get_cleanup_candidates()
            assert len(candidates) == 1
            assert candidates[0]["cleanup_class"] == "delete"
            assert candidates[0]["file_name"] == "del.jpg"
        finally:
            db.close()

    def test_returns_delete_and_review_with_include_review(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img_del = self._make_image(tmp_path, "del.jpg")
        img_rev = self._make_image(tmp_path, "rev.jpg")
        img_keep = self._make_image(tmp_path, "keep.jpg")
        try:
            db.mark_done(
                img_del,
                ImageResult(
                    file_path=str(img_del), file_name="del.jpg", cleanup_class="delete", tags=["a"]
                ),
            )
            db.mark_done(
                img_rev,
                ImageResult(
                    file_path=str(img_rev), file_name="rev.jpg", cleanup_class="review", tags=["b"]
                ),
            )
            db.mark_done(
                img_keep,
                ImageResult(
                    file_path=str(img_keep),
                    file_name="keep.jpg",
                    cleanup_class="keep",
                    tags=["c"],
                ),
            )
            candidates = db.get_cleanup_candidates(include_review=True)
            classes = {c["cleanup_class"] for c in candidates}
            assert classes == {"delete", "review"}
            assert len(candidates) == 2
        finally:
            db.close()

    def test_does_not_return_keep_entries(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img_keep = self._make_image(tmp_path, "keep.jpg")
        try:
            db.mark_done(
                img_keep,
                ImageResult(
                    file_path=str(img_keep),
                    file_name="keep.jpg",
                    cleanup_class="keep",
                    tags=["landscape"],
                ),
            )
            candidates = db.get_cleanup_candidates(include_review=True)
            assert candidates == []
        finally:
            db.close()

    def test_returns_empty_list_on_old_db_without_cleanup_class(self, tmp_path):
        """An old DB lacking cleanup_class column must return an empty list."""
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE processed_images (
                file_path TEXT PRIMARY KEY,
                file_size INTEGER,
                file_mtime REAL,
                tags TEXT,
                scene_summary TEXT,
                processed_at TEXT,
                status TEXT,
                error_message TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO processed_images (file_path, tags, status) VALUES (?, ?, ?)",
            ("/old/photo.jpg", '["a"]', "ok"),
        )
        conn.commit()
        conn.close()

        # Bypass ProgressDB.__init__ to avoid auto-migration adding the column.
        raw = sqlite3.connect(str(db_path))
        db = ProgressDB.__new__(ProgressDB)
        db._path = db_path
        db._conn = raw
        candidates = db.get_cleanup_candidates()
        raw.close()
        assert candidates == []

    def test_returned_dict_fields_are_correct(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img = self._make_image(tmp_path, "photo.jpg")
        try:
            db.mark_done(
                img,
                ImageResult(
                    file_path=str(img),
                    file_name="photo.jpg",
                    cleanup_class="delete",
                    tags=["sunset", "beach"],
                    scene_summary="A beautiful sunset",
                ),
            )
            candidates = db.get_cleanup_candidates()
            assert len(candidates) == 1
            item = candidates[0]
            assert item["file_path"] == str(img)
            assert item["file_name"] == "photo.jpg"
            assert item["cleanup_class"] == "delete"
            assert item["tags"] is not None
            assert "image_date" in item
            assert "nearest_city" in item
            assert "nearest_country" in item
        finally:
            db.close()
