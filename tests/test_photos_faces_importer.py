"""Tests for photos_faces_importer (photoscript & osascript mocked).

The real photoscript ``PhotosLibrary`` does not expose a ``persons()``
method; persons are surfaced only at the photo level via
``Photo.persons`` (a list of name strings). These tests stub that
contract and exercise the importer's name → uuid grouping plus its
single-face-photo assignment logic.

Tests mock both the bulk osascript path and the photoscript fallback —
no test is allowed to call the real Photos library, so each fixture
forces a specific code path.
"""

from __future__ import annotations

import contextlib
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.models import FaceDetection
from pyimgtag.photos_faces_importer import import_photos_persons


def _mock_photo(uuid: str, persons: list[str]) -> MagicMock:
    photo = MagicMock()
    photo.uuid = uuid
    photo.persons = persons
    return photo


@contextlib.contextmanager
def _photoscript_only(library: MagicMock):
    """Force the slow photoscript fallback by disabling osascript.

    Pre-existing behavioural tests run through this fixture so they
    keep covering the same name → uuid grouping logic regardless of
    which collection path is preferred.
    """
    mock_ps = MagicMock()
    mock_ps.PhotosLibrary.return_value = library
    with (
        patch("pyimgtag.photos_faces_importer._has_photoscript", new=lambda: True),
        patch("pyimgtag.photos_faces_importer.is_applescript_available", new=lambda: False),
        patch.dict("sys.modules", {"photoscript": mock_ps}),
    ):
        yield


@contextlib.contextmanager
def _bulk_applescript_returns(stdout: str, *, returncode: int = 0):
    """Force the bulk-AppleScript path with a canned osascript stdout."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = ""
    proc.returncode = returncode
    with (
        patch("pyimgtag.photos_faces_importer.is_applescript_available", new=lambda: True),
        patch(
            "pyimgtag.photos_faces_importer.subprocess.run",
            return_value=proc,
        ) as run_mock,
    ):
        yield run_mock


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

            with _photoscript_only(library):
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

            with _photoscript_only(library):
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

            with _photoscript_only(library):
                imported, skipped = import_photos_persons(db)

            assert imported == 0
            assert skipped == 0
            assert db.get_persons() == []

    def test_skips_photo_not_in_faces_db(self, tmp_path):
        with self._make_db(tmp_path) as db:
            library = MagicMock()
            library.photos.return_value = [_mock_photo("notindb", ["Carol"])]

            with _photoscript_only(library):
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

            with _photoscript_only(library):
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

            with _photoscript_only(library):
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

            with _photoscript_only(library):
                imported, _ = import_photos_persons(db)

            # The good photo still produces its person row.
            assert imported == 1
            assert {p.label for p in db.get_persons()} == {"Alice"}

    def test_neither_path_available_raises(self, tmp_path):
        """When osascript and photoscript are both unavailable, raise."""
        with self._make_db(tmp_path) as db:
            with (
                patch(
                    "pyimgtag.photos_faces_importer.is_applescript_available",
                    new=lambda: False,
                ),
                patch("pyimgtag.photos_faces_importer._has_photoscript", new=lambda: False),
            ):
                with pytest.raises(RuntimeError, match="Neither osascript nor photoscript"):
                    import_photos_persons(db)


class TestBulkAppleScriptPath:
    """Cover the fast path: one osascript call returns ``<uuid>\\t<persons>``."""

    def _make_db(self, tmp_path):
        from pyimgtag.progress_db import ProgressDB

        return ProgressDB(db_path=tmp_path / "test.db")

    def test_parses_bulk_output_into_name_to_uuids(self, tmp_path):
        """Happy path: osascript returns multiple rows, all parsed."""
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
            stdout = (
                "abc123\tAlice|\n"
                "def456\tBob|Carol|\n"
                "ghi789\t\n"  # photo with no persons — skipped
            )
            with _bulk_applescript_returns(stdout):
                imported, _ = import_photos_persons(db)

            labels = sorted(p.label for p in db.get_persons())
            assert labels == ["Alice", "Bob", "Carol"]
            assert imported == 3
            alice = next(p for p in db.get_persons() if p.label == "Alice")
            # Single-face photo gets auto-assigned.
            assert len(alice.face_ids) == 1

    def test_skips_malformed_lines(self, tmp_path):
        """Blank lines, lines with no tab, and embedded pipes don't crash."""
        with self._make_db(tmp_path) as db:
            stdout = (
                "\n"  # blank
                "no-tab-here-just-text\n"  # missing tab
                "\tEve|\n"  # empty uuid
                "uuid_a\t\n"  # uuid with no persons
                "uuid_b\tDave\n"  # last persons field has no trailing pipe
                "uuid_c\t  \n"  # whitespace-only name list
                "uuid_d\tFrank|Grace|\n"
            )
            with _bulk_applescript_returns(stdout):
                imported, _ = import_photos_persons(db)

            # "Eve" never lands because the uuid was empty; only Dave,
            # Frank, Grace make it through.
            labels = sorted(p.label for p in db.get_persons())
            assert labels == ["Dave", "Frank", "Grace"]
            assert imported == 3

    def test_falls_back_when_osascript_fails(self, tmp_path):
        """Non-zero osascript exit triggers the photoscript fallback."""
        with self._make_db(tmp_path) as db:
            library = MagicMock()
            library.photos.return_value = [_mock_photo("uuidX", ["Hannah"])]

            mock_ps = MagicMock()
            mock_ps.PhotosLibrary.return_value = library
            failed = MagicMock(returncode=1, stdout="", stderr="permission denied")

            with (
                patch(
                    "pyimgtag.photos_faces_importer.is_applescript_available",
                    new=lambda: True,
                ),
                patch(
                    "pyimgtag.photos_faces_importer.subprocess.run",
                    return_value=failed,
                ),
                patch("pyimgtag.photos_faces_importer._has_photoscript", new=lambda: True),
                patch.dict("sys.modules", {"photoscript": mock_ps}),
            ):
                imported, _ = import_photos_persons(db)

            assert {p.label for p in db.get_persons()} == {"Hannah"}
            assert imported == 1

    def test_falls_back_on_osascript_timeout(self, tmp_path):
        """A subprocess timeout falls through to photoscript."""
        with self._make_db(tmp_path) as db:
            library = MagicMock()
            library.photos.return_value = [_mock_photo("uuidY", ["Ivan"])]

            mock_ps = MagicMock()
            mock_ps.PhotosLibrary.return_value = library

            def boom(*_a, **_kw):
                raise subprocess.TimeoutExpired(cmd="osascript", timeout=1)

            with (
                patch(
                    "pyimgtag.photos_faces_importer.is_applescript_available",
                    new=lambda: True,
                ),
                patch("pyimgtag.photos_faces_importer.subprocess.run", side_effect=boom),
                patch("pyimgtag.photos_faces_importer._has_photoscript", new=lambda: True),
                patch.dict("sys.modules", {"photoscript": mock_ps}),
            ):
                imported, _ = import_photos_persons(db)

            assert imported == 1


class TestProgressOutput:
    """Confirm the user always sees activity, even without ``--verbose``."""

    def _make_db(self, tmp_path):
        from pyimgtag.progress_db import ProgressDB

        return ProgressDB(db_path=tmp_path / "test.db")

    def test_emits_banner_and_periodic_lines(self, tmp_path):
        """Banner + at least one periodic counter must be emitted.

        Generates 250 fake photos so the 200-photo cadence fires once.
        """
        from pyimgtag.photos_faces_importer import _PROGRESS_EVERY

        with self._make_db(tmp_path) as db:
            # 250 photos, alternating with one named person each.
            lines = [f"uuid_{i:04d}\tPerson_{i}|\n" for i in range(_PROGRESS_EVERY + 50)]
            stdout = "".join(lines)
            messages: list[str] = []

            with _bulk_applescript_returns(stdout):
                import_photos_persons(db, progress=messages.append)

            # Banner must appear regardless of verbosity.
            assert any("Scanning Photos library" in m for m in messages), (
                f"missing banner; got {messages!r}"
            )
            # AppleScript-specific banner must mention osascript work.
            assert any("AppleScript" in m for m in messages), (
                f"missing AppleScript banner; got {messages!r}"
            )
            # At least one periodic counter line.
            assert any(m.startswith("\r[faces] processed") for m in messages), (
                f"missing periodic line; got {messages!r}"
            )
            # Final summary.
            assert any("import complete" in m for m in messages), (
                f"missing final summary; got {messages!r}"
            )


class TestKeyboardInterruptHandling:
    """Ctrl-C must produce a clean abort message + non-zero exit, not a traceback."""

    def test_cli_wrapper_catches_keyboard_interrupt(self, tmp_path, capsys):
        """The CLI handler turns KeyboardInterrupt into exit code 130."""
        from pyimgtag.commands.faces import _handle_faces_import_photos

        # Touch a fresh DB so ProgressDB has somewhere to open.
        db_path = tmp_path / "test.db"

        args = MagicMock()
        args.db = str(db_path)

        with patch(
            "pyimgtag.photos_faces_importer.import_photos_persons",
            side_effect=KeyboardInterrupt(),
        ):
            rc = _handle_faces_import_photos(args)

        assert rc == 130
        captured = capsys.readouterr()
        assert "Aborted" in captured.err


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
