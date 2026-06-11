"""Tests for face clustering pipeline.

The real clustering logic depends on scikit-learn's DBSCAN. scikit-learn is
an optional ([face]) extra and is *not* installed in CI, so the behavioural
tests below mock ``sklearn.cluster.DBSCAN`` at the import boundary. This lets
the real ``cluster_faces`` body run (and count toward coverage) without the
optional dependency being present.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import numpy as np
import pytest

from pyimgtag.face.clustering import cluster_faces, recluster_auto
from pyimgtag.models import FaceDetection
from pyimgtag.progress_db import ProgressDB


def _seed_faces(db: ProgressDB, embeddings: list[np.ndarray], prefix: str = "/img") -> list[int]:
    """Insert faces with given embeddings and return their ids."""
    face_ids = []
    for i, emb in enumerate(embeddings):
        det = FaceDetection(image_path=f"{prefix}/{i}.jpg")
        fid = db.insert_face(f"{prefix}/{i}.jpg", det, embedding=emb)
        face_ids.append(fid)
    return face_ids


def _fake_dbscan(labels: list[int]):
    """Return a fake ``sklearn.cluster`` module whose DBSCAN yields *labels*.

    ``DBSCAN(...).fit_predict(X)`` returns the canned ``labels`` array so the
    test controls exactly which cluster each face lands in, decoupling the
    clustering body from the real (absent) scikit-learn implementation.
    """
    fake_cluster = type(sys)("sklearn.cluster")

    class _DBSCAN:
        def __init__(self, *args, **kwargs):
            self._labels = np.array(labels)

        def fit_predict(self, _x):
            return self._labels

    fake_cluster.DBSCAN = _DBSCAN
    return fake_cluster


class TestClusterFacesMocked:
    """Exercise the real cluster_faces body with a mocked DBSCAN."""

    def test_empty_db_returns_empty(self, tmp_path):
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            with patch.dict(sys.modules, {"sklearn.cluster": _fake_dbscan([])}):
                assert cluster_faces(db) == {}

    def test_two_faces_one_cluster(self, tmp_path):
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            emb = np.ones(128) * 0.5
            face_ids = _seed_faces(db, [emb, emb])
            with patch.dict(sys.modules, {"sklearn.cluster": _fake_dbscan([0, 0])}):
                result = cluster_faces(db)
            assert len(result) == 1
            person_id = next(iter(result))
            assert set(result[person_id]) == set(face_ids)

    def test_two_distinct_clusters(self, tmp_path):
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            face_ids = _seed_faces(db, [np.zeros(128) for _ in range(4)])
            with patch.dict(sys.modules, {"sklearn.cluster": _fake_dbscan([0, 0, 1, 1])}):
                result = cluster_faces(db)
            assert len(result) == 2
            sizes = sorted(len(v) for v in result.values())
            assert sizes == [2, 2]
            all_fids = set()
            for fids in result.values():
                all_fids.update(fids)
            assert all_fids == set(face_ids)

    def test_noise_label_not_assigned(self, tmp_path):
        """DBSCAN label -1 (noise) faces must be left unassigned."""
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            face_ids = _seed_faces(db, [np.zeros(128) for _ in range(3)])
            with patch.dict(sys.modules, {"sklearn.cluster": _fake_dbscan([0, 0, -1])}):
                result = cluster_faces(db)
            clustered = []
            for fids in result.values():
                clustered.extend(fids)
            assert face_ids[2] not in clustered
            assert set(clustered) == {face_ids[0], face_ids[1]}

    def test_creates_person_rows(self, tmp_path):
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            _seed_faces(db, [np.ones(128), np.ones(128)])
            with patch.dict(sys.modules, {"sklearn.cluster": _fake_dbscan([0, 0])}):
                cluster_faces(db)
            persons = db.get_persons()
            assert len(persons) == 1
            assert persons[0].label.startswith("Person ")
            assert len(persons[0].face_ids) == 2

    def test_sets_person_id_on_faces(self, tmp_path):
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            _seed_faces(db, [np.ones(128), np.ones(128)])
            with patch.dict(sys.modules, {"sklearn.cluster": _fake_dbscan([0, 0])}):
                result = cluster_faces(db)
            person_id = next(iter(result))
            for fid in result[person_id]:
                row = db._conn.execute(
                    "SELECT person_id FROM faces WHERE id = ?", (fid,)
                ).fetchone()
                assert row[0] == person_id

    def test_label_numbering_is_one_based(self, tmp_path):
        """Cluster label 0 should produce 'Person 1' (label + 1)."""
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            _seed_faces(db, [np.ones(128), np.ones(128)])
            with patch.dict(sys.modules, {"sklearn.cluster": _fake_dbscan([0, 0])}):
                cluster_faces(db)
            persons = db.get_persons()
            assert persons[0].label == "Person 1"


class TestReclusterAuto:
    def test_clears_then_reclusters(self, tmp_path):
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            _seed_faces(db, [np.ones(128), np.ones(128)])
            with patch.object(db, "clear_auto_persons", wraps=db.clear_auto_persons) as clear:
                with patch.dict(sys.modules, {"sklearn.cluster": _fake_dbscan([0, 0])}):
                    result = recluster_auto(db)
            clear.assert_called_once()
            assert len(result) == 1

    def test_preserves_trusted_person_faces(self, tmp_path):
        """Recluster must not steal faces from a trusted (named) person.

        Regression for the bug where the background recluster during a scan
        fed *every* embedding into DBSCAN — including faces already assigned to
        a trusted/Photos-imported person — and reassigned them to a fresh
        "Person N" cluster, silently emptying the named person.
        """
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            pid = db.create_person(
                label="Alexander Ryabikov", confirmed=True, source="photos", trusted=True
            )
            face_ids = _seed_faces(db, [np.ones(128) for _ in range(3)])
            for fid in face_ids:
                db.set_person_id(fid, pid)

            # DBSCAN would group these 3 faces, but they belong to a trusted
            # person and must be excluded from clustering entirely.
            with patch.dict(sys.modules, {"sklearn.cluster": _fake_dbscan([0, 0, 0])}):
                result = recluster_auto(db)

            assert result == {}  # nothing clusterable
            person = next(p for p in db.get_persons() if p.person_id == pid)
            assert len(person.face_ids) == 3  # trusted person keeps all its faces
            # No stray auto "Person N" was created.
            assert all(p.trusted for p in db.get_persons())

    def test_excludes_ignored_faces(self, tmp_path):
        """Ignored (trashed) faces must not be reclustered back into a person."""
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            face_ids = _seed_faces(db, [np.ones(128), np.ones(128)])
            db.ignore_face(face_ids[0])
            # Only the non-ignored face is fed to DBSCAN; the single returned
            # label clusters that face alone. The ignored face is never an input.
            with patch.dict(sys.modules, {"sklearn.cluster": _fake_dbscan([0])}):
                result = cluster_faces(db)
            # The lone cluster contains only the non-ignored face.
            assert list(result.values()) == [[face_ids[1]]]
            # The ignored face was never assigned to a cluster.
            person_id = db._conn.execute(
                "SELECT person_id FROM faces WHERE id = ?", (face_ids[0],)
            ).fetchone()[0]
            assert person_id is None


class TestClusterFacesImportError:
    """The ImportError guard fires when scikit-learn is absent."""

    def test_raises_import_error_when_sklearn_missing(self, tmp_path):
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            _seed_faces(db, [np.ones(128), np.ones(128)])

            saved = sys.modules.pop("sklearn.cluster", None)
            sklearn_saved = sys.modules.pop("sklearn", None)
            try:
                with patch.dict("sys.modules", {"sklearn": None, "sklearn.cluster": None}):
                    with pytest.raises(ImportError, match="scikit-learn"):
                        cluster_faces(db)
            finally:
                if saved is not None:
                    sys.modules["sklearn.cluster"] = saved
                if sklearn_saved is not None:
                    sys.modules["sklearn"] = sklearn_saved
