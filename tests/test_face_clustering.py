"""Tests for face clustering pipeline."""

from __future__ import annotations

import numpy as np
import pytest

from pyimgtag.models import FaceDetection
from pyimgtag.progress_db import ProgressDB

sklearn = pytest.importorskip("sklearn", reason="scikit-learn not installed")

from pyimgtag.face_clustering import cluster_faces  # noqa: E402


def _seed_faces(db: ProgressDB, embeddings: list[np.ndarray], prefix: str = "/img") -> list[int]:
    """Insert faces with given embeddings and return their ids."""
    face_ids = []
    for i, emb in enumerate(embeddings):
        det = FaceDetection(image_path=f"{prefix}/{i}.jpg")
        fid = db.insert_face(f"{prefix}/{i}.jpg", det, embedding=emb)
        face_ids.append(fid)
    return face_ids


class TestClusterFaces:
    def test_empty_db_returns_empty(self, tmp_path):
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            result = cluster_faces(db)
            assert result == {}

    def test_single_face_no_cluster(self, tmp_path):
        """One face cannot form a cluster with min_samples=2."""
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            _seed_faces(db, [np.zeros(128)])
            result = cluster_faces(db)
            assert result == {}

    def test_two_identical_embeddings_form_cluster(self, tmp_path):
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            emb = np.ones(128) * 0.5
            face_ids = _seed_faces(db, [emb, emb])
            result = cluster_faces(db)
            assert len(result) == 1
            person_id = next(iter(result))
            assert set(result[person_id]) == set(face_ids)

    def test_two_distinct_groups(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            # Group A: embeddings near [1, 0, 0, ...]
            a = np.zeros(128)
            a[0] = 1.0
            # Group B: embeddings near [0, 1, 0, ...]
            b = np.zeros(128)
            b[1] = 1.0
            # Add small noise so they're close but not identical
            rng = np.random.RandomState(42)
            embs = [
                a + rng.normal(0, 0.01, 128),
                a + rng.normal(0, 0.01, 128),
                b + rng.normal(0, 0.01, 128),
                b + rng.normal(0, 0.01, 128),
            ]
            face_ids = _seed_faces(db, embs)
            result = cluster_faces(db, eps=0.5, min_samples=2)
            assert len(result) == 2
            # Each cluster should have exactly 2 faces
            cluster_sizes = sorted(len(v) for v in result.values())
            assert cluster_sizes == [2, 2]
            # Group A faces and group B faces should be in different clusters
            all_fids = set()
            for fids in result.values():
                all_fids.update(fids)
            assert all_fids == set(face_ids)
        finally:
            db.close()

    def test_noise_faces_not_assigned(self, tmp_path):
        """An outlier far from any group should be noise (unassigned)."""
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            cluster_emb = np.zeros(128)
            outlier = np.ones(128) * 100.0
            face_ids = _seed_faces(db, [cluster_emb, cluster_emb, outlier])
            result = cluster_faces(db, eps=0.5, min_samples=2)
            # Only the two close faces should be clustered
            all_clustered = []
            for fids in result.values():
                all_clustered.extend(fids)
            assert face_ids[2] not in all_clustered
            assert set(all_clustered) == {face_ids[0], face_ids[1]}
        finally:
            db.close()

    def test_creates_person_rows(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            emb = np.ones(128)
            _seed_faces(db, [emb, emb])
            cluster_faces(db)
            persons = db.get_persons()
            assert len(persons) == 1
            assert persons[0].label.startswith("Person ")
            assert len(persons[0].face_ids) == 2
        finally:
            db.close()

    def test_sets_person_id_on_faces(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            emb = np.ones(128)
            _seed_faces(db, [emb, emb])
            result = cluster_faces(db)
            person_id = next(iter(result))
            for fid in result[person_id]:
                faces = db._conn.execute(
                    "SELECT person_id FROM faces WHERE id = ?", (fid,)
                ).fetchone()
                assert faces[0] == person_id
        finally:
            db.close()

    def test_custom_eps_tighter(self, tmp_path):
        """A very small eps should prevent clustering of slightly different embeddings."""
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            rng = np.random.RandomState(99)
            base = np.zeros(128)
            embs = [base + rng.normal(0, 0.1, 128) for _ in range(3)]
            _seed_faces(db, embs)
            # eps=0.001 is too tight for noise of scale 0.1
            result = cluster_faces(db, eps=0.001, min_samples=2)
            assert result == {}
        finally:
            db.close()

    def test_custom_min_samples(self, tmp_path):
        """min_samples=3 should require at least 3 faces to form a cluster."""
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            emb = np.ones(128) * 0.5
            _seed_faces(db, [emb, emb])  # only 2
            result = cluster_faces(db, min_samples=3)
            assert result == {}
        finally:
            db.close()

    def test_faces_without_embeddings_ignored(self, tmp_path):
        """Faces inserted without embeddings should not appear in clustering."""
        db = ProgressDB(db_path=tmp_path / "test.db")
        try:
            det = FaceDetection(image_path="/img/no_emb.jpg")
            db.insert_face("/img/no_emb.jpg", det)  # no embedding
            emb = np.ones(128)
            _seed_faces(db, [emb, emb])
            result = cluster_faces(db)
            all_fids = []
            for fids in result.values():
                all_fids.extend(fids)
            # The face without embedding should not be in any cluster
            assert db.get_face_count() == 3
            assert len(all_fids) == 2
        finally:
            db.close()
