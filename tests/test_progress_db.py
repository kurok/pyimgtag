"""Tests for SQLite progress database."""

from __future__ import annotations

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
