"""Tests for photos_importer (photoscript & osascript mocked).

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

import numpy as np
import pytest

from pyimgtag.face.photos_importer import (
    _assign_faces_to_person,
    _OsxphotosUnavailable,
    import_photos_persons,
)
from pyimgtag.models import FaceDetection


def _mock_photo(uuid: str, persons: list[str]) -> MagicMock:
    photo = MagicMock()
    photo.uuid = uuid
    photo.persons = persons
    return photo


def _fake_osxphotos(persons: list[tuple[str, list[str]]]):
    """Build a stand-in ``osxphotos`` module whose ``PhotosDB().person_info``
    yields the given ``(name, [uuid, ...])`` people."""
    import types

    class _Photo:
        def __init__(self, uuid):
            self.uuid = uuid

    class _Person:
        def __init__(self, name, uuids):
            self.name = name
            self.photos = [_Photo(u) for u in uuids]

    class _DB:
        def __init__(self, *args, **kwargs):
            pass

        @property
        def person_info(self):
            return [_Person(n, u) for n, u in persons]

    module = types.ModuleType("osxphotos")
    module.PhotosDB = _DB
    return module


@pytest.fixture(autouse=True)
def _osxphotos_absent():
    """Disable the osxphotos reader for the whole module so the AppleScript /
    photoscript enumeration paths (what these tests cover) run, and no test
    ever touches the real Photos library. osxphotos is now the preferred path;
    setting it to ``None`` in ``sys.modules`` makes ``import osxphotos`` raise.
    The osxphotos-specific tests inject a fake module to override this."""
    with patch.dict("sys.modules", {"osxphotos": None}):
        yield


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
        patch("pyimgtag.face.photos_importer._has_photoscript", new=lambda: True),
        patch("pyimgtag.face.photos_importer.is_applescript_available", new=lambda: False),
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
        patch("pyimgtag.face.photos_importer.is_applescript_available", new=lambda: True),
        patch(
            "pyimgtag.face.photos_importer.subprocess.run",
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

    def test_reimport_links_faces_scanned_after_initial_import(self, tmp_path):
        with self._make_db(tmp_path) as db:
            library = MagicMock()
            library.photos.return_value = [_mock_photo("uuid99", ["Alice"])]

            # First import: no faces in DB yet — person created with 0 faces.
            with _photoscript_only(library):
                imported, _ = import_photos_persons(db)
            assert imported == 1
            persons = db.get_persons()
            assert len(persons[0].face_ids) == 0

            # Faces scan runs — adds a face for Alice's photo.
            det = FaceDetection(
                image_path="/photos/uuid99.jpg",
                bbox_x=0,
                bbox_y=0,
                bbox_w=50,
                bbox_h=50,
                confidence=0.9,
            )
            db.insert_face("/photos/uuid99.jpg", det)

            # Second import: person already exists, but should link the new face.
            with _photoscript_only(library):
                imported2, _ = import_photos_persons(db)
            assert imported2 == 0  # no new person created
            persons = db.get_persons()
            assert len(persons[0].face_ids) == 1  # face now linked

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
        """When osxphotos, osascript, and photoscript are all unavailable, raise."""
        with self._make_db(tmp_path) as db:
            with (
                patch(
                    "pyimgtag.face.photos_importer.is_applescript_available",
                    new=lambda: False,
                ),
                patch("pyimgtag.face.photos_importer._has_photoscript", new=lambda: False),
            ):
                with pytest.raises(RuntimeError, match="Could not read the Photos library"):
                    import_photos_persons(db)


class TestAssignFacesMultiFace:
    """Embedding-based linking of group (multi-face) photos."""

    @staticmethod
    def _db(tmp_path):
        from pyimgtag.progress_db import ProgressDB

        return ProgressDB(db_path=tmp_path / "test.db")

    @staticmethod
    def _vec(*nonzero: tuple[int, float]) -> np.ndarray:
        v = np.zeros(128, dtype=np.float64)
        for idx, val in nonzero:
            v[idx] = val
        return v

    def _face(self, db, path: str, embedding=None) -> int:
        return db.insert_face(path, FaceDetection(image_path=path), embedding=embedding)

    def test_links_matching_face_in_group_photo(self, tmp_path):
        """The group-photo face closest to the person's centroid is linked."""
        with self._db(tmp_path) as db:
            pid = db.create_person(label="Alice", confirmed=True, source="photos", trusted=True)
            seed = self._vec((0, 1.0))
            solo = self._face(db, "/p/solo.jpg", embedding=seed)
            # Group shot: Alice (near seed) + a stranger (far away).
            alice = self._face(db, "/p/group.jpg", embedding=self._vec((0, 1.0), (1, 0.05)))
            stranger = self._face(db, "/p/group.jpg", embedding=self._vec((40, 1.0)))

            skipped = _assign_faces_to_person(db, pid, ["solo", "group"])

            assert skipped == 0
            faces = next(p for p in db.get_persons() if p.person_id == pid).face_ids
            assert solo in faces
            assert alice in faces
            assert stranger not in faces

    def test_group_photo_skipped_without_reference(self, tmp_path):
        """No seed (person only in a group shot) → left for manual review."""
        with self._db(tmp_path) as db:
            pid = db.create_person(label="Bob", confirmed=True, source="photos", trusted=True)
            self._face(db, "/p/group.jpg", embedding=self._vec((0, 1.0)))
            self._face(db, "/p/group.jpg", embedding=self._vec((40, 1.0)))

            skipped = _assign_faces_to_person(db, pid, ["group"])

            assert skipped == 1
            assert next(p for p in db.get_persons() if p.person_id == pid).face_ids == []

    def test_ambiguous_group_photo_skipped(self, tmp_path):
        """Two near-equally-close candidates fail the margin check → skipped."""
        with self._db(tmp_path) as db:
            pid = db.create_person(label="Carol", confirmed=True, source="photos", trusted=True)
            seed = self._vec((0, 1.0))
            solo = self._face(db, "/p/solo.jpg", embedding=seed)
            # Both group faces sit almost the same tiny distance from the seed.
            self._face(db, "/p/group.jpg", embedding=self._vec((0, 1.0), (1, 0.05)))
            self._face(db, "/p/group.jpg", embedding=self._vec((0, 1.0), (2, 0.06)))

            skipped = _assign_faces_to_person(db, pid, ["solo", "group"])

            # Solo seed is linked; the ambiguous group photo is not.
            assert skipped == 1
            faces = next(p for p in db.get_persons() if p.person_id == pid).face_ids
            assert faces == [solo]

    def test_seeds_from_existing_assignment(self, tmp_path):
        """An already-assigned reference face resolves a later group import."""
        with self._db(tmp_path) as db:
            pid = db.create_person(label="Dave", confirmed=True, source="photos", trusted=True)
            # Pre-existing reference face for Dave (e.g. from a prior import).
            ref = self._face(db, "/p/ref.jpg", embedding=self._vec((0, 1.0)))
            db.set_person_id(ref, pid)
            # New group photo only — no single-face seed in this batch.
            dave = self._face(db, "/p/grp.jpg", embedding=self._vec((0, 1.0), (1, 0.04)))
            self._face(db, "/p/grp.jpg", embedding=self._vec((40, 1.0)))

            skipped = _assign_faces_to_person(db, pid, ["grp"])

            assert skipped == 0
            assert dave in next(p for p in db.get_persons() if p.person_id == pid).face_ids

    @staticmethod
    def _owner(db, face_id: int) -> int | None:
        return db._conn.execute("SELECT person_id FROM faces WHERE id = ?", (face_id,)).fetchone()[
            0
        ]

    def test_reclaims_single_face_from_auto_cluster(self, tmp_path):
        """Regression: the scan's background recluster grabs a freshly-detected
        face into a ``Person N`` auto cluster before import-photos runs. The
        authoritative UUID match must reclaim it into the named person rather
        than leave the named person empty."""
        uuid = "AAAAAAAA-1111-2222-3333-444444444444"
        with self._db(tmp_path) as db:
            named = db.create_person(label="Alice", confirmed=True, source="photos", trusted=True)
            auto = db.create_person(label="Person 1", source="auto")
            fid = self._face(db, f"/lib/{uuid}.jpg", embedding=self._vec((0, 1.0)))
            db.set_person_id(fid, auto)  # recluster put Alice's face in the auto cluster

            skipped = _assign_faces_to_person(db, named, [uuid])

            assert skipped == 0
            assert self._owner(db, fid) == named  # reclaimed from the auto cluster

    def test_remaining_stranger_in_group_photo_not_direct_assigned(self, tmp_path):
        """Regression: a group photo whose only *candidate* face is a stranger
        (the person's own face is already assigned, e.g. on re-import) must go
        through the Phase 2 embedding check, not Phase 1 direct assignment —
        "single-face" means total faces in the photo, not remaining candidates."""
        with self._db(tmp_path) as db:
            pid = db.create_person(label="Alice", confirmed=True, source="photos", trusted=True)
            # Group shot: Alice's face was assigned by a prior import…
            alice = self._face(db, "/p/group.jpg", embedding=self._vec((0, 1.0)))
            db.set_person_id(alice, pid)
            # …leaving one unassigned stranger with an orthogonal embedding.
            stranger = self._face(db, "/p/group.jpg", embedding=self._vec((40, 1.0)))

            skipped = _assign_faces_to_person(db, pid, ["group"])

            assert skipped == 1  # left for manual review, not mis-assigned
            assert self._owner(db, stranger) is None

    def test_does_not_reclaim_from_trusted_person(self, tmp_path):
        """A face already owned by another trusted/confirmed person is never
        stolen, even on a UUID match."""
        uuid = "BBBBBBBB-1111-2222-3333-444444444444"
        with self._db(tmp_path) as db:
            named = db.create_person(label="Alice", confirmed=True, source="photos", trusted=True)
            other = db.create_person(label="Bob", confirmed=True, source="photos", trusted=True)
            fid = self._face(db, f"/lib/{uuid}.jpg", embedding=self._vec((0, 1.0)))
            db.set_person_id(fid, other)

            skipped = _assign_faces_to_person(db, named, [uuid])

            assert skipped == 0  # no candidate faces — nothing to do
            assert self._owner(db, fid) == other  # Bob keeps his face


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
        from pyimgtag.face.photos_importer import _bulk_applescript_every_person

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
        from pyimgtag.face.photos_importer import _bulk_applescript_persons_property

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
        from pyimgtag.face import photos_importer

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
                patch.object(photos_importer, "is_applescript_available", new=lambda: True),
                patch.object(photos_importer.subprocess, "run", side_effect=_fake_run),
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
        from pyimgtag.face.photos_importer import _bulk_applescript_app_people

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
        from pyimgtag.face import photos_importer

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
                patch.object(photos_importer, "is_applescript_available", new=lambda: True),
                patch.object(photos_importer.subprocess, "run", side_effect=_fake_run),
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

    def test_strips_photos_id_suffix_from_uuid(self, tmp_path):
        """Regression: Photos 5+ AppleScript media-item ids are
        ``<UUID>/L0/001``; the suffix must be stripped or the
        ``get_faces_by_uuid`` LIKE patterns can never match and every person
        imports with 0 faces."""
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
            stdout = "abc123/L0/001\tAlice|\n"
            with _bulk_applescript_returns(stdout):
                imported, _ = import_photos_persons(db)

            assert imported == 1
            alice = next(p for p in db.get_persons() if p.label == "Alice")
            assert len(alice.face_ids) == 1  # uuid matched despite the suffix

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
                    "pyimgtag.face.photos_importer.is_applescript_available",
                    new=lambda: True,
                ),
                patch(
                    "pyimgtag.face.photos_importer.subprocess.run",
                    return_value=failed,
                ),
                patch("pyimgtag.face.photos_importer._has_photoscript", new=lambda: True),
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
                    "pyimgtag.face.photos_importer.is_applescript_available",
                    new=lambda: True,
                ),
                patch("pyimgtag.face.photos_importer.subprocess.run", side_effect=boom),
                patch("pyimgtag.face.photos_importer._has_photoscript", new=lambda: True),
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
        from pyimgtag.face.photos_importer import _PROGRESS_EVERY

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

    def test_heartbeat_not_reemitted_for_duplicate_uuid_rows(self):
        """Regression: while ``processed`` sits at a multiple of the cadence,
        duplicate-uuid rows (one row per photo×person from the app-level
        walker) must not re-emit the identical heartbeat line."""
        import time

        from pyimgtag.face.photos_importer import _PROGRESS_EVERY, _parse_bulk_output

        lines = [f"uuid_{i:04d}\tAlice|\n" for i in range(_PROGRESS_EVERY)]
        # processed now sits exactly at the cadence; these rows repeat a uuid.
        lines += [f"uuid_0000\tPerson_{i}|\n" for i in range(5)]
        messages: list[str] = []

        _parse_bulk_output("".join(lines), time.monotonic(), messages.append)

        periodic = [m for m in messages if m.startswith("\r[faces] processed")]
        # Exactly one cadence line (at _PROGRESS_EVERY) plus the final summary.
        assert len(periodic) == 2, f"heartbeat re-emitted: {periodic!r}"

    def test_default_progress_overwrites_cr_lines(self, capsys):
        """``\\r``-prefixed heartbeats must not get a trailing newline, so the
        next counter overwrites them instead of spamming scrollback."""
        from pyimgtag.face.photos_importer import _default_progress

        _default_progress("\r[faces] processed 200")
        _default_progress("\r[faces] processed 400")
        _default_progress("")  # callers' final newline
        _default_progress("done")

        err = capsys.readouterr().err
        assert err == "\r[faces] processed 200\r[faces] processed 400\ndone\n"


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
            "pyimgtag.face.photos_importer.import_photos_persons",
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

        # Use import_module (not ``import pyimgtag``) so this file never mixes
        # ``import pyimgtag`` with the ``from pyimgtag import ...`` forms used
        # elsewhere; we still need the package object to rebind the submodule
        # attribute in ``finally``.
        pyimgtag = importlib.import_module("pyimgtag")

        mod_name = "pyimgtag.face.photos_importer"
        saved = sys.modules.pop(mod_name, None)
        ps_saved = sys.modules.pop("photoscript", None)
        try:
            importlib.import_module(mod_name)
            assert "photoscript" not in sys.modules, (
                "photoscript was imported at module level in photos_importer"
            )
        finally:
            # Restore BOTH sys.modules AND the parent-package attribute. The
            # re-import above rebinds ``pyimgtag.face.photos_importer`` (the
            # attribute) to a throwaway module object; if we only restore
            # sys.modules, later ``from pyimgtag.face import photos_importer``
            # resolves the throwaway while ``patch("pyimgtag.face.photos_importer.X")``
            # targets the sys.modules one — the two diverge and every
            # module-level patch in subsequent tests silently misses.
            if saved is not None:
                sys.modules[mod_name] = saved
                pyimgtag.face.photos_importer = saved
            if ps_saved is not None:
                sys.modules["photoscript"] = ps_saved


class TestHasPhotoscript:
    """Cover the real _has_photoscript body (find_spec, lru_cache)."""

    def test_returns_true_when_spec_found(self):
        from pyimgtag.face import photos_importer

        photos_importer._has_photoscript.cache_clear()
        try:
            with patch("importlib.util.find_spec", return_value=MagicMock()):
                assert photos_importer._has_photoscript() is True
        finally:
            photos_importer._has_photoscript.cache_clear()

    def test_returns_false_when_spec_missing(self):
        from pyimgtag.face import photos_importer

        photos_importer._has_photoscript.cache_clear()
        try:
            with patch("importlib.util.find_spec", return_value=None):
                assert photos_importer._has_photoscript() is False
        finally:
            photos_importer._has_photoscript.cache_clear()


class TestBulkAppScriptAlias:
    def test_bulk_applescript_alias_returns_every_person_form(self):
        from pyimgtag.face.photos_importer import (
            _bulk_applescript,
            _bulk_applescript_every_person,
        )

        assert _bulk_applescript() == _bulk_applescript_every_person()


class TestRunBulkOsascript:
    """Cover _run_bulk_osascript error branches (timeout, OSError)."""

    def test_timeout_raises_unavailable(self):
        from pyimgtag.face.photos_importer import (
            _BulkAppleScriptUnavailable,
            _run_bulk_osascript,
        )

        with patch(
            "pyimgtag.face.photos_importer.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=1800),
        ):
            with pytest.raises(_BulkAppleScriptUnavailable, match="timed out"):
                _run_bulk_osascript('tell application "Photos"\nreturn ""\nend tell')

    def test_oserror_raises_unavailable(self):
        from pyimgtag.face.photos_importer import (
            _BulkAppleScriptUnavailable,
            _run_bulk_osascript,
        )

        with patch(
            "pyimgtag.face.photos_importer.subprocess.run",
            side_effect=OSError("no such binary"),
        ):
            with pytest.raises(_BulkAppleScriptUnavailable, match="failed to launch osascript"):
                _run_bulk_osascript("script")


class TestAppLevelWalkFailure:
    """Cover the branch where the app-level 'people' walk itself fails (line 386)."""

    def _make_db(self, tmp_path):
        from pyimgtag.progress_db import ProgressDB

        return ProgressDB(db_path=tmp_path / "test.db")

    def test_app_people_walk_nonzero_logs_and_returns_empty(self, tmp_path):
        """Per-photo path returns 0 persons, then the app-level walk also fails.

        The first osascript call succeeds with no person rows, triggering the
        app-level retry. That retry returns a non-zero exit, so the warning
        branch (line 386-389) runs and the result stays empty.
        """
        from pyimgtag.face import photos_importer

        with self._make_db(tmp_path) as db:
            # Mock at the _run_bulk_osascript seam rather than subprocess.run so
            # the real /usr/bin/osascript binary is never consulted. On Linux/
            # Windows runners that binary is absent, and a subprocess.run patch
            # that misses lets the real call raise OSError -> the bulk path falls
            # through to the photoscript branch -> RuntimeError. Patching the
            # helper keeps the test platform-independent.
            def _fake_osascript(script):
                proc = MagicMock()
                if "set _people_list" in script:
                    # app-level walk fails
                    proc.returncode = 1
                    proc.stdout = ""
                    proc.stderr = "app-level boom"
                else:
                    # per-photo path: succeeds but emits zero persons
                    proc.returncode = 0
                    proc.stdout = "uuid1\t\n"
                    proc.stderr = ""
                return proc

            with (
                patch(
                    "pyimgtag.face.photos_importer.is_applescript_available",
                    new=lambda: True,
                ),
                patch(
                    "pyimgtag.face.photos_importer._run_bulk_osascript",
                    side_effect=_fake_osascript,
                ),
            ):
                imported, skipped = photos_importer.import_photos_persons(db)

            assert imported == 0
            assert skipped == 0
            assert db.get_persons() == []


class TestPhotoscriptFallbackEdgeCases:
    """Cover the slow photoscript fallback's remaining branches."""

    def _make_db(self, tmp_path):
        from pyimgtag.progress_db import ProgressDB

        return ProgressDB(db_path=tmp_path / "test.db")

    def test_unreadable_uuid_is_skipped(self, tmp_path):
        """A photo whose .uuid getter raises must be skipped (lines 469-470)."""
        with self._make_db(tmp_path) as db:
            bad = MagicMock()
            type(bad).persons = property(lambda self: ["Alice"])
            type(bad).uuid = property(
                lambda self: (_ for _ in ()).throw(RuntimeError("uuid unreadable"))
            )

            library = MagicMock()
            library.photos.return_value = [bad]

            with _photoscript_only(library):
                imported, skipped = import_photos_persons(db)

            # uuid could not be read, so the name never maps to a uuid → no person.
            assert imported == 0
            assert db.get_persons() == []

    def test_photoscript_periodic_progress_line(self, tmp_path):
        """The photoscript fallback emits a periodic counter every 200 photos (479-480)."""
        from pyimgtag.face.photos_importer import _PROGRESS_EVERY

        with self._make_db(tmp_path) as db:
            photos = [_mock_photo(f"uuid_{i:04d}", [f"P{i}"]) for i in range(_PROGRESS_EVERY + 10)]
            library = MagicMock()
            library.photos.return_value = photos
            messages: list[str] = []

            with _photoscript_only(library):
                import_photos_persons(db, progress=messages.append)

            assert any(m.startswith("\r[faces] processed") for m in messages), (
                f"missing periodic photoscript line; got first few: {messages[:3]!r}"
            )


class TestListPhotosFailure:
    """Cover _list_photos exception handling (lines 557-559)."""

    def test_enumeration_failure_returns_empty_list(self):
        from pyimgtag.face.photos_importer import _list_photos

        library = MagicMock()
        library.photos.side_effect = RuntimeError("AppleScript bridge died")
        assert _list_photos(library) == []


class TestPhotoPersonNames:
    """Cover _photo_person_names defensive branches."""

    def test_single_name_string_wrapped_in_list(self):
        """A bridge that returns a bare string must be wrapped (line 580)."""
        from pyimgtag.face.photos_importer import _photo_person_names

        photo = MagicMock()
        type(photo).persons = property(lambda self: "Alice")
        assert _photo_person_names(photo) == ["Alice"]

    def test_persons_getter_raises_returns_empty(self):
        from pyimgtag.face.photos_importer import _photo_person_names

        photo = MagicMock()
        type(photo).persons = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("no metadata"))
        )
        assert _photo_person_names(photo) == []

    def test_empty_persons_returns_empty(self):
        from pyimgtag.face.photos_importer import _photo_person_names

        photo = MagicMock()
        type(photo).persons = property(lambda self: [])
        assert _photo_person_names(photo) == []

    def test_list_of_names_coerced_to_str(self):
        from pyimgtag.face.photos_importer import _photo_person_names

        photo = MagicMock()
        type(photo).persons = property(lambda self: ["Alice", "Bob"])
        assert _photo_person_names(photo) == ["Alice", "Bob"]


class TestCollectViaOsxphotos:
    """The preferred enumeration path: read names + UUIDs from the Photos DB."""

    def test_builds_name_to_uuids_skipping_unknown_and_empty(self):
        from pyimgtag.face.photos_importer import _collect_via_osxphotos

        fake = _fake_osxphotos(
            [("Kate Резниченко", ["U1", "U2"]), ("_UNKNOWN_", ["U3"]), ("", ["U4"])]
        )
        with patch.dict("sys.modules", {"osxphotos": fake}):
            result = _collect_via_osxphotos(lambda _m: None)
        assert result == {"Kate Резниченко": ["U1", "U2"]}

    def test_missing_osxphotos_raises_unavailable(self):
        # The autouse fixture already maps osxphotos -> None (import fails).
        from pyimgtag.face.photos_importer import _collect_via_osxphotos

        with pytest.raises(_OsxphotosUnavailable):
            _collect_via_osxphotos(lambda _m: None)

    def test_open_failure_raises_unavailable(self):
        import types

        from pyimgtag.face.photos_importer import _collect_via_osxphotos

        module = types.ModuleType("osxphotos")

        def _boom(*_a, **_k):
            raise RuntimeError("library locked")

        module.PhotosDB = _boom
        with patch.dict("sys.modules", {"osxphotos": module}):
            with pytest.raises(_OsxphotosUnavailable):
                _collect_via_osxphotos(lambda _m: None)

    def test_import_prefers_osxphotos_and_links_face(self, tmp_path):
        """End-to-end: osxphotos is used over AppleScript and its UUID links a
        scanned face onto the named person."""
        from pyimgtag.progress_db import ProgressDB

        uuid = "AAAAAAAA-1111-2222-3333-444444444444"
        with ProgressDB(db_path=tmp_path / "t.db") as db:
            db.insert_face(f"/lib/{uuid}.jpg", FaceDetection(image_path=f"/lib/{uuid}.jpg"))
            fake = _fake_osxphotos([("Alice", [uuid])])
            with patch.dict("sys.modules", {"osxphotos": fake}):
                imported, _skipped = import_photos_persons(db)
            assert imported == 1
            alice = next(p for p in db.get_persons() if p.label == "Alice")
            assert alice.source == "photos"
            assert len(alice.face_ids) == 1
