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


_MODELS_INSTALL_HINT = (
    "face_recognition_models is not installed. It's a runtime dependency\n"
    "of face_recognition that PyPI doesn't host, so it must come from\n"
    "the upstream git repo. Install it into THIS Python environment:\n"
    "\n"
    "    {python} -m pip install \\\n"
    '        "face_recognition_models @ git+https://github.com/ageitgey/face_recognition_models"\n'
    "\n"
    "If you've already run that command and still see this error, the\n"
    "models likely landed in a different venv. Run:\n"
    "\n"
    "    {python} -m pip show face_recognition_models\n"
    "\n"
    "to confirm the install path matches your pyimgtag environment."
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
            return str(_pl.Path(mod.__file__).parent / resource_name)

    shim = types.ModuleType("pkg_resources")
    shim.resource_filename = _resource_filename  # type: ignore[attr-defined]
    sys.modules["pkg_resources"] = shim


def _ensure_face_dep() -> ModuleType:
    """Import and return the ``face_recognition`` module after pre-flight checks.

    The check order matters: we probe ``face_recognition_models`` *first*
    so we can raise our own clear error before ``face_recognition``'s own
    import-time stderr message fires.

    Returns:
        The imported ``face_recognition`` module.

    Raises:
        MissingFaceModelsError: If ``face_recognition_models`` is not
            importable. The error message includes the active Python
            interpreter path so the user can install the missing package
            into the correct environment.
        ImportError: If ``face_recognition`` itself is not installed.
    """
    _inject_pkg_resources_shim()

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message="pkg_resources is deprecated", category=UserWarning
            )
            import face_recognition_models  # noqa: F401
    except ModuleNotFoundError:
        raise MissingFaceModelsError(_MODELS_INSTALL_HINT.format(python=sys.executable)) from None
    except ImportError:
        raise MissingFaceModelsError(_MODELS_INSTALL_HINT.format(python=sys.executable)) from None

    try:
        import face_recognition
    except ImportError:
        raise ImportError(
            "face_recognition is not installed. "
            "Install the [face] extra: pip install 'pyimgtag[face]'"
        ) from None

    return face_recognition
