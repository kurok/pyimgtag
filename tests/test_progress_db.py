"""Tests for SQLite progress database."""

from __future__ import annotations

import sqlite3
import time

import numpy as np

from pyimgtag.models import FaceDetection, ImageResult
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
            db.mark_done(img_ok, ImageResult(file_path=str(img_ok), file_name="ok.jpg", tags=["a"]))
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

    def _column_names(self, conn: sqlite3.Connection, table: str = "processed_images") -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None

    def _user_version(self, conn: sqlite3.Connection) -> int:
        return conn.execute("PRAGMA user_version").fetchone()[0]

    def test_fresh_db_is_at_version_3(self, tmp_path):
        """A brand-new database must be fully migrated to the latest version."""
        db = ProgressDB(db_path=tmp_path / "v3.db")
        try:
            assert self._user_version(db._conn) == 4
        finally:
            db.close()

    def test_fresh_db_has_all_new_columns(self, tmp_path):
        """All version-2 columns must be present in a fresh database."""
        db = ProgressDB(db_path=tmp_path / "v3.db")
        try:
            cols = self._column_names(db._conn)
            assert _NEW_COLUMN_NAMES.issubset(cols)
        finally:
            db.close()

    def test_fresh_db_has_face_tables(self, tmp_path):
        """A fresh database must have faces and persons tables."""
        db = ProgressDB(db_path=tmp_path / "v3.db")
        try:
            assert self._table_exists(db._conn, "faces")
            assert self._table_exists(db._conn, "persons")
            face_cols = self._column_names(db._conn, "faces")
            assert {
                "id",
                "image_path",
                "bbox_x",
                "bbox_y",
                "bbox_w",
                "bbox_h",
                "confidence",
                "embedding",
                "person_id",
            }.issubset(face_cols)
            person_cols = self._column_names(db._conn, "persons")
            assert {"id", "label", "confirmed"}.issubset(person_cols)
        finally:
            db.close()

    def test_v1_db_migrates_to_v3_on_open(self, tmp_path):
        """A database stuck at version 1 must be upgraded through v2 and v3."""
        db_path = tmp_path / "old.db"
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
            assert self._user_version(db._conn) == 4
            assert _NEW_COLUMN_NAMES.issubset(self._column_names(db._conn))
            assert self._table_exists(db._conn, "faces")
            assert self._table_exists(db._conn, "persons")
        finally:
            db.close()

    def test_v2_db_migrates_to_v3_on_open(self, tmp_path):
        """A database at version 2 must gain face tables on open."""
        db_path = tmp_path / "v2.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE processed_images (
                file_path TEXT PRIMARY KEY, file_size INTEGER, file_mtime REAL,
                tags TEXT, scene_summary TEXT, processed_at TEXT, status TEXT,
                error_message TEXT, scene_category TEXT, emotional_tone TEXT,
                cleanup_class TEXT, has_text INTEGER DEFAULT 0, text_summary TEXT,
                event_hint TEXT, significance TEXT
            )
            """
        )
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        conn.close()

        db = ProgressDB(db_path=db_path)
        try:
            assert self._user_version(db._conn) == 4
            assert self._table_exists(db._conn, "faces")
            assert self._table_exists(db._conn, "persons")
        finally:
            db.close()

    def test_user_version_is_set_correctly_after_migration(self, tmp_path):
        """PRAGMA user_version must equal 4 after migration from version 1."""
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

        raw = sqlite3.connect(str(db_path))
        try:
            assert raw.execute("PRAGMA user_version").fetchone()[0] == 4
        finally:
            raw.close()

    def test_migrations_are_idempotent(self, tmp_path):
        """Opening an already-migrated database a second time must not raise."""
        db_path = tmp_path / "idempotent.db"
        db = ProgressDB(db_path=db_path)
        db.close()

        db2 = ProgressDB(db_path=db_path)
        try:
            assert self._user_version(db2._conn) == 4
            assert _NEW_COLUMN_NAMES.issubset(self._column_names(db2._conn))
            assert self._table_exists(db2._conn, "faces")
            assert self._table_exists(db2._conn, "persons")
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


class TestReviewMethods:
    """Tests for review UI query and update methods."""

    def _populate(self, db: ProgressDB, tmp_path) -> list[str]:
        """Insert 3 images with varied metadata. Returns list of file_path strings."""
        paths = []
        specs = [
            ("a.jpg", ["sunset", "beach"], "A warm sunset.", "delete"),
            ("b.jpg", ["dog", "park"], "A dog running.", "review"),
            ("c.jpg", ["mountain"], "Snowy peaks.", None),
        ]
        for name, tags, summary, cleanup in specs:
            img = tmp_path / name
            img.write_bytes(b"\x00" * 10)
            result = ImageResult(
                file_path=str(img),
                file_name=name,
                tags=tags,
                scene_summary=summary,
                cleanup_class=cleanup,
            )
            db.mark_done(img, result)
            paths.append(str(img))
        return paths

    def test_get_images_returns_all(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.get_images(limit=10)
            assert len(rows) == 3
        finally:
            db.close()

    def test_get_images_pagination(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            page1 = db.get_images(limit=2, offset=0)
            page2 = db.get_images(limit=2, offset=2)
            assert len(page1) == 2
            assert len(page2) == 1
            assert page1[0]["file_path"] != page2[0]["file_path"]
        finally:
            db.close()

    def test_get_images_filter_cleanup(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.get_images(cleanup_class="delete")
            assert len(rows) == 1
            assert rows[0]["cleanup_class"] == "delete"
        finally:
            db.close()

    def test_get_images_dict_has_tags_list(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.get_images()
            for row in rows:
                assert "tags_list" in row
                assert isinstance(row["tags_list"], list)
        finally:
            db.close()

    def test_count_images_total(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            assert db.count_images() == 3
        finally:
            db.close()

    def test_count_images_filter(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            assert db.count_images(cleanup_class="delete") == 1
            assert db.count_images(cleanup_class="review") == 1
            assert db.count_images(cleanup_class="nonexistent") == 0
        finally:
            db.close()

    def test_get_image_found(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            paths = self._populate(db, tmp_path)
            row = db.get_image(paths[0])
            assert row is not None
            assert row["file_path"] == paths[0]
            assert row["tags_list"] == ["sunset", "beach"]
            assert row["cleanup_class"] == "delete"
        finally:
            db.close()

    def test_get_image_not_found(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            assert db.get_image("/nonexistent/path.jpg") is None
        finally:
            db.close()

    def test_update_image_tags(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            paths = self._populate(db, tmp_path)
            db.update_image_tags(paths[0], ["new", "tags"])
            row = db.get_image(paths[0])
            assert row is not None
            assert row["tags_list"] == ["new", "tags"]
        finally:
            db.close()

    def test_update_image_cleanup_set(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            paths = self._populate(db, tmp_path)
            db.update_image_cleanup(paths[2], "delete")
            row = db.get_image(paths[2])
            assert row is not None
            assert row["cleanup_class"] == "delete"
        finally:
            db.close()

    def test_update_image_cleanup_clear(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            paths = self._populate(db, tmp_path)
            db.update_image_cleanup(paths[0], None)
            row = db.get_image(paths[0])
            assert row is not None
            assert row["cleanup_class"] is None
        finally:
            db.close()


class TestFaceDB:
    """Tests for face pipeline database methods."""

    def test_insert_face_returns_id(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            det = FaceDetection(
                image_path="/img/a.jpg",
                bbox_x=10,
                bbox_y=20,
                bbox_w=50,
                bbox_h=60,
                confidence=0.95,
            )
            face_id = db.insert_face("/img/a.jpg", det)
            assert isinstance(face_id, int)
            assert face_id >= 1
        finally:
            db.close()

    def test_insert_face_with_embedding(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            det = FaceDetection(image_path="/img/a.jpg", bbox_x=0, bbox_y=0, bbox_w=64, bbox_h=64)
            emb = np.random.rand(128).astype(np.float64)
            face_id = db.insert_face("/img/a.jpg", det, embedding=emb)
            results = db.get_all_embeddings()
            assert len(results) == 1
            assert results[0][0] == face_id
            np.testing.assert_array_almost_equal(results[0][1], emb)
        finally:
            db.close()

    def test_get_faces_for_image(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            det1 = FaceDetection(
                image_path="/img/a.jpg", bbox_x=10, bbox_y=20, bbox_w=50, bbox_h=60
            )
            det2 = FaceDetection(
                image_path="/img/a.jpg", bbox_x=100, bbox_y=200, bbox_w=50, bbox_h=60
            )
            det3 = FaceDetection(image_path="/img/b.jpg", bbox_x=5, bbox_y=5, bbox_w=30, bbox_h=30)
            db.insert_face("/img/a.jpg", det1)
            db.insert_face("/img/a.jpg", det2)
            db.insert_face("/img/b.jpg", det3)

            faces_a = db.get_faces_for_image("/img/a.jpg")
            assert len(faces_a) == 2
            assert faces_a[0]["bbox_x"] == 10
            assert faces_a[1]["bbox_x"] == 100

            faces_b = db.get_faces_for_image("/img/b.jpg")
            assert len(faces_b) == 1
        finally:
            db.close()

    def test_get_faces_for_image_empty(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            assert db.get_faces_for_image("/nonexistent.jpg") == []
        finally:
            db.close()

    def test_get_all_embeddings_skips_null(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            det1 = FaceDetection(image_path="/img/a.jpg")
            det2 = FaceDetection(image_path="/img/b.jpg")
            db.insert_face("/img/a.jpg", det1, embedding=np.ones(128))
            db.insert_face("/img/b.jpg", det2)  # no embedding
            results = db.get_all_embeddings()
            assert len(results) == 1
        finally:
            db.close()

    def test_create_person_and_assign(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            det = FaceDetection(image_path="/img/a.jpg")
            face_id = db.insert_face("/img/a.jpg", det)
            person_id = db.create_person(label="Alice")
            db.set_person_id(face_id, person_id)

            faces = db.get_faces_for_image("/img/a.jpg")
            assert faces[0]["person_id"] == person_id
        finally:
            db.close()

    def test_get_persons(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            det1 = FaceDetection(image_path="/img/a.jpg")
            det2 = FaceDetection(image_path="/img/b.jpg")
            f1 = db.insert_face("/img/a.jpg", det1)
            f2 = db.insert_face("/img/b.jpg", det2)
            pid = db.create_person(label="Bob", confirmed=True)
            db.set_person_id(f1, pid)
            db.set_person_id(f2, pid)

            persons = db.get_persons()
            assert len(persons) == 1
            assert persons[0].label == "Bob"
            assert persons[0].confirmed is True
            assert set(persons[0].face_ids) == {f1, f2}
        finally:
            db.close()

    def test_update_person_label(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            pid = db.create_person(label="Unknown_1")
            db.update_person_label(pid, "Charlie")
            persons = db.get_persons()
            assert persons[0].label == "Charlie"
        finally:
            db.close()

    def test_get_face_count(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            assert db.get_face_count() == 0
            db.insert_face("/a.jpg", FaceDetection())
            db.insert_face("/b.jpg", FaceDetection())
            assert db.get_face_count() == 2
        finally:
            db.close()

    def test_embedding_roundtrip_precision(self, tmp_path):
        """Embedding blob serialization must preserve float64 precision."""
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            original = np.array([1.0 / 3.0, np.pi, -0.0, 1e-300, 1e300] + [0.0] * 123)
            db.insert_face("/img/a.jpg", FaceDetection(), embedding=original)
            results = db.get_all_embeddings()
            np.testing.assert_array_equal(results[0][1], original)
        finally:
            db.close()


def _make_image(tmp_path, name: str):
    img = tmp_path / name
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    return img


class TestMigrationV4:
    def test_fresh_db_is_at_version_4(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            ver = db._conn.execute("PRAGMA user_version").fetchone()[0]
            assert ver == 4
        finally:
            db.close()

    def test_fresh_db_has_location_columns(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            cols = {
                row[1] for row in db._conn.execute("PRAGMA table_info(processed_images)").fetchall()
            }
            assert "nearest_city" in cols
            assert "nearest_region" in cols
            assert "nearest_country" in cols
        finally:
            db.close()

    def test_mark_done_stores_location(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img = _make_image(tmp_path, "photo.jpg")
        try:
            result = ImageResult(
                file_path=str(img),
                file_name="photo.jpg",
                tags=["nature"],
                nearest_city="Lviv",
                nearest_region="Lviv Oblast",
                nearest_country="Ukraine",
            )
            db.mark_done(img, result)
            row = db._conn.execute(
                "SELECT nearest_city, nearest_region, nearest_country "
                "FROM processed_images WHERE file_path = ?",
                (str(img),),
            ).fetchone()
            assert row[0] == "Lviv"
            assert row[1] == "Lviv Oblast"
            assert row[2] == "Ukraine"
        finally:
            db.close()

    def test_v3_db_migrates_to_v4(self, tmp_path):
        db_path = tmp_path / "test.db"
        # Build a v3 database manually
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE processed_images (
                file_path TEXT PRIMARY KEY, file_size INTEGER, file_mtime REAL,
                tags TEXT, scene_summary TEXT, processed_at TEXT, status TEXT,
                error_message TEXT, scene_category TEXT, emotional_tone TEXT,
                cleanup_class TEXT, has_text INTEGER DEFAULT 0, text_summary TEXT,
                event_hint TEXT, significance TEXT
            )"""
        )
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        conn.close()

        db = ProgressDB(db_path=db_path)
        try:
            ver = db._conn.execute("PRAGMA user_version").fetchone()[0]
            assert ver == 4
            cols = {
                row[1] for row in db._conn.execute("PRAGMA table_info(processed_images)").fetchall()
            }
            assert "nearest_city" in cols
        finally:
            db.close()


class TestQueryImages:
    def _populate(self, db: ProgressDB, tmp_path) -> None:
        images = [
            ("a.jpg", ["cat", "indoor"], "tabby on couch", None, False, "home", "UA"),
            ("b.jpg", ["cat", "outdoor"], "cat in garden", "review", False, "Kyiv", "UA"),
            ("c.jpg", ["dog", "outdoor"], "labrador running", "delete", False, "Berlin", "DE"),
            ("d.jpg", ["cat", "sign"], "street sign with text", None, True, "Paris", "FR"),
        ]
        for name, tags, summary, cleanup, has_text, city, country in images:
            img = _make_image(tmp_path, name)
            result = ImageResult(
                file_path=str(img),
                file_name=name,
                tags=tags,
                scene_summary=summary,
                cleanup_class=cleanup,
                has_text=has_text,
                nearest_city=city,
                nearest_country=country,
            )
            db.mark_done(img, result)

    def test_query_no_filters_returns_all(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images()
            assert len(rows) == 4
        finally:
            db.close()

    def test_query_by_tag_exact(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images(tag="cat")
            assert len(rows) == 3
            for r in rows:
                assert "cat" in r["tags_list"]
        finally:
            db.close()

    def test_query_by_tag_substring(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images(tag="sig")  # matches "sign" only
            assert len(rows) == 1
            assert "sign" in rows[0]["tags_list"]
        finally:
            db.close()

    def test_query_has_text_true(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images(has_text=True)
            assert len(rows) == 1
            assert rows[0]["file_name"] == "d.jpg"
        finally:
            db.close()

    def test_query_has_text_false(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images(has_text=False)
            assert len(rows) == 3
        finally:
            db.close()

    def test_query_by_cleanup_class(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images(cleanup_class="delete")
            assert len(rows) == 1
            assert rows[0]["cleanup_class"] == "delete"
        finally:
            db.close()

    def test_query_by_city(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images(city="kyiv")
            assert len(rows) == 1
            assert rows[0]["nearest_city"] == "Kyiv"
        finally:
            db.close()

    def test_query_by_country(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images(country="UA")
            assert len(rows) == 2
        finally:
            db.close()

    def test_query_combined_filters(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images(tag="cat", country="UA")
            assert len(rows) == 2
        finally:
            db.close()

    def test_query_limit(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images(limit=2)
            assert len(rows) == 2
        finally:
            db.close()

    def test_query_returns_location_fields(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images(city="Paris")
            assert len(rows) == 1
            assert rows[0]["nearest_city"] == "Paris"
            assert rows[0]["nearest_country"] == "FR"
        finally:
            db.close()

    def test_query_no_match_returns_empty(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            rows = db.query_images(tag="unicorn")
            assert rows == []
        finally:
            db.close()


class TestTagCounts:
    def _populate(self, db: ProgressDB, tmp_path) -> None:
        data = [
            ("a.jpg", ["cat", "indoor"]),
            ("b.jpg", ["cat", "outdoor"]),
            ("c.jpg", ["dog", "outdoor"]),
        ]
        for name, tags in data:
            img = _make_image(tmp_path, name)
            db.mark_done(img, ImageResult(file_path=str(img), file_name=name, tags=tags))

    def test_get_tag_counts_returns_all_tags(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            counts = dict(db.get_tag_counts())
            assert counts["cat"] == 2
            assert counts["outdoor"] == 2
            assert counts["dog"] == 1
            assert counts["indoor"] == 1
        finally:
            db.close()

    def test_get_tag_counts_sorted_by_count_desc(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            counts = db.get_tag_counts()
            values = [c for _, c in counts]
            assert values == sorted(values, reverse=True)
        finally:
            db.close()

    def test_get_tag_counts_empty_db(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            assert db.get_tag_counts() == []
        finally:
            db.close()


class TestRenameTag:
    def _populate(self, db: ProgressDB, tmp_path) -> None:
        data = [
            ("a.jpg", ["cat", "indoor"]),
            ("b.jpg", ["cat", "outdoor"]),
            ("c.jpg", ["dog", "outdoor"]),
        ]
        for name, tags in data:
            img = _make_image(tmp_path, name)
            db.mark_done(img, ImageResult(file_path=str(img), file_name=name, tags=tags))

    def test_rename_updates_matching_images(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            count = db.rename_tag("cat", "feline")
            assert count == 2
            rows = db.query_images(tag="feline")
            assert len(rows) == 2
        finally:
            db.close()

    def test_rename_removes_old_tag(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            db.rename_tag("cat", "feline")
            rows = db.query_images(tag="cat")
            assert rows == []
        finally:
            db.close()

    def test_rename_skips_unrelated_images(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            count = db.rename_tag("cat", "feline")
            # dog image is unaffected
            rows = db.query_images(tag="dog")
            assert len(rows) == 1
            assert count == 2
        finally:
            db.close()

    def test_rename_case_insensitive(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            count = db.rename_tag("CAT", "feline")
            assert count == 2
        finally:
            db.close()

    def test_rename_deduplicates_when_new_tag_already_present(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img = _make_image(tmp_path, "x.jpg")
        try:
            db.mark_done(
                img, ImageResult(file_path=str(img), file_name="x.jpg", tags=["cat", "feline"])
            )
            db.rename_tag("cat", "feline")
            rows = db.query_images(tag="feline")
            assert len(rows[0]["tags_list"]) == 1
        finally:
            db.close()

    def test_rename_returns_zero_when_tag_not_found(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            count = db.rename_tag("unicorn", "rainbow")
            assert count == 0
        finally:
            db.close()


class TestDeleteTag:
    def _populate(self, db: ProgressDB, tmp_path) -> None:
        data = [
            ("a.jpg", ["cat", "indoor"]),
            ("b.jpg", ["cat", "outdoor"]),
            ("c.jpg", ["dog", "outdoor"]),
        ]
        for name, tags in data:
            img = _make_image(tmp_path, name)
            db.mark_done(img, ImageResult(file_path=str(img), file_name=name, tags=tags))

    def test_delete_removes_tag_from_matching_images(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            count = db.delete_tag("cat")
            assert count == 2
            rows = db.query_images(tag="cat")
            assert rows == []
        finally:
            db.close()

    def test_delete_leaves_other_tags_intact(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            db.delete_tag("cat")
            rows = db.query_images(tag="indoor")
            assert len(rows) == 1
        finally:
            db.close()

    def test_delete_case_insensitive(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            count = db.delete_tag("CAT")
            assert count == 2
        finally:
            db.close()

    def test_delete_returns_zero_when_not_found(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            count = db.delete_tag("unicorn")
            assert count == 0
        finally:
            db.close()


class TestMergeTags:
    def _populate(self, db: ProgressDB, tmp_path) -> None:
        data = [
            ("a.jpg", ["cat", "indoor"]),
            ("b.jpg", ["cat", "outdoor"]),
            ("c.jpg", ["dog", "outdoor"]),
        ]
        for name, tags in data:
            img = _make_image(tmp_path, name)
            db.mark_done(img, ImageResult(file_path=str(img), file_name=name, tags=tags))

    def test_merge_adds_target_and_removes_source(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            count = db.merge_tags("cat", "feline")
            assert count == 2
            assert db.query_images(tag="cat") == []
            assert len(db.query_images(tag="feline")) == 2
        finally:
            db.close()

    def test_merge_does_not_duplicate_target(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        img = _make_image(tmp_path, "x.jpg")
        try:
            db.mark_done(
                img, ImageResult(file_path=str(img), file_name="x.jpg", tags=["cat", "feline"])
            )
            db.merge_tags("cat", "feline")
            rows = db.query_images(tag="feline")
            assert len(rows[0]["tags_list"]) == 1
        finally:
            db.close()

    def test_merge_leaves_unrelated_images_unchanged(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            db.merge_tags("cat", "feline")
            rows = db.query_images(tag="dog")
            assert len(rows) == 1
            assert "feline" not in rows[0]["tags_list"]
        finally:
            db.close()

    def test_merge_returns_zero_when_source_not_found(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            self._populate(db, tmp_path)
            count = db.merge_tags("unicorn", "animal")
            assert count == 0
        finally:
            db.close()
