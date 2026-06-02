"""Safety net for every delete / prune operation.

Each destructive surface gets a *positive* case (it removes exactly what it
should) and a *negative* case (it leaves everything else intact, and refuses to
over-delete when its presence signal is missing). The drift cases pin the
regression where `cleanup-drift --prune` deleted almost the whole DB: a Photos
library probe whose ids carry the `/L0/001` localIdentifier suffix — or that
comes back empty — made every on-disk row look "missing from Photos".

No test shells out to osascript; the Photos probe is supplied through the
`fetch_membership` seam, so the suite runs identically on Linux CI.
"""

from __future__ import annotations

from pathlib import Path

from pyimgtag.applescript_writer import _parse_membership_output
from pyimgtag.cleanup_drift import (
    CAT_DISK_MISSING,
    CAT_PHOTOS_MISSING,
    CAT_PRESENT,
    _classify,
    prune_drift,
    scan_drift,
)
from pyimgtag.models import FaceDetection, ImageResult
from pyimgtag.progress_db import ProgressDB

_UUID = "42A9A72A-A3F1-43AA-B4C3-7A2A3121697E"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _db(tmp_path: Path) -> ProgressDB:
    return ProgressDB(db_path=tmp_path / "t.db")


def _mark(db: ProgressDB, path: Path) -> None:
    db.mark_done(
        path,
        ImageResult(file_path=str(path), file_name=path.name, processing_status="ok"),
    )


def _face(db: ProgressDB, path: str, *, person_id: int | None = None, ignored: bool = False) -> int:
    fid = db.insert_face(path, FaceDetection(image_path=path))
    if person_id is not None:
        db.set_person_id(fid, person_id)
    if ignored:
        db.ignore_face(fid)  # also nulls person_id
    return fid


def _person_id_of(db: ProgressDB, face_id: int) -> int | None:
    row = db._conn.execute("SELECT person_id FROM faces WHERE id = ?", (face_id,)).fetchone()
    return row[0]


def _face_exists(db: ProgressDB, face_id: int) -> bool:
    return db.get_face_by_id(face_id) is not None


# --------------------------------------------------------------------------- #
# Photos membership parsing — the prune-everything regression
# --------------------------------------------------------------------------- #
class TestMembershipParsing:
    def test_localidentifier_id_also_indexes_bare_uuid(self):
        """`<UUID>/L0/001` must contribute the bare `<UUID>` so a library
        original (on disk as `<UUID>.<ext>`) is recognised as present."""
        membership = _parse_membership_output(f"{_UUID}/L0/001\tIMG_6874.HEIC\n")
        assert f"{_UUID}/L0/001" in membership  # full localIdentifier
        assert _UUID in membership  # bare UUID prefix — the fix
        assert "IMG_6874.HEIC" in membership  # original import filename

    def test_blank_and_tabless_lines_are_skipped(self):
        membership = _parse_membership_output("\nno-tab-line\n\tonly_filename.jpg\nbare_id\t\n")
        assert "no-tab-line" not in membership
        assert "only_filename.jpg" in membership
        assert "bare_id" in membership

    def test_empty_output_is_empty_set(self):
        assert _parse_membership_output("") == set()


# --------------------------------------------------------------------------- #
# Drift classification — library originals must survive
# --------------------------------------------------------------------------- #
class TestLibraryOriginalsSurvive:
    def test_library_original_classified_present(self, tmp_path: Path):
        originals = tmp_path / "originals" / "4"
        originals.mkdir(parents=True)
        f = originals / f"{_UUID}.jpeg"
        f.write_bytes(b"x")
        membership = _parse_membership_output(f"{_UUID}/L0/001\tIMG_6874.HEIC\n")
        # POSITIVE: recognised as present despite filename != <UUID>.jpeg
        assert _classify(str(f), membership) == CAT_PRESENT

    def test_scan_keeps_all_library_originals(self, tmp_path: Path):
        """Regression: a DB full of library originals must lose 0 rows when the
        probe returns the real `/L0/001` id form."""
        db_path = tmp_path / "t.db"
        uuids = [f"{i:08d}-AAAA-BBBB-CCCC-DDDDDDDDDDDD" for i in range(5)]
        with ProgressDB(db_path=db_path) as db:
            for u in uuids:
                p = tmp_path / f"{u}.jpeg"
                p.write_bytes(b"x")
                _mark(db, p)
        membership = _parse_membership_output(
            "".join(f"{u}/L0/001\tIMG_{i}.HEIC\n" for i, u in enumerate(uuids))
        )
        with ProgressDB(db_path=db_path) as db:
            report = scan_drift(db, fetch_membership=lambda: (membership, None))
        assert report.total == 5
        assert report.present == 5
        assert report.dead_count == 0


# --------------------------------------------------------------------------- #
# Drift safety guard — an empty/failed probe must never prune everything
# --------------------------------------------------------------------------- #
class TestDriftProbeSafety:
    def test_empty_membership_no_error_degrades_to_disk_only(self, tmp_path: Path):
        """A "successful" probe returning zero items is a silent failure, not
        an empty library — every on-disk row must stay present."""
        db_path = tmp_path / "t.db"
        with ProgressDB(db_path=db_path) as db:
            for i in range(3):
                p = tmp_path / f"img{i}.jpg"
                p.write_bytes(b"x")
                _mark(db, p)
        with ProgressDB(db_path=db_path) as db:
            report = scan_drift(db, fetch_membership=lambda: (set(), None))
        assert report.photos_probe_error == "empty_membership"
        assert report.present == 3
        assert report.photos_missing == 0
        assert report.dead_count == 0

    def test_empty_membership_prune_deletes_nothing(self, tmp_path: Path):
        db_path = tmp_path / "t.db"
        p = tmp_path / "keep.jpg"
        p.write_bytes(b"x")
        with ProgressDB(db_path=db_path) as db:
            _mark(db, p)
        with ProgressDB(db_path=db_path) as db:
            report = scan_drift(db, fetch_membership=lambda: (set(), None))
            deleted = prune_drift(db, report.dead_paths)
            assert deleted == 0
            assert list(db.iter_image_paths()) == [str(p)]

    def test_probe_error_only_disk_missing_pruned(self, tmp_path: Path):
        db_path = tmp_path / "t.db"
        on_disk = tmp_path / "here.jpg"
        on_disk.write_bytes(b"x")
        gone = tmp_path / "gone.jpg"
        gone.write_bytes(b"x")
        with ProgressDB(db_path=db_path) as db:
            _mark(db, on_disk)
            _mark(db, gone)
        gone.unlink()
        with ProgressDB(db_path=db_path) as db:
            report = scan_drift(db, fetch_membership=lambda: (set(), "parse_error"))
        # NEGATIVE: the on-disk row is not pruned despite no Photos signal.
        assert report.dead_paths == [str(gone)]
        assert report.present == 1


# --------------------------------------------------------------------------- #
# Drift prune — positive / negative
# --------------------------------------------------------------------------- #
class TestDriftPrune:
    def test_classify_categories(self, tmp_path: Path):
        present = tmp_path / "p.jpg"
        present.write_bytes(b"x")
        gone = tmp_path / "g.jpg"  # never created on disk
        assert _classify(str(present), {"p.jpg"}) == CAT_PRESENT
        assert _classify(str(gone), {"p.jpg"}) == CAT_DISK_MISSING
        assert _classify(str(present), set()) == CAT_PHOTOS_MISSING

    def test_prune_removes_only_dead_rows(self, tmp_path: Path):
        db_path = tmp_path / "t.db"
        present = tmp_path / "present.jpg"
        present.write_bytes(b"x")
        gone = tmp_path / "gone.jpg"
        gone.write_bytes(b"x")
        with ProgressDB(db_path=db_path) as db:
            _mark(db, present)
            _mark(db, gone)
        gone.unlink()
        with ProgressDB(db_path=db_path) as db:
            report = scan_drift(db, fetch_membership=lambda: ({"present.jpg"}, None))
            deleted = prune_drift(db, report.dead_paths)
            assert deleted == 1
            assert list(db.iter_image_paths()) == [str(present)]


# --------------------------------------------------------------------------- #
# ProgressDB.delete_image_rows
# --------------------------------------------------------------------------- #
class TestDeleteImageRows:
    def test_deletes_only_listed_paths(self, tmp_path: Path):
        db_path = tmp_path / "t.db"
        paths = [tmp_path / f"i{i}.jpg" for i in range(3)]
        with ProgressDB(db_path=db_path) as db:
            for p in paths:
                p.write_bytes(b"x")
                _mark(db, p)
            deleted = db.delete_image_rows([str(paths[0])])
            assert deleted == 1
            remaining = sorted(db.iter_image_paths())
            assert remaining == sorted(str(p) for p in paths[1:])

    def test_empty_list_is_noop(self, tmp_path: Path):
        db_path = tmp_path / "t.db"
        p = tmp_path / "i.jpg"
        with ProgressDB(db_path=db_path) as db:
            p.write_bytes(b"x")
            _mark(db, p)
            assert db.delete_image_rows([]) == 0
            assert list(db.iter_image_paths()) == [str(p)]


# --------------------------------------------------------------------------- #
# Person deletion keeps face crops (unassigns them)
# --------------------------------------------------------------------------- #
class TestDeletePerson:
    def test_delete_person_keeps_faces_unassigned(self, tmp_path: Path):
        with _db(tmp_path) as db:
            pid = db.create_person(label="Alice", trusted=True, confirmed=True, source="photos")
            other = db.create_person(label="Bob", trusted=True, confirmed=True)
            f1 = _face(db, "/p/a.jpg", person_id=pid)
            f_other = _face(db, "/p/b.jpg", person_id=other)

            db.delete_person(pid)

            # POSITIVE: person gone.
            assert all(p.person_id != pid for p in db.get_persons())
            # NEGATIVE: its face survives, just unassigned; other person intact.
            assert _face_exists(db, f1)
            assert _person_id_of(db, f1) is None
            assert _person_id_of(db, f_other) == other

    def test_delete_persons_batch(self, tmp_path: Path):
        with _db(tmp_path) as db:
            a = db.create_person(label="A", trusted=True, confirmed=True)
            b = db.create_person(label="B", trusted=True, confirmed=True)
            keep = db.create_person(label="Keep", trusted=True, confirmed=True)
            fa = _face(db, "/p/a.jpg", person_id=a)
            fk = _face(db, "/p/k.jpg", person_id=keep)

            deleted = db.delete_persons([a, b])

            assert deleted == 2
            ids = {p.person_id for p in db.get_persons()}
            assert ids == {keep}
            assert _face_exists(db, fa) and _person_id_of(db, fa) is None
            assert _person_id_of(db, fk) == keep

    def test_delete_persons_empty_noop(self, tmp_path: Path):
        with _db(tmp_path) as db:
            keep = db.create_person(label="Keep", trusted=True, confirmed=True)
            assert db.delete_persons([]) == 0
            assert [p.person_id for p in db.get_persons()] == [keep]


# --------------------------------------------------------------------------- #
# reset_untrusted_faces — keep trusted/confirmed + ignored, drop the rest
# --------------------------------------------------------------------------- #
class TestResetUntrustedFaces:
    def test_keeps_trusted_confirmed_ignored_drops_rest(self, tmp_path: Path):
        with _db(tmp_path) as db:
            trusted = db.create_person(label="T", trusted=True, confirmed=True, source="photos")
            confirmed = db.create_person(label="C", trusted=False, confirmed=True)
            auto = db.create_person(label="", trusted=False, confirmed=False)

            f_trusted = _face(db, "/p/t.jpg", person_id=trusted)
            f_confirmed = _face(db, "/p/c.jpg", person_id=confirmed)
            f_auto = _face(db, "/p/a.jpg", person_id=auto)
            f_unassigned = _face(db, "/p/u.jpg")
            f_ignored = _face(db, "/p/i.jpg", ignored=True)

            counts = db.reset_untrusted_faces(dry_run=False)

            # POSITIVE: untrusted + unassigned faces and the auto person gone.
            assert not _face_exists(db, f_auto)
            assert not _face_exists(db, f_unassigned)
            assert all(p.person_id != auto for p in db.get_persons())
            assert counts["faces"] == 2 and counts["persons"] == 1
            # NEGATIVE: trusted, confirmed, and ignored faces survive.
            assert _face_exists(db, f_trusted)
            assert _face_exists(db, f_confirmed)
            assert _face_exists(db, f_ignored)
            kept = {p.person_id for p in db.get_persons()}
            assert kept == {trusted, confirmed}

    def test_dry_run_deletes_nothing(self, tmp_path: Path):
        with _db(tmp_path) as db:
            auto = db.create_person()
            f_auto = _face(db, "/p/a.jpg", person_id=auto)
            counts = db.reset_untrusted_faces(dry_run=True)
            assert counts["faces"] == 1
            assert _face_exists(db, f_auto)  # nothing actually removed

    def test_untrusted_person_with_ignored_face_not_orphaned(self, tmp_path: Path):
        """An untrusted person whose only face is ignored must not be deleted —
        doing so would leave the surviving trash face pointing at a dead row."""
        with _db(tmp_path) as db:
            auto = db.create_person(label="", trusted=False, confirmed=False)
            # Directly model the anomaly: an ignored face still owned by the
            # untrusted person (the trash face survives the faces delete).
            fid = db.insert_face("/p/a.jpg", FaceDetection(image_path="/p/a.jpg"))
            db._conn.execute(
                "UPDATE faces SET ignored = 1, person_id = ? WHERE id = ?", (auto, fid)
            )
            db._conn.commit()

            counts = db.reset_untrusted_faces(dry_run=False)

            # The person is kept (it still owns a face); no orphan is created.
            assert counts["persons"] == 0
            assert any(p.person_id == auto for p in db.get_persons())
            assert _person_id_of(db, fid) == auto  # not dangling


# --------------------------------------------------------------------------- #
# reset_all_faces — wipe faces/persons, leave image tagging untouched
# --------------------------------------------------------------------------- #
class TestResetAllFaces:
    def test_wipes_faces_and_persons_keeps_processed_images(self, tmp_path: Path):
        db_path = tmp_path / "t.db"
        img = tmp_path / "tagged.jpg"
        img.write_bytes(b"x")
        with ProgressDB(db_path=db_path) as db:
            _mark(db, img)  # image-tagging progress row
            pid = db.create_person(label="T", trusted=True, confirmed=True)
            _face(db, "/p/t.jpg", person_id=pid)
            _face(db, "/p/i.jpg", ignored=True)

            db.reset_all_faces(dry_run=False)

            # POSITIVE: even trusted persons + ignored faces are gone.
            assert db.get_persons() == []
            assert db._conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0
            # NEGATIVE: processed_images (tagging/geocoding progress) untouched.
            assert list(db.iter_image_paths()) == [str(img)]

    def test_dry_run_keeps_everything(self, tmp_path: Path):
        with _db(tmp_path) as db:
            pid = db.create_person(label="T", trusted=True)
            fid = _face(db, "/p/t.jpg", person_id=pid)
            counts = db.reset_all_faces(dry_run=True)
            assert counts["faces"] == 1 and counts["persons"] == 1
            assert _face_exists(db, fid)


# --------------------------------------------------------------------------- #
# clear_auto_persons — drop auto clusters only
# --------------------------------------------------------------------------- #
class TestClearAutoPersons:
    def test_clears_auto_keeps_trusted_and_confirmed(self, tmp_path: Path):
        with _db(tmp_path) as db:
            trusted = db.create_person(label="T", trusted=True, confirmed=True)
            confirmed = db.create_person(label="C", trusted=False, confirmed=True)
            auto = db.create_person(label="", trusted=False, confirmed=False)
            f_trusted = _face(db, "/p/t.jpg", person_id=trusted)
            f_auto = _face(db, "/p/a.jpg", person_id=auto)

            db.clear_auto_persons()

            # POSITIVE: auto person removed, its face released to unassigned.
            assert all(p.person_id != auto for p in db.get_persons())
            assert _face_exists(db, f_auto) and _person_id_of(db, f_auto) is None
            # NEGATIVE: trusted/confirmed persons and their faces untouched.
            assert {p.person_id for p in db.get_persons()} == {trusted, confirmed}
            assert _person_id_of(db, f_trusted) == trusted


# --------------------------------------------------------------------------- #
# ignore_face — moves to trash, never deletes the crop
# --------------------------------------------------------------------------- #
class TestIgnoreFace:
    def test_ignore_unassigns_but_keeps_row(self, tmp_path: Path):
        with _db(tmp_path) as db:
            pid = db.create_person(label="T", trusted=True)
            fid = _face(db, "/p/t.jpg", person_id=pid)

            db.ignore_face(fid)

            # NEGATIVE: the crop is not deleted, just unassigned + trashed.
            assert _face_exists(db, fid)
            assert _person_id_of(db, fid) is None
            assert any(f["id"] == fid for f in db.get_ignored_faces())

    def test_restore_round_trip(self, tmp_path: Path):
        with _db(tmp_path) as db:
            fid = _face(db, "/p/t.jpg", ignored=True)
            assert any(f["id"] == fid for f in db.get_ignored_faces())
            db.restore_face(fid)
            assert all(f["id"] != fid for f in db.get_ignored_faces())
            assert _face_exists(db, fid)


# --------------------------------------------------------------------------- #
# merge_persons — must never orphan faces (self-merge guard)
# --------------------------------------------------------------------------- #
class TestMergePersons:
    def test_self_merge_is_noop(self, tmp_path: Path):
        """merge(X, X) used to delete X and orphan all its faces."""
        with _db(tmp_path) as db:
            pid = db.create_person(label="Alice", trusted=True, confirmed=True)
            f1 = _face(db, "/p/a.jpg", person_id=pid)
            f2 = _face(db, "/p/b.jpg", person_id=pid)

            db.merge_persons(source_id=pid, target_id=pid)

            # Person survives, faces still belong to it — nothing orphaned.
            assert any(p.person_id == pid for p in db.get_persons())
            assert _person_id_of(db, f1) == pid
            assert _person_id_of(db, f2) == pid

    def test_merge_moves_all_faces_then_deletes_source(self, tmp_path: Path):
        with _db(tmp_path) as db:
            src = db.create_person(label="Dup", trusted=True, confirmed=True)
            dst = db.create_person(label="Alice", trusted=True, confirmed=True)
            f1 = _face(db, "/p/a.jpg", person_id=src)
            f2 = _face(db, "/p/b.jpg", person_id=src)

            db.merge_persons(source_id=src, target_id=dst)

            # POSITIVE: source gone, all its faces reassigned (none orphaned).
            assert all(p.person_id != src for p in db.get_persons())
            assert _person_id_of(db, f1) == dst
            assert _person_id_of(db, f2) == dst


# --------------------------------------------------------------------------- #
# Drift report exposes per-category paths so callers can prune conservatively
# --------------------------------------------------------------------------- #
class TestDriftCategoryPaths:
    def test_scan_separates_disk_and_photos_missing(self, tmp_path: Path):
        db_path = tmp_path / "t.db"
        present = tmp_path / "present.jpg"
        present.write_bytes(b"x")
        photos_only = tmp_path / "photos_only.jpg"  # on disk, absent from Photos
        photos_only.write_bytes(b"x")
        gone = tmp_path / "gone.jpg"
        gone.write_bytes(b"x")
        with ProgressDB(db_path=db_path) as db:
            for p in (present, photos_only, gone):
                _mark(db, p)
        gone.unlink()
        with ProgressDB(db_path=db_path) as db:
            report = scan_drift(db, fetch_membership=lambda: ({"present.jpg"}, None))
        assert report.disk_missing_paths == [str(gone)]
        assert report.photos_missing_paths == [str(photos_only)]
        # dead_paths remains the union (the web panel sample relies on it).
        assert sorted(report.dead_paths) == sorted([str(gone), str(photos_only)])
