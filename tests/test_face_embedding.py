"""Tests for face embedding pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from pyimgtag.face_embedding import compute_embeddings, scan_and_store
from pyimgtag.models import FaceDetection
from pyimgtag.progress_db import ProgressDB


def _patch_face_modules(face_recognition_module):
    """Install fakes for both face deps so the pre-flight check passes."""
    return patch.dict(
        "sys.modules",
        {
            "face_recognition_models": MagicMock(),
            "face_recognition": face_recognition_module,
        },
    )


def _make_image(tmp_path: Path, size: tuple[int, int] = (640, 480)) -> Path:
    img = Image.new("RGB", size, color="white")
    path = tmp_path / "photo.jpg"
    img.save(path)
    return path


class TestComputeEmbeddings:
    def test_returns_empty_for_no_faces(self, tmp_path):
        path = _make_image(tmp_path)
        result = compute_embeddings(path, [])
        assert result == []

    def test_returns_embeddings_for_faces(self, tmp_path):
        path = _make_image(tmp_path)
        faces = [
            FaceDetection(image_path=str(path), bbox_x=10, bbox_y=20, bbox_w=50, bbox_h=60),
        ]
        fake_encoding = np.random.rand(128)
        mock_fr = MagicMock()
        mock_fr.face_encodings.return_value = [fake_encoding]
        with _patch_face_modules(mock_fr):
            result = compute_embeddings(path, faces)
        assert len(result) == 1
        np.testing.assert_array_equal(result[0], fake_encoding)

    def test_passes_known_locations_in_correct_format(self, tmp_path):
        path = _make_image(tmp_path)
        faces = [
            FaceDetection(image_path=str(path), bbox_x=30, bbox_y=50, bbox_w=120, bbox_h=150),
        ]
        mock_fr = MagicMock()
        mock_fr.face_encodings.return_value = [np.zeros(128)]
        with _patch_face_modules(mock_fr):
            compute_embeddings(path, faces)
        call_kwargs = mock_fr.face_encodings.call_args[1]
        # (top, right, bottom, left) = (bbox_y, bbox_x+bbox_w, bbox_y+bbox_h, bbox_x)
        assert call_kwargs["known_face_locations"] == [(50, 150, 200, 30)]

    def test_multiple_faces(self, tmp_path):
        path = _make_image(tmp_path)
        faces = [
            FaceDetection(image_path=str(path), bbox_x=10, bbox_y=20, bbox_w=50, bbox_h=60),
            FaceDetection(image_path=str(path), bbox_x=100, bbox_y=100, bbox_w=80, bbox_h=80),
        ]
        mock_fr = MagicMock()
        mock_fr.face_encodings.return_value = [np.ones(128), np.ones(128) * 2]
        with _patch_face_modules(mock_fr):
            result = compute_embeddings(path, faces)
        assert len(result) == 2

    def test_file_not_found(self, tmp_path):
        faces = [FaceDetection(image_path="/missing.jpg")]
        mock_fr = MagicMock()
        with _patch_face_modules(mock_fr):
            with pytest.raises(FileNotFoundError):
                compute_embeddings(tmp_path / "missing.jpg", faces)

    def test_import_error_without_face_recognition(self, tmp_path):
        path = _make_image(tmp_path)
        faces = [FaceDetection(image_path=str(path))]
        with patch.dict(
            "sys.modules",
            {"face_recognition_models": MagicMock(), "face_recognition": None},
        ):
            with pytest.raises(ImportError, match="face_recognition is not installed"):
                compute_embeddings(path, faces)

    def test_raises_missing_models_error(self, tmp_path):
        """Pre-flight surfaces a clear error when face_recognition_models is gone."""
        from pyimgtag._face_dep_check import MissingFaceModelsError

        path = _make_image(tmp_path)
        faces = [FaceDetection(image_path=str(path))]
        with patch.dict(
            "sys.modules",
            {"face_recognition_models": None, "face_recognition": MagicMock()},
        ):
            with pytest.raises(MissingFaceModelsError) as excinfo:
                compute_embeddings(path, faces)
            msg = str(excinfo.value)
            assert "face_recognition_models" in msg
            assert "git+https://github.com/ageitgey/face_recognition_models" in msg


class TestScanAndStore:
    def _mock_detect_and_embed(self, faces, embeddings):
        """Return a context manager that patches detect_faces and compute_embeddings."""
        return (
            patch("pyimgtag.face_embedding.detect_faces", return_value=faces),
            patch("pyimgtag.face_embedding.compute_embeddings", return_value=embeddings),
        )

    def test_stores_faces_in_db(self, tmp_path):
        path = _make_image(tmp_path)
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            faces = [
                FaceDetection(image_path=str(path), bbox_x=10, bbox_y=20, bbox_w=50, bbox_h=60),
            ]
            emb = np.random.rand(128)
            p_detect, p_embed = self._mock_detect_and_embed(faces, [emb])
            with p_detect, p_embed:
                count = scan_and_store(path, db)
            assert count == 1
            stored = db.get_faces_for_image(str(path))
            assert len(stored) == 1
            assert stored[0]["bbox_x"] == 10

    def test_stores_embedding_in_db(self, tmp_path):
        path = _make_image(tmp_path)
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            faces = [FaceDetection(image_path=str(path))]
            emb = np.ones(128) * 0.5
            p_detect, p_embed = self._mock_detect_and_embed(faces, [emb])
            with p_detect, p_embed:
                scan_and_store(path, db)
            all_emb = db.get_all_embeddings()
            assert len(all_emb) == 1
            np.testing.assert_array_almost_equal(all_emb[0][1], emb)

    def test_skips_already_scanned_image(self, tmp_path):
        path = _make_image(tmp_path)
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            # First scan
            faces = [FaceDetection(image_path=str(path), bbox_x=5, bbox_y=5)]
            emb = np.zeros(128)
            p_detect, p_embed = self._mock_detect_and_embed(faces, [emb])
            with p_detect, p_embed:
                scan_and_store(path, db)

            # Second scan — should skip
            p_detect2, p_embed2 = self._mock_detect_and_embed([], [])
            with p_detect2 as mock_d, p_embed2:
                count = scan_and_store(path, db)
            assert count == 0
            mock_d.assert_not_called()

    def test_returns_zero_for_no_faces(self, tmp_path):
        path = _make_image(tmp_path)
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            p_detect, p_embed = self._mock_detect_and_embed([], [])
            with p_detect, p_embed:
                count = scan_and_store(path, db)
            assert count == 0
            assert db.get_face_count() == 0

    def test_multiple_faces_stored(self, tmp_path):
        path = _make_image(tmp_path)
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            faces = [
                FaceDetection(image_path=str(path), bbox_x=10, bbox_y=20),
                FaceDetection(image_path=str(path), bbox_x=200, bbox_y=100),
            ]
            embs = [np.ones(128), np.ones(128) * 2]
            p_detect, p_embed = self._mock_detect_and_embed(faces, embs)
            with p_detect, p_embed:
                count = scan_and_store(path, db)
            assert count == 2
            assert db.get_face_count() == 2
            all_emb = db.get_all_embeddings()
            assert len(all_emb) == 2

    def test_handles_fewer_embeddings_than_faces(self, tmp_path):
        """If face_recognition returns fewer encodings than faces, store None for the rest."""
        path = _make_image(tmp_path)
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            faces = [
                FaceDetection(image_path=str(path), bbox_x=10, bbox_y=20),
                FaceDetection(image_path=str(path), bbox_x=200, bbox_y=100),
            ]
            embs = [np.ones(128)]  # only 1 embedding for 2 faces
            p_detect, p_embed = self._mock_detect_and_embed(faces, embs)
            with p_detect, p_embed:
                count = scan_and_store(path, db)
            assert count == 2
            assert db.get_face_count() == 2
            all_emb = db.get_all_embeddings()
            assert len(all_emb) == 1  # only 1 has embedding

    def test_passes_model_and_max_dim(self, tmp_path):
        path = _make_image(tmp_path)
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            with (
                patch("pyimgtag.face_embedding.detect_faces", return_value=[]) as mock_d,
                patch("pyimgtag.face_embedding.compute_embeddings", return_value=[]),
            ):
                scan_and_store(path, db, max_dim=800, model="cnn")
            mock_d.assert_called_once_with(path, max_dim=800, model="cnn")
