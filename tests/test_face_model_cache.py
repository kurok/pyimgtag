"""Tests for the face model auto-download and shim injection."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag._face_model_cache import ensure_models_cached, inject_shim

# ---------------------------------------------------------------------------
# ensure_models_cached
# ---------------------------------------------------------------------------


class TestEnsureModelsCached:
    def test_creates_cache_dir(self, tmp_path):
        d = tmp_path / "subdir"
        assert not d.exists()

        def _noop_dl(name, size, dest):
            dest.write_bytes(b"fake")

        with patch("pyimgtag._face_model_cache._download", side_effect=_noop_dl):
            ensure_models_cached(d)

        assert d.is_dir()

    def test_downloads_missing_files(self, tmp_path):
        downloaded = []

        def _fake_dl(name, size, dest):
            downloaded.append(name)
            dest.write_bytes(b"data")

        with patch("pyimgtag._face_model_cache._download", side_effect=_fake_dl):
            paths = ensure_models_cached(tmp_path)

        assert len(downloaded) == 4
        assert all(p.exists() for p in paths.values())

    def test_skips_already_present_files(self, tmp_path):
        from pyimgtag._face_model_cache import _MODEL_FILES

        for name, _ in _MODEL_FILES:
            (tmp_path / name).write_bytes(b"cached")

        downloaded: list[str] = []

        def _fake_dl(name, size, dest):
            downloaded.append(name)

        with patch("pyimgtag._face_model_cache._download", side_effect=_fake_dl):
            paths = ensure_models_cached(tmp_path)

        assert downloaded == []
        assert len(paths) == 4

    def test_returns_correct_paths(self, tmp_path):
        def _fake_dl(name, size, dest):
            dest.write_bytes(b"x")

        with patch("pyimgtag._face_model_cache._download", side_effect=_fake_dl):
            paths = ensure_models_cached(tmp_path)

        assert paths["dlib_face_recognition_resnet_model_v1.dat"] == (
            tmp_path / "dlib_face_recognition_resnet_model_v1.dat"
        )
        assert paths["shape_predictor_68_face_landmarks.dat"] == (
            tmp_path / "shape_predictor_68_face_landmarks.dat"
        )

    def test_propagates_download_error(self, tmp_path):
        with patch(
            "pyimgtag._face_model_cache._download",
            side_effect=OSError("no network"),
        ):
            with pytest.raises(OSError, match="no network"):
                ensure_models_cached(tmp_path)

    def test_cleans_up_tmp_on_failure(self, tmp_path):
        """_download itself cleans the .tmp file on failure."""
        import warnings

        def _bad_urlretrieve(url, dest):
            Path(dest).write_bytes(b"partial")
            raise OSError("fail mid-download")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with patch("urllib.request.urlretrieve", side_effect=_bad_urlretrieve):
                with pytest.raises(OSError):
                    ensure_models_cached(tmp_path)

        assert not any(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# inject_shim
# ---------------------------------------------------------------------------


def _setup_missing_frm():
    """Remove face_recognition_models from sys.modules, return original."""
    return sys.modules.pop("face_recognition_models", None)


def _restore_frm(original):
    if original is None:
        sys.modules.pop("face_recognition_models", None)
    else:
        sys.modules["face_recognition_models"] = original


class TestInjectShim:
    def test_noop_when_real_package_works(self, tmp_path):
        """If face_recognition_models is importable and callable, skip injection."""
        real = MagicMock()
        real.face_recognition_model_location.return_value = "/some/path.dat"
        original = sys.modules.get("face_recognition_models")
        try:
            sys.modules["face_recognition_models"] = real
            inject_shim(tmp_path)
            assert sys.modules["face_recognition_models"] is real
        finally:
            if original is None:
                sys.modules.pop("face_recognition_models", None)
            else:
                sys.modules["face_recognition_models"] = original

    def test_injects_shim_when_package_missing(self, tmp_path):
        """When face_recognition_models is absent, inject shim after download."""

        def _fake_dl(name, size, dest):
            dest.write_bytes(b"model")

        original = _setup_missing_frm()
        try:
            with patch("pyimgtag._face_model_cache._download", side_effect=_fake_dl):
                inject_shim(tmp_path)

            shim = sys.modules.get("face_recognition_models")
            assert shim is not None
            assert callable(shim.face_recognition_model_location)
            assert callable(shim.pose_predictor_model_location)
            assert callable(shim.pose_predictor_five_point_model_location)
            assert callable(shim.cnn_face_detector_model_location)
        finally:
            _restore_frm(original)

    def test_shim_location_functions_return_strings(self, tmp_path):
        """Each shim location function returns a string path."""

        def _fake_dl(name, size, dest):
            dest.write_bytes(b"model")

        original = _setup_missing_frm()
        try:
            with patch("pyimgtag._face_model_cache._download", side_effect=_fake_dl):
                inject_shim(tmp_path)

            shim = sys.modules["face_recognition_models"]
            assert isinstance(shim.face_recognition_model_location(), str)
            assert isinstance(shim.pose_predictor_model_location(), str)
            assert isinstance(shim.pose_predictor_five_point_model_location(), str)
            assert isinstance(shim.cnn_face_detector_model_location(), str)
        finally:
            _restore_frm(original)

    def test_propagates_download_error(self, tmp_path):
        """If download fails, inject_shim lets the exception propagate."""
        original = _setup_missing_frm()
        try:
            with patch(
                "pyimgtag._face_model_cache._download",
                side_effect=OSError("timeout"),
            ):
                with pytest.raises(OSError, match="timeout"):
                    inject_shim(tmp_path)
        finally:
            _restore_frm(original)
