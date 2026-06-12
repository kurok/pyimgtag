"""Tests for the face_recognition pre-flight dependency check.

``face_recognition`` and ``face_recognition_models`` are optional ([face])
deps that are NOT installed in CI. Each branch of ``_ensure_face_dep`` is
exercised by injecting fake modules into ``sys.modules`` (or forcing the
import to raise), so the real body runs and counts toward coverage without
the heavy native dependency being present.
"""

from __future__ import annotations

import builtins
import sys
from types import ModuleType
from unittest.mock import patch

import pytest

from pyimgtag._face_dep_check import (
    MissingFaceModelsError,
    _ensure_face_dep,
    _inject_pkg_resources_shim,
)


def _fake_module(name: str) -> ModuleType:
    return ModuleType(name)


class TestEnsureFaceDepSuccess:
    def test_returns_face_recognition_module(self):
        """Both deps importable → returns the face_recognition module."""
        fake_models = _fake_module("face_recognition_models")
        fake_fr = _fake_module("face_recognition")
        with patch.dict(
            sys.modules,
            {"face_recognition_models": fake_models, "face_recognition": fake_fr},
        ):
            result = _ensure_face_dep()
        assert result is fake_fr


class TestEnsureFaceDepModelsMissing:
    """Cover the ModuleNotFoundError discrimination (models vs pkg_resources)."""

    def test_models_package_missing_uses_models_hint(self):
        """ModuleNotFoundError whose name != pkg_resources → models hint."""
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "face_recognition_models":
                raise ModuleNotFoundError(
                    "No module named 'face_recognition_models'",
                    name="face_recognition_models",
                )
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            with pytest.raises(MissingFaceModelsError) as exc:
                _ensure_face_dep()
        msg = str(exc.value)
        assert "face_recognition_models is not installed" in msg
        assert sys.executable in msg

    def test_any_module_not_found_from_models_uses_models_hint(self):
        """Any ModuleNotFoundError from face_recognition_models → models install hint.

        The pkg_resources case is handled by the shim before the import, so
        if the import still raises ModuleNotFoundError for any reason, the
        models install hint is the actionable response.
        """
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "face_recognition_models":
                raise ModuleNotFoundError(
                    "No module named 'face_recognition_models'",
                    name="face_recognition_models",
                )
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            with pytest.raises(MissingFaceModelsError) as exc:
                _ensure_face_dep()
        msg = str(exc.value)
        assert "face_recognition_models is not installed" in msg
        assert sys.executable in msg

    def test_generic_import_error_uses_models_hint(self):
        """A plain ImportError (not ModuleNotFoundError) → models hint (lines 109-110)."""
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "face_recognition_models":
                raise ImportError("broken in some other way")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            with pytest.raises(MissingFaceModelsError) as exc:
                _ensure_face_dep()
        assert "face_recognition_models is not installed" in str(exc.value)


class TestEnsureFaceDepFaceRecognitionMissing:
    """Cover the branch where models import OK but face_recognition is absent."""

    def test_face_recognition_missing_raises_plain_import_error(self):
        real_import = builtins.__import__
        fake_models = _fake_module("face_recognition_models")

        def fake_import(name, *args, **kwargs):
            if name == "face_recognition":
                raise ImportError("No module named 'face_recognition'")
            return real_import(name, *args, **kwargs)

        with patch.dict(sys.modules, {"face_recognition_models": fake_models}):
            with patch.object(builtins, "__import__", side_effect=fake_import):
                with pytest.raises(ImportError) as exc:
                    _ensure_face_dep()
        msg = str(exc.value)
        assert "face_recognition is not installed" in msg
        assert "[face]" in msg
        # Must be a plain ImportError, NOT the MissingFaceModelsError subclass,
        # so callers can tell "models missing" from "face_recognition missing".
        assert not isinstance(exc.value, MissingFaceModelsError)


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
                # Remove the sentinel None so the import fails
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
