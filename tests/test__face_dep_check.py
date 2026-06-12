"""Tests for the face_recognition pre-flight dependency check.

``face_recognition`` and ``face_recognition_models`` are optional ([face])
deps that are NOT installed in CI. Each branch of ``_ensure_face_dep`` is
exercised by injecting fake modules into ``sys.modules`` (or forcing the
import to raise), so the real body runs and counts toward coverage without
the heavy native dependency being present.

All tests that exercise the "models missing" path also patch
``pyimgtag._face_dep_check``'s import of ``inject_shim`` so no real network
call is attempted.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag._face_dep_check import (
    MissingFaceModelsError,
    _ensure_face_dep,
    _inject_pkg_resources_shim,
)


def _fake_module(name: str) -> ModuleType:
    return ModuleType(name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_face_modules(*, models_ok: bool = True, fr_ok: bool = True):
    """Return a context manager that injects fake face deps into sys.modules.

    ``inject_shim`` is also patched to a no-op so tests don't hit the network.
    """
    mods = {}
    if models_ok:
        fake_models = MagicMock()
        fake_models.face_recognition_model_location.return_value = "/fake/model.dat"
        mods["face_recognition_models"] = fake_models
    else:
        mods["face_recognition_models"] = None  # simulate missing
    if fr_ok:
        mods["face_recognition"] = _fake_module("face_recognition")
    else:
        mods["face_recognition"] = None  # simulate missing

    return patch.dict(sys.modules, mods)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class TestEnsureFaceDepSuccess:
    def test_returns_face_recognition_module(self):
        """Both deps importable → returns the face_recognition module."""
        fake_fr = _fake_module("face_recognition")
        fake_models = MagicMock()
        fake_models.face_recognition_model_location.return_value = "/fake/m.dat"
        with patch.dict(
            sys.modules,
            {"face_recognition_models": fake_models, "face_recognition": fake_fr},
        ):
            with patch("pyimgtag._face_model_cache.inject_shim"):
                result = _ensure_face_dep()
        assert result is fake_fr


# ---------------------------------------------------------------------------
# Models missing / download-failed path
# ---------------------------------------------------------------------------


class TestEnsureFaceDepModelsMissing:
    """Cover the path where inject_shim raises (download failed)."""

    def test_inject_shim_failure_raises_missing_face_models_error(self):
        """If inject_shim raises, _ensure_face_dep wraps it in MissingFaceModelsError."""
        with patch.dict(sys.modules, {"face_recognition": None}):
            with patch(
                "pyimgtag._face_model_cache.inject_shim",
                side_effect=OSError("network unreachable"),
            ):
                with pytest.raises(MissingFaceModelsError) as exc:
                    _ensure_face_dep()
        msg = str(exc.value)
        assert "Could not download face recognition models automatically" in msg
        assert sys.executable in msg

    def test_models_import_fails_after_inject_raises_missing_error(self):
        """ModuleNotFoundError from face_recognition_models import → MissingFaceModelsError."""
        with patch.dict(sys.modules, {"face_recognition_models": None}):
            with patch("pyimgtag._face_model_cache.inject_shim"):
                with pytest.raises(MissingFaceModelsError) as exc:
                    _ensure_face_dep()
        assert "Could not download face recognition models automatically" in str(exc.value)

    def test_generic_import_error_uses_download_failed_hint(self):
        """A plain ImportError from face_recognition_models → download-failed hint."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "face_recognition_models":
                raise ImportError("broken in some other way")
            return real_import(name, *args, **kwargs)

        with patch.dict(sys.modules, {"face_recognition_models": None}):
            with patch("pyimgtag._face_model_cache.inject_shim"):
                with patch.object(builtins, "__import__", side_effect=fake_import):
                    with pytest.raises(MissingFaceModelsError) as exc:
                        _ensure_face_dep()
        assert "Could not download" in str(exc.value)


# ---------------------------------------------------------------------------
# face_recognition missing path
# ---------------------------------------------------------------------------


class TestEnsureFaceDepFaceRecognitionMissing:
    """Cover the branch where models import OK but face_recognition is absent."""

    def test_face_recognition_missing_raises_plain_import_error(self):
        fake_models = MagicMock()
        fake_models.face_recognition_model_location.return_value = "/fake/m.dat"
        with patch.dict(
            sys.modules,
            {"face_recognition_models": fake_models, "face_recognition": None},
        ):
            with patch("pyimgtag._face_model_cache.inject_shim"):
                with pytest.raises(ImportError) as exc:
                    _ensure_face_dep()
        msg = str(exc.value)
        assert "face_recognition is not installed" in msg
        assert "[face]" in msg
        assert not isinstance(exc.value, MissingFaceModelsError)


# ---------------------------------------------------------------------------
# _inject_pkg_resources_shim
# ---------------------------------------------------------------------------


class TestInjectPkgResourcesShim:
    """Cover _inject_pkg_resources_shim behaviour."""

    def test_noop_when_pkg_resources_available(self):
        """If pkg_resources is already importable, nothing is injected."""
        import types

        original = sys.modules.get("pkg_resources")
        real_mod = types.ModuleType("pkg_resources")
        try:
            sys.modules["pkg_resources"] = real_mod
            _inject_pkg_resources_shim()
            assert sys.modules.get("pkg_resources") is real_mod
        finally:
            if original is None:
                sys.modules.pop("pkg_resources", None)
            else:
                sys.modules["pkg_resources"] = original

    def test_injects_shim_when_pkg_resources_absent(self):
        """When pkg_resources is absent a shim with resource_filename is injected."""
        original = sys.modules.pop("pkg_resources", None)
        try:
            with patch.dict(sys.modules, {"pkg_resources": None}):
                del sys.modules["pkg_resources"]
                _inject_pkg_resources_shim()
                shim = sys.modules.get("pkg_resources")
                assert shim is not None
                assert callable(getattr(shim, "resource_filename", None))
        finally:
            if original is None:
                sys.modules.pop("pkg_resources", None)
            else:
                sys.modules["pkg_resources"] = original

    def test_shim_resource_filename_returns_path(self):
        """The injected resource_filename returns a string path under the package."""
        original = sys.modules.pop("pkg_resources", None)
        try:
            if "pkg_resources" in sys.modules:
                del sys.modules["pkg_resources"]
            _inject_pkg_resources_shim()
            shim = sys.modules.get("pkg_resources")
            if shim is None:
                pytest.skip("pkg_resources was importable; shim not injected")
            path = shim.resource_filename("pathlib", "")
            assert isinstance(path, str)
        finally:
            if original is None:
                sys.modules.pop("pkg_resources", None)
            else:
                sys.modules["pkg_resources"] = original
