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

    def test_bulk_script_every_person_form(self):
        """Default bulk script uses ``name of every person of p``.

        That's the photoscript-canonical form and works on the vast
        majority of macOS Photos.app builds. The older ``persons of p``
        form silently returned zero persons across whole libraries.
        """
        from pyimgtag.photos_faces_importer import _bulk_applescript_every_person

        script = _bulk_applescript_every_person()
        assert "name of every person of p" in script
        # Per-photo ``on error`` keeps a single bad row from killing the
        # whole traversal.
        assert "on error" in script
        assert "set name_list to {}" in script

    def test_bulk_script_persons_property_fallback_avoids_class_identifier(self):
        """The fallback script must NOT name ``person`` as a class.

        On Photos.app builds where ``person`` isn't terminologised as a
        class (osascript ``-2741: Expected class name but found
        identifier``), the entire script fails to compile. The fallback
        uses only the ``persons`` *property* and indexes into it via
        ``item i of _persons``; ``name of <ref>`` works on any object,
        so the class identifier is never required.
        """
        from pyimgtag.photos_faces_importer import _bulk_applescript_persons_property

        script = _bulk_applescript_persons_property()
        assert "persons of p" in script
        # Index iteration — no ``every person`` anywhere.
        assert "every person" not in script
        assert "item i of _persons" in script
        assert "count of _persons" in script

    def test_bulk_runs_fallback_on_parse_error(self, tmp_path):
        """When osascript returns ``-2741`` for the first script, the
        importer must invoke a SECOND osascript call with the
        property-based script — the user's ``person``-class-less
        Photos.app keeps producing 0 persons otherwise."""
        from pyimgtag import photos_faces_importer

        with self._make_db(tmp_path) as db:
            calls: list[str] = []

            def _fake_run(cmd, **_kw):
                # cmd[2] is the AppleScript source.
                calls.append(cmd[2])
                proc = MagicMock()
                if "every person of p" in cmd[2]:
                    # First script: simulate the -2741 parse failure.
                    proc.returncode = 1
                    proc.stdout = ""
                    proc.stderr = (
                        "osascript: 225:231: syntax error: Expected class "
                        "name but found identifier. (-2741)"
                    )
                else:
                    # Fallback script succeeds and returns a real row.
                    proc.returncode = 0
                    proc.stdout = "abc123\tAlice|\n"
                    proc.stderr = ""
                return proc

            with (
                patch.object(photos_faces_importer, "is_applescript_available", new=lambda: True),
                patch.object(photos_faces_importer.subprocess, "run", side_effect=_fake_run),
            ):
                imported, _ = import_photos_persons(db)

            assert len(calls) == 2, "fallback must trigger one extra osascript call"
            assert "every person of p" in calls[0]
            assert "every person" not in calls[1], (
                "fallback script must avoid the 'person' class identifier"
            )
            assert imported == 1
            labels = sorted(p.label for p in db.get_persons())
            assert labels == ["Alice"]

    def test_app_people_script_walks_application_collection(self):
        """The third-tier fallback queries Photos' application-level
        ``people`` collection and emits one ``<uuid>\\t<name>`` row per
        photo×person. Some macOS Photos.app builds expose persons only
        at the app level (the user's "People" sidebar shows named
        items) even though no per-media-item accessor returns them."""
        from pyimgtag.photos_faces_importer import _bulk_applescript_app_people

        script = _bulk_applescript_app_people()
        # Walks photos via the persons collection, not the other way.
        assert "photos of p" in script
        # Tries each plausible identifier — historic ``every person``
        # at the app level, the ``persons`` plural property, and the
        # UI-facing ``people`` term.
        assert "every person" in script
        assert "set _people_list to persons" in script
        assert "set _people_list to people" in script
        # Per-photo and per-person ``try`` blocks keep one bad row from
        # killing the whole traversal.
        assert "try" in script

    def test_app_people_fallback_runs_when_per_photo_path_returns_zero(self, tmp_path):
        """When the per-media-item scripts execute cleanly but report
        zero persons across the entire library, the importer must fire
        the app-level walker before giving up. Otherwise users with
        Photos.app builds that surface persons only via the application
        collection silently get 0 imports."""
        from pyimgtag import photos_faces_importer

        with self._make_db(tmp_path) as db:
            calls: list[str] = []

            def _fake_run(cmd, **_kw):
                calls.append(cmd[2])
                proc = MagicMock()
                proc.stderr = ""
                if "every person of p" in cmd[2]:
                    # Per-photo script ran successfully — but produced
                    # no ``<uuid>\t<name>`` lines (every row was the
                    # empty trailing-tab form).
                    proc.returncode = 0
                    proc.stdout = "ph1\t\nph2\t\n"
                elif "photos of p" in cmd[2]:
                    # App-level walker: one row per (photo, person) pair.
                    proc.returncode = 0
                    proc.stdout = "ph1\tAlice\nph2\tAlice\nph2\tBob\n"
                else:
                    proc.returncode = 0
                    proc.stdout = ""
                return proc

            with (
                patch.object(photos_faces_importer, "is_applescript_available", new=lambda: True),
                patch.object(photos_faces_importer.subprocess, "run", side_effect=_fake_run),
            ):
                imported, _ = import_photos_persons(db)

            # First call = every-person; second call = app-level walker.
            assert len(calls) == 2
            assert "every person of p" in calls[0]
            assert "photos of p" in calls[1]
            assert imported == 2  # Alice + Bob materialised
            labels = sorted(p.label for p in db.get_persons())
            assert labels == ["Alice", "Bob"]

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
