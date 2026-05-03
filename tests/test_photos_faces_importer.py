"""Tests for photos_faces_importer (photoscript mocked).

The real photoscript ``PhotosLibrary`` does not expose a ``persons()``
method; persons are surfaced only at the photo level via
``Photo.persons`` (a list of name strings). These tests stub that
contract and exercise the importer's name → uuid grouping plus its
single-face-photo assignment logic.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.models import FaceDetection
from pyimgtag.photos_faces_importer import import_photos_persons


def _mock_photo(uuid: str, persons: list[str]) -> MagicMock:
    photo = MagicMock()
    photo.uuid = uuid
    photo.persons = persons
    return photo


def _patch_photoscript(library: MagicMock):
    """Return the context-manager pair used by every test below."""
    mock_ps = MagicMock()
    mock_ps.PhotosLibrary.return_value = library
    return (
        patch("pyimgtag.photos_faces_importer._has_photoscript", new=lambda: True),
        patch.dict("sys.modules", {"photoscript": mock_ps}),
    )


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

            library = MagicMock()
            library.photos.return_value = [_mock_photo("abc123", ["Alice"])]

            ps_patch, sys_patch = _patch_photoscript(library)
            with ps_patch, sys_patch:
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

            library = MagicMock()
            library.photos.return_value = [_mock_photo("uuid1", ["Bob"])]

            ps_patch, sys_patch = _patch_photoscript(library)
            with ps_patch, sys_patch:
                imported, skipped = import_photos_persons(db)

            assert imported == 1
            assert skipped == 1  # multi-face photo flagged
            persons = db.get_persons()
            assert len(persons) == 1
            # No face assignment on multi-face photo
            assert len(persons[0].face_ids) == 0

    def test_skips_unnamed_persons(self, tmp_path):
        with self._make_db(tmp_path) as db:
            library = MagicMock()
            # Photo with an empty / blank-string person tag is ignored.
            library.photos.return_value = [
                _mock_photo("ph1", []),
                _mock_photo("ph2", [""]),
                _mock_photo("ph3", ["   "]),
            ]

            ps_patch, sys_patch = _patch_photoscript(library)
            with ps_patch, sys_patch:
                imported, skipped = import_photos_persons(db)

            assert imported == 0
            assert skipped == 0
            assert db.get_persons() == []

    def test_skips_photo_not_in_faces_db(self, tmp_path):
        with self._make_db(tmp_path) as db:
            library = MagicMock()
            library.photos.return_value = [_mock_photo("notindb", ["Carol"])]

            ps_patch, sys_patch = _patch_photoscript(library)
            with ps_patch, sys_patch:
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

            library = MagicMock()
            library.photos.return_value = [_mock_photo("abc123", ["Alice"])]

            ps_patch, sys_patch = _patch_photoscript(library)
            with ps_patch, sys_patch:
                imported1, _ = import_photos_persons(db)
                imported2, _ = import_photos_persons(db)

            assert imported1 == 1
            assert imported2 == 0
            # Still only one person row
            assert len(db.get_persons()) == 1

    def test_photo_in_multiple_persons(self, tmp_path):
        """A photo tagged with multiple names contributes to every person.

        Real-world Apple Photos: a group shot has multiple persons. The
        importer must create one row per name and (for a single-face
        photo, which is unusual but possible if the user manually tagged
        the same face with two names) assign that face once to whichever
        person row reaches it first."""
        with self._make_db(tmp_path) as db:
            det = FaceDetection(
                image_path="/photos/group.jpg",
                bbox_x=0,
                bbox_y=0,
                bbox_w=30,
                bbox_h=30,
                confidence=0.9,
            )
            db.insert_face("/photos/group.jpg", det)

            library = MagicMock()
            library.photos.return_value = [_mock_photo("group", ["Alice", "Bob"])]

            ps_patch, sys_patch = _patch_photoscript(library)
            with ps_patch, sys_patch:
                imported, _ = import_photos_persons(db)

            assert imported == 2
            labels = sorted(p.label for p in db.get_persons())
            assert labels == ["Alice", "Bob"]

    def test_unreadable_photo_metadata_is_skipped(self, tmp_path):
        """``photoscript`` raises on iCloud-only photos the AppleScript
        bridge cannot resolve. The importer must log and continue rather
        than abort the whole import."""
        with self._make_db(tmp_path) as db:
            good = _mock_photo("good", ["Alice"])

            bad = MagicMock()
            type(bad).persons = property(
                lambda self: (_ for _ in ()).throw(RuntimeError("AppleEvent timeout"))
            )
            type(bad).uuid = "bad"

            library = MagicMock()
            library.photos.return_value = [bad, good]

            ps_patch, sys_patch = _patch_photoscript(library)
            with ps_patch, sys_patch:
                imported, _ = import_photos_persons(db)

            # The good photo still produces its person row.
            assert imported == 1
            assert {p.label for p in db.get_persons()} == {"Alice"}

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
