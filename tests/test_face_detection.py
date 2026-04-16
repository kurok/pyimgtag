"""Tests for face detection pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from pyimgtag.face_detection import _load_and_resize, detect_faces


class TestCheckFaceRecognition:
    def test_import_error_when_missing(self):
        with patch.dict("sys.modules", {"face_recognition": None}):
            with pytest.raises(ImportError, match="face_recognition is not installed"):
                from pyimgtag.face_detection import _check_face_recognition

                _check_face_recognition()


class TestLoadAndResize:
    def test_loads_rgb_image(self, tmp_path):
        img = Image.new("RGB", (200, 100), color="red")
        path = tmp_path / "test.jpg"
        img.save(path)
        result = _load_and_resize(path, max_dim=1280)
        assert result.size == (200, 100)
        assert result.mode == "RGB"

    def test_resizes_large_image(self, tmp_path):
        img = Image.new("RGB", (2560, 1920))
        path = tmp_path / "big.jpg"
        img.save(path)
        result = _load_and_resize(path, max_dim=1280)
        assert max(result.size) == 1280
        # Aspect ratio preserved
        assert result.size == (1280, 960)

    def test_does_not_upscale_small_image(self, tmp_path):
        img = Image.new("RGB", (640, 480))
        path = tmp_path / "small.jpg"
        img.save(path)
        result = _load_and_resize(path, max_dim=1280)
        assert result.size == (640, 480)

    def test_converts_rgba_to_rgb(self, tmp_path):
        img = Image.new("RGBA", (100, 100))
        path = tmp_path / "alpha.png"
        img.save(path)
        result = _load_and_resize(path, max_dim=1280)
        assert result.mode == "RGB"

    def test_keeps_grayscale(self, tmp_path):
        img = Image.new("L", (100, 100))
        path = tmp_path / "gray.jpg"
        img.save(path)
        result = _load_and_resize(path, max_dim=1280)
        assert result.mode == "L"

    def test_converts_palette_to_rgb(self, tmp_path):
        img = Image.new("P", (100, 100))
        path = tmp_path / "palette.png"
        img.save(path)
        result = _load_and_resize(path, max_dim=1280)
        assert result.mode == "RGB"

    def test_heic_converted_before_load(self, tmp_path):
        # Create a real JPEG but name it .heic to trigger the HEIC path
        img = Image.new("RGB", (100, 100), color="blue")
        jpeg_path = tmp_path / "converted.jpg"
        img.save(jpeg_path)

        heic_path = tmp_path / "photo.heic"
        heic_path.write_bytes(b"fake")

        with (
            patch("pyimgtag.face_detection.is_heic", return_value=True),
            patch("pyimgtag.face_detection.convert_heic_to_jpeg", return_value=jpeg_path),
        ):
            result = _load_and_resize(heic_path, max_dim=1280)
            assert result.size == (100, 100)


class TestDetectFaces:
    def _make_image(self, tmp_path: Path, size: tuple[int, int] = (640, 480)) -> Path:
        img = Image.new("RGB", size, color="white")
        path = tmp_path / "photo.jpg"
        img.save(path)
        return path

    def test_returns_empty_for_no_faces(self, tmp_path):
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = []
        with patch.dict("sys.modules", {"face_recognition": mock_fr}):
            results = detect_faces(path)
        assert results == []

    def test_returns_face_detections(self, tmp_path):
        path = self._make_image(tmp_path)
        # face_recognition returns (top, right, bottom, left)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = [(50, 150, 200, 30)]
        with patch.dict("sys.modules", {"face_recognition": mock_fr}):
            results = detect_faces(path)
        assert len(results) == 1
        face = results[0]
        assert face.bbox_x == 30  # left
        assert face.bbox_y == 50  # top
        assert face.bbox_w == 120  # right - left = 150 - 30
        assert face.bbox_h == 150  # bottom - top = 200 - 50
        assert face.confidence == 1.0
        assert face.image_path == str(path)

    def test_returns_multiple_faces(self, tmp_path):
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = [
            (10, 60, 50, 20),
            (100, 200, 180, 120),
        ]
        with patch.dict("sys.modules", {"face_recognition": mock_fr}):
            results = detect_faces(path)
        assert len(results) == 2
        assert results[0].bbox_x == 20
        assert results[1].bbox_x == 120

    def test_passes_model_to_face_recognition(self, tmp_path):
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = []
        with patch.dict("sys.modules", {"face_recognition": mock_fr}):
            detect_faces(path, model="cnn")
        mock_fr.face_locations.assert_called_once()
        _, kwargs = mock_fr.face_locations.call_args
        assert kwargs["model"] == "cnn"

    def test_default_model_is_hog(self, tmp_path):
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = []
        with patch.dict("sys.modules", {"face_recognition": mock_fr}):
            detect_faces(path)
        _, kwargs = mock_fr.face_locations.call_args
        assert kwargs["model"] == "hog"

    def test_file_not_found_raises(self, tmp_path):
        mock_fr = MagicMock()
        with patch.dict("sys.modules", {"face_recognition": mock_fr}):
            with pytest.raises(FileNotFoundError, match="Image not found"):
                detect_faces(tmp_path / "missing.jpg")

    def test_resizes_before_detection(self, tmp_path):
        path = self._make_image(tmp_path, size=(3000, 2000))
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = []
        with patch.dict("sys.modules", {"face_recognition": mock_fr}):
            detect_faces(path, max_dim=640)
        # Verify the array passed to face_locations has resized dimensions
        call_args = mock_fr.face_locations.call_args
        img_array = call_args[0][0]
        assert img_array.shape[1] == 640  # width (resized)
        assert img_array.shape[0] <= 640  # height

    def test_accepts_string_path(self, tmp_path):
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = []
        with patch.dict("sys.modules", {"face_recognition": mock_fr}):
            results = detect_faces(str(path))
        assert results == []

    def test_import_error_without_face_recognition(self, tmp_path):
        path = self._make_image(tmp_path)
        with patch.dict("sys.modules", {"face_recognition": None}):
            with pytest.raises(ImportError, match="face_recognition is not installed"):
                detect_faces(path)
