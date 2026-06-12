"""Pre-flight check for the face_recognition runtime dependency.

The ``face_recognition`` PyPI wheel does **not** declare its data-files
dependency ``face_recognition_models`` because that package was never
published to PyPI — it only exists as a git URL. When the models package
is missing, ``face_recognition`` swallows the ``ImportError`` and prints
its own stderr message *before* raising, so the user sees no Python
traceback. This module catches that case earlier and surfaces a clear,
actionable error.

It is imported lazily by :mod:`pyimgtag.face.detection` and
:mod:`pyimgtag.face.embedding` to keep ``face_recognition`` itself out of
the import graph until a face operation is actually requested.
"""

from __future__ import annotations

import sys
import warnings
from types import ModuleType


class MissingFaceModelsError(ImportError):
    """Raised when ``face_recognition_models`` is not importable.

    Inherits from ``ImportError`` so existing callers that already handle
    ``ImportError`` (e.g. ``pyimgtag.commands.faces``) catch this without
    code changes.
    """


_DOWNLOAD_FAILED_HINT = (
    "Could not download face recognition models automatically.\n"
    "Check your internet connection, or install the models manually:\n"
    "\n"
    "    {python} -m pip install \\\n"
    '        "face_recognition_models @ git+https://github.com/ageitgey/face_recognition_models"\n'
    "\n"
    "If you prefer to download the model files yourself, set\n"
    "PYIMGTAG_FACE_MODEL_DIR to the directory containing the .dat files\n"
    "and re-run the command."
)


def _inject_pkg_resources_shim() -> None:
    """Inject a minimal pkg_resources shim when the real one is absent.

    ``face_recognition_models`` calls ``pkg_resources.resource_filename``
    at import time. setuptools>=81 no longer bundles ``pkg_resources``, so
    on those environments ``import pkg_resources`` raises
    ``ModuleNotFoundError``. We provide a shim that implements only
    ``resource_filename`` via ``importlib.resources.files`` (stdlib since
    Python 3.9) to keep ``face_recognition_models`` importable.
    """
    try:
        import pkg_resources  # noqa: F401

        return
    except ModuleNotFoundError:
        pass

    import importlib.resources as _ir
    import pathlib as _pl
    import types

    def _resource_filename(package_or_requirement: object, resource_name: str) -> str:
        package_name = (
            package_or_requirement
            if isinstance(package_or_requirement, str)
            else str(package_or_requirement)
        )
        try:
            return str(_ir.files(package_name) / resource_name)
        except Exception:
            mod = sys.modules.get(package_name) or __import__(package_name)
            file = getattr(mod, "__file__", None) or ""
            return str(_pl.Path(file).parent / resource_name)

    shim = types.ModuleType("pkg_resources")
    shim.resource_filename = _resource_filename  # type: ignore[attr-defined]
    sys.modules["pkg_resources"] = shim


def _ensure_face_dep() -> ModuleType:
    """Import and return the ``face_recognition`` module after pre-flight checks.

    On the first call, model files are downloaded automatically to
    ``~/.cache/pyimgtag/face_models/`` if ``face_recognition_models`` is not
    already installed.  Subsequent calls return instantly (models are cached
    on disk; ``face_recognition`` itself is already in ``sys.modules``).

    Returns:
        The imported ``face_recognition`` module.

    Raises:
        MissingFaceModelsError: If ``face_recognition_models`` is unavailable
            and the automatic download failed (e.g. no internet access).
        ImportError: If ``face_recognition`` itself is not installed.
    """
    _inject_pkg_resources_shim()

    # Auto-download the model .dat files and inject a shim so that
    # face_recognition finds them without a manual install step.
    try:
        from pyimgtag._face_model_cache import inject_shim

        inject_shim()
    except Exception as exc:
        raise MissingFaceModelsError(_DOWNLOAD_FAILED_HINT.format(python=sys.executable)) from exc

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message="pkg_resources is deprecated", category=UserWarning
            )
            import face_recognition_models  # noqa: F401
    except ModuleNotFoundError:
        raise MissingFaceModelsError(_DOWNLOAD_FAILED_HINT.format(python=sys.executable)) from None
    except ImportError:
        raise MissingFaceModelsError(_DOWNLOAD_FAILED_HINT.format(python=sys.executable)) from None

    try:
        import face_recognition
    except ImportError:
        raise ImportError(
            "face_recognition is not installed. "
            "Install the [face] extra: pip install 'pyimgtag[face]'"
        ) from None

    return face_recognition
