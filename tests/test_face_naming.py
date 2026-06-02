"""Tests for the reference-face naming engine.

The matching/applying logic is exercised with synthetic 128-d embeddings, so
these run without the optional ``[face]`` dependency. ``load_reference_embeddings``
(which needs the detector) is covered only at the path-iteration level.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pyimgtag.face_naming import (
    NameMatch,
    _iter_reference_images,
    apply_matches,
    match_clusters_to_references,
)
from pyimgtag.models import FaceDetection
from pyimgtag.progress_db import ProgressDB


def _db(tmp_path: Path) -> ProgressDB:
    return ProgressDB(db_path=tmp_path / "t.db")


def _vec(*nonzero: tuple[int, float]) -> np.ndarray:
    v = np.zeros(128, dtype=np.float64)
    for idx, val in nonzero:
        v[idx] = val
    return v


def _cluster(db: ProgressDB, label: str, embs: list[np.ndarray], *, trusted=False, confirmed=False):
    pid = db.create_person(label=label, trusted=trusted, confirmed=confirmed)
    for i, emb in enumerate(embs):
        fid = db.insert_face(
            f"/c{pid}_{i}.jpg", FaceDetection(image_path=f"/c{pid}_{i}.jpg"), embedding=emb
        )
        db.set_person_id(fid, pid)
    return pid


# --------------------------------------------------------------------------- #
# reference folder iteration
# --------------------------------------------------------------------------- #
class TestIterReferenceImages:
    def test_flat_files_and_subfolders(self, tmp_path: Path):
        (tmp_path / "Alice.jpg").write_bytes(b"x")
        (tmp_path / "Bob Smith.png").write_bytes(b"x")
        sub = tmp_path / "Олег"  # non-ascii (Cyrillic) name as a sub-folder
        sub.mkdir()
        (sub / "01.heic").write_bytes(b"x")
        (sub / "02.jpeg").write_bytes(b"x")
        (tmp_path / "notes.txt").write_bytes(b"x")  # ignored (not an image)

        pairs = {(p.name, name) for p, name in _iter_reference_images(tmp_path)}
        assert ("Alice.jpg", "Alice") in pairs
        assert ("Bob Smith.png", "Bob Smith") in pairs
        assert ("01.heic", sub.name) in pairs
        assert ("02.jpeg", sub.name) in pairs
        assert all(n != "notes" for _, n in pairs)  # txt skipped


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #
class TestMatchClusters:
    def test_matches_nearest_reference(self, tmp_path: Path):
        with _db(tmp_path) as db:
            auto = _cluster(db, "Person 1", [_vec((0, 1.0)), _vec((0, 0.99), (1, 0.02))])
            references = {"Alice": [_vec((0, 1.0))], "Bob": [_vec((50, 1.0))]}

            matches = match_clusters_to_references(db, references)

            assert len(matches) == 1
            assert matches[0].person_id == auto
            assert matches[0].name == "Alice"
            assert matches[0].face_count == 2

    def test_far_cluster_not_matched(self, tmp_path: Path):
        with _db(tmp_path) as db:
            _cluster(db, "Person 1", [_vec((0, 1.0))])
            references = {"Alice": [_vec((90, 1.0))]}  # nowhere near
            assert match_clusters_to_references(db, references) == []

    def test_ambiguous_match_skipped(self, tmp_path: Path):
        with _db(tmp_path) as db:
            _cluster(db, "Person 1", [_vec((0, 1.0))])
            # Two references almost equidistant → margin not met → skip.
            references = {"Alice": [_vec((0, 1.0), (1, 0.10))], "Bob": [_vec((0, 1.0), (2, 0.11))]}
            assert match_clusters_to_references(db, references) == []

    def test_trusted_clusters_ignored(self, tmp_path: Path):
        with _db(tmp_path) as db:
            # Already-named person sitting right on a reference must be left alone.
            _cluster(db, "Alice", [_vec((0, 1.0))], trusted=True, confirmed=True)
            references = {"Alice": [_vec((0, 1.0))]}
            assert match_clusters_to_references(db, references) == []


# --------------------------------------------------------------------------- #
# applying
# --------------------------------------------------------------------------- #
class TestApplyMatches:
    def test_rename_when_no_existing_person(self, tmp_path: Path):
        with _db(tmp_path) as db:
            auto = _cluster(db, "Person 1", [_vec((0, 1.0))])
            result = apply_matches(
                db,
                [
                    NameMatch(
                        person_id=auto,
                        current_label="Person 1",
                        name="Carol",
                        distance=0.1,
                        face_count=1,
                    )
                ],
            )
            assert result == {"renamed": 1, "merged": 0}
            person = next(p for p in db.get_persons() if p.person_id == auto)
            assert person.label == "Carol"
            assert person.trusted is True  # update_person_label marks it trusted

    def test_merge_into_existing_trusted_person(self, tmp_path: Path):
        with _db(tmp_path) as db:
            existing = db.create_person(label="Alice", trusted=True, confirmed=True)  # 0 faces
            auto = _cluster(db, "Person 1", [_vec((0, 1.0)), _vec((0, 0.99))])

            result = apply_matches(
                db,
                [
                    NameMatch(
                        person_id=auto,
                        current_label="Person 1",
                        name="Alice",
                        distance=0.1,
                        face_count=2,
                    )
                ],
            )

            assert result == {"renamed": 0, "merged": 1}
            persons = {p.person_id: p for p in db.get_persons()}
            assert auto not in persons  # cluster folded in and removed
            assert len(persons[existing].face_ids) == 2  # Alice now owns the faces

    def test_end_to_end_match_then_apply(self, tmp_path: Path):
        with _db(tmp_path) as db:
            existing = db.create_person(label="Alice", trusted=True, confirmed=True)
            _cluster(db, "Person 1", [_vec((0, 1.0)), _vec((0, 0.98), (1, 0.03))])
            references = {"Alice": [_vec((0, 1.0))]}

            matches = match_clusters_to_references(db, references)
            apply_matches(db, matches)

            alice = next(p for p in db.get_persons() if p.person_id == existing)
            assert len(alice.face_ids) == 2
