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

    def test_pkg_resources_missing_uses_pkg_resources_hint(self):
        """ModuleNotFoundError(name='pkg_resources') → setuptools/pkg_resources hint."""
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "face_recognition_models":
                raise ModuleNotFoundError("No module named 'pkg_resources'", name="pkg_resources")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            with pytest.raises(MissingFaceModelsError) as exc:
                _ensure_face_dep()
        msg = str(exc.value)
        assert "pkg_resources" in msg
        assert "setuptools" in msg

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
