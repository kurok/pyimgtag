"""Tests for face detection pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from pyimgtag.face.detection import _load_and_resize, detect_faces


def _patch_face_modules(face_recognition_module):
    """Install fakes for both face deps so the pre-flight check passes."""
    return patch.dict(
        "sys.modules",
        {
            "face_recognition_models": MagicMock(),
            "face_recognition": face_recognition_module,
        },
    )


class TestCheckFaceRecognition:
    def test_import_error_when_face_recognition_missing(self):
        # face_recognition_models present, face_recognition missing
        with patch.dict(
            "sys.modules",
            {"face_recognition_models": MagicMock(), "face_recognition": None},
        ):
            with pytest.raises(ImportError, match="face_recognition is not installed"):
                from pyimgtag.face.detection import _check_face_recognition

                _check_face_recognition()

    def test_raises_when_face_recognition_models_missing(self):
        # face_recognition_models missing — should raise the pre-flight error
        # with the actionable hint, even if face_recognition itself is fine.
        with patch.dict(
            "sys.modules",
            {"face_recognition_models": None, "face_recognition": MagicMock()},
        ):
            from pyimgtag._face_dep_check import MissingFaceModelsError
            from pyimgtag.face.detection import _check_face_recognition

            with pytest.raises(MissingFaceModelsError) as excinfo:
                _check_face_recognition()
            msg = str(excinfo.value)
            assert "face_recognition_models" in msg
            assert "git+https://github.com/ageitgey/face_recognition_models" in msg

    def test_models_import_error_raises_missing_face_models(self):
        """Any ImportError from face_recognition_models raises MissingFaceModelsError.

        The pkg_resources case is handled transparently by the shim in
        _inject_pkg_resources_shim, so any residual ImportError from the
        models package maps to the models install hint.
        """
        import builtins

        from pyimgtag._face_dep_check import MissingFaceModelsError, _ensure_face_dep

        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "face_recognition_models":
                raise ModuleNotFoundError(
                    "No module named 'face_recognition_models'",
                    name="face_recognition_models",
                )
            return real_import(name, globals, locals, fromlist, level)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            with pytest.raises(MissingFaceModelsError) as excinfo:
                _ensure_face_dep()

        msg = str(excinfo.value)
        assert "face_recognition_models is not installed" in msg
        assert "git+https://github.com/ageitgey/face_recognition_models" in msg


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

    def test_converts_grayscale_to_rgb(self, tmp_path):
        # dlib's compute_face_descriptor requires 3-channel input, so grayscale
        # must be converted or encoding crashes after detection succeeds.
        img = Image.new("L", (100, 100))
        path = tmp_path / "gray.jpg"
        img.save(path)
        result = _load_and_resize(path, max_dim=1280)
        assert result.mode == "RGB"

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
            patch("pyimgtag.face.detection.is_heic", return_value=True),
            patch("pyimgtag.face.detection.convert_heic_to_jpeg", return_value=jpeg_path),
        ):
            result = _load_and_resize(heic_path, max_dim=1280)
            assert result.size == (100, 100)

    def test_heic_temp_conversion_cleaned_up_after_load(self, tmp_path):
        """Regression: the owned mkdtemp dir from convert_heic_to_jpeg must be
        removed once pixel data is loaded — every HEIC scan used to leak a
        full-resolution JPEG in a ``pyimgtag_heic_*`` temp dir."""
        owned_dir = tmp_path / "pyimgtag_heic_fake"
        owned_dir.mkdir()
        jpeg_path = owned_dir / "converted.jpg"
        Image.new("RGB", (100, 100), color="blue").save(jpeg_path)

        heic_path = tmp_path / "photo.heic"
        heic_path.write_bytes(b"fake")

        with (
            patch("pyimgtag.face.detection.is_heic", return_value=True),
            patch("pyimgtag.face.detection.convert_heic_to_jpeg", return_value=jpeg_path),
        ):
            result = _load_and_resize(heic_path, max_dim=1280)

        assert result.size == (100, 100)
        assert not jpeg_path.exists()
        assert not owned_dir.exists()


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
        with _patch_face_modules(mock_fr):
            results = detect_faces(path)
        assert results == []

    def test_returns_face_detections(self, tmp_path):
        path = self._make_image(tmp_path)
        # face_recognition returns (top, right, bottom, left)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = [(50, 150, 200, 30)]
        with _patch_face_modules(mock_fr):
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
        with _patch_face_modules(mock_fr):
            results = detect_faces(path)
        assert len(results) == 2
        assert results[0].bbox_x == 20
        assert results[1].bbox_x == 120

    def test_passes_model_to_face_recognition(self, tmp_path):
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = []
        with _patch_face_modules(mock_fr):
            detect_faces(path, model="cnn")
        mock_fr.face_locations.assert_called_once()
        _, kwargs = mock_fr.face_locations.call_args
        assert kwargs["model"] == "cnn"

    def test_default_model_is_hog(self, tmp_path):
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = []
        with _patch_face_modules(mock_fr):
            detect_faces(path)
        _, kwargs = mock_fr.face_locations.call_args
        assert kwargs["model"] == "hog"

    def test_file_not_found_raises(self, tmp_path):
        mock_fr = MagicMock()
        with _patch_face_modules(mock_fr):
            with pytest.raises(FileNotFoundError, match="Image not found"):
                detect_faces(tmp_path / "missing.jpg")

    def test_resizes_before_detection(self, tmp_path):
        path = self._make_image(tmp_path, size=(3000, 2000))
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = []
        with _patch_face_modules(mock_fr):
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
        with _patch_face_modules(mock_fr):
            results = detect_faces(str(path))
        assert results == []

    def test_import_error_without_face_recognition(self, tmp_path):
        path = self._make_image(tmp_path)
        with patch.dict(
            "sys.modules",
            {"face_recognition_models": MagicMock(), "face_recognition": None},
        ):
            with pytest.raises(ImportError, match="face_recognition is not installed"):
                detect_faces(path)

    def test_raises_missing_models_error(self, tmp_path):
        """Pre-flight surfaces a clear error when face_recognition_models is gone."""
        from pyimgtag._face_dep_check import MissingFaceModelsError

        path = self._make_image(tmp_path)
        with patch.dict(
            "sys.modules",
            {"face_recognition_models": None, "face_recognition": MagicMock()},
        ):
            with pytest.raises(MissingFaceModelsError) as excinfo:
                detect_faces(path)
            msg = str(excinfo.value)
            assert "face_recognition_models" in msg
            assert "git+https://github.com/ageitgey/face_recognition_models" in msg


class TestDetectFacesQuality:
    def _make_image(self, tmp_path: Path) -> Path:
        img = Image.new("RGB", (640, 480), color="white")
        path = tmp_path / "photo.jpg"
        img.save(path)
        return path

    def test_passes_upsample_to_face_recognition(self, tmp_path):
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = []
        with _patch_face_modules(mock_fr):
            detect_faces(path, upsample=3)
        _, kwargs = mock_fr.face_locations.call_args
        assert kwargs["number_of_times_to_upsample"] == 3

    def test_default_upsample_is_one(self, tmp_path):
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = []
        with _patch_face_modules(mock_fr):
            detect_faces(path)
        _, kwargs = mock_fr.face_locations.call_args
        assert kwargs["number_of_times_to_upsample"] == 1

    def test_min_face_size_drops_small_detections(self, tmp_path):
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        # (top, right, bottom, left): a 20x20 face and a 120x150 face
        mock_fr.face_locations.return_value = [(0, 20, 20, 0), (50, 150, 200, 30)]
        with _patch_face_modules(mock_fr):
            results = detect_faces(path, min_face_size=50)
        assert len(results) == 1
        assert results[0].bbox_w == 120  # the 20px face was filtered out

    def test_min_face_size_zero_keeps_all(self, tmp_path):
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = [(0, 20, 20, 0), (50, 150, 200, 30)]
        with _patch_face_modules(mock_fr):
            results = detect_faces(path, min_face_size=0)
        assert len(results) == 2

    def test_min_face_size_keeps_face_equal_to_threshold(self, tmp_path):
        # The filter uses `< min_face_size`, so a face exactly at the threshold
        # is KEPT (boundary case).
        path = self._make_image(tmp_path)
        mock_fr = MagicMock()
        mock_fr.face_locations.return_value = [(0, 50, 50, 0)]  # 50x50
        with _patch_face_modules(mock_fr):
            results = detect_faces(path, min_face_size=50)
        assert len(results) == 1
