"""Tests for photos_faces_importer (photoscript mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.models import FaceDetection
from pyimgtag.photos_faces_importer import import_photos_persons


class TestImportPhotosPersons:
    def _make_db(self, tmp_path):
        from pyimgtag.progress_db import ProgressDB

        return ProgressDB(db_path=tmp_path / "test.db")

    def test_imports_single_face_photo(self, tmp_path):
        with self._make_db(tmp_path) as db:
            det = FaceDetection(
                image_path="/photos/abc123.jpg",
                bbox_x=0,
                bbox_y=0,
                bbox_w=50,
                bbox_h=50,
                confidence=0.9,
            )
            fid = db.insert_face("/photos/abc123.jpg", det)

            mock_photo = MagicMock()
            mock_photo.uuid = "abc123"
            mock_person = MagicMock()
            mock_person.name = "Alice"
            mock_person.photos.return_value = [mock_photo]

            mock_library = MagicMock()
            mock_library.persons.return_value = [mock_person]

            mock_ps = MagicMock()
            mock_ps.PhotosLibrary.return_value = mock_library

            with (
                patch("pyimgtag.photos_faces_importer._has_photoscript", new=lambda: True),
                patch.dict("sys.modules", {"photoscript": mock_ps}),
            ):
                imported, skipped = import_photos_persons(db)

            assert imported == 1
            assert skipped == 0
            persons = db.get_persons()
            assert len(persons) == 1
            assert persons[0].label == "Alice"
            assert persons[0].source == "photos"
            assert persons[0].trusted is True
            assert fid in persons[0].face_ids

    def test_skips_multi_face_photo(self, tmp_path):
        with self._make_db(tmp_path) as db:
            det1 = FaceDetection(
                image_path="/photos/uuid1.jpg",
                bbox_x=0,
                bbox_y=0,
                bbox_w=30,
                bbox_h=30,
                confidence=0.9,
            )
            det2 = FaceDetection(
                image_path="/photos/uuid1.jpg",
                bbox_x=100,
                bbox_y=0,
                bbox_w=30,
                bbox_h=30,
                confidence=0.9,
            )
            db.insert_face("/photos/uuid1.jpg", det1)
            db.insert_face("/photos/uuid1.jpg", det2)

            mock_photo = MagicMock()
            mock_photo.uuid = "uuid1"
            mock_person = MagicMock()
            mock_person.name = "Bob"
            mock_person.photos.return_value = [mock_photo]

            mock_library = MagicMock()
            mock_library.persons.return_value = [mock_person]

            mock_ps = MagicMock()
            mock_ps.PhotosLibrary.return_value = mock_library

            with (
                patch("pyimgtag.photos_faces_importer._has_photoscript", new=lambda: True),
                patch.dict("sys.modules", {"photoscript": mock_ps}),
            ):
                imported, skipped = import_photos_persons(db)

            assert imported == 1
            assert skipped == 1  # multi-face photo flagged
            persons = db.get_persons()
            assert len(persons) == 1
            # No face assignment on multi-face photo
            assert len(persons[0].face_ids) == 0

    def test_skips_unnamed_persons(self, tmp_path):
        with self._make_db(tmp_path) as db:
            mock_person = MagicMock()
            mock_person.name = ""
            mock_person.photos.return_value = []

            mock_library = MagicMock()
            mock_library.persons.return_value = [mock_person]

            mock_ps = MagicMock()
            mock_ps.PhotosLibrary.return_value = mock_library

            with (
                patch("pyimgtag.photos_faces_importer._has_photoscript", new=lambda: True),
                patch.dict("sys.modules", {"photoscript": mock_ps}),
            ):
                imported, skipped = import_photos_persons(db)

            assert imported == 0
            assert skipped == 0
            assert db.get_persons() == []

    def test_skips_photo_not_in_faces_db(self, tmp_path):
        with self._make_db(tmp_path) as db:
            mock_photo = MagicMock()
            mock_photo.uuid = "notindb"
            mock_person = MagicMock()
            mock_person.name = "Carol"
            mock_person.photos.return_value = [mock_photo]

            mock_library = MagicMock()
            mock_library.persons.return_value = [mock_person]

            mock_ps = MagicMock()
            mock_ps.PhotosLibrary.return_value = mock_library

            with (
                patch("pyimgtag.photos_faces_importer._has_photoscript", new=lambda: True),
                patch.dict("sys.modules", {"photoscript": mock_ps}),
            ):
                imported, skipped = import_photos_persons(db)

            # Person is created but no face assigned
            assert imported == 1
            persons = db.get_persons()
            assert persons[0].label == "Carol"
            assert len(persons[0].face_ids) == 0

    def test_idempotency_skips_existing_photos_person(self, tmp_path):
        with self._make_db(tmp_path) as db:
            det = FaceDetection(
                image_path="/photos/abc123.jpg",
                bbox_x=0,
                bbox_y=0,
                bbox_w=50,
                bbox_h=50,
                confidence=0.9,
            )
            db.insert_face("/photos/abc123.jpg", det)

            mock_photo = MagicMock()
            mock_photo.uuid = "abc123"
            mock_person = MagicMock()
            mock_person.name = "Alice"
            mock_person.photos.return_value = [mock_photo]

            mock_library = MagicMock()
            mock_library.persons.return_value = [mock_person]

            mock_ps = MagicMock()
            mock_ps.PhotosLibrary.return_value = mock_library

            with (
                patch("pyimgtag.photos_faces_importer._has_photoscript", new=lambda: True),
                patch.dict("sys.modules", {"photoscript": mock_ps}),
            ):
                # First import
                imported1, _ = import_photos_persons(db)
                # Second import — should skip the already-existing person
                imported2, _ = import_photos_persons(db)

            assert imported1 == 1
            assert imported2 == 0
            # Still only one person row
            assert len(db.get_persons()) == 1

    def test_photoscript_unavailable_raises(self, tmp_path):
        with self._make_db(tmp_path) as db:
            with patch("pyimgtag.photos_faces_importer._has_photoscript", new=lambda: False):
                with pytest.raises(RuntimeError, match="photoscript"):
                    import_photos_persons(db)


class TestLazyPhotoscriptImport:
    def test_importing_module_does_not_import_photoscript(self):
        import importlib
        import sys

        mod_name = "pyimgtag.photos_faces_importer"
        saved = sys.modules.pop(mod_name, None)
        ps_saved = sys.modules.pop("photoscript", None)
        try:
            importlib.import_module(mod_name)
            assert "photoscript" not in sys.modules, (
                "photoscript was imported at module level in photos_faces_importer"
            )
        finally:
            if saved is not None:
                sys.modules[mod_name] = saved
            if ps_saved is not None:
                sys.modules["photoscript"] = ps_saved
