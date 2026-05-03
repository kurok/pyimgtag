"""Pre-flight check for the face_recognition runtime dependency.

The ``face_recognition`` PyPI wheel does **not** declare its data-files
dependency ``face_recognition_models`` because that package was never
published to PyPI — it only exists as a git URL. When the models package
is missing, ``face_recognition`` swallows the ``ImportError`` and prints
its own stderr message *before* raising, so the user sees no Python
traceback. This module catches that case earlier and surfaces a clear,
actionable error.

It is imported lazily by :mod:`pyimgtag.face_detection` and
:mod:`pyimgtag.face_embedding` to keep ``face_recognition`` itself out of
the import graph until a face operation is actually requested.
"""

from __future__ import annotations

import sys
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

# face_recognition_models does ``from pkg_resources import resource_filename``
# at import time. ``pkg_resources`` ships with ``setuptools``, which is no
# longer bundled with Python 3.12+ in many distributions, so the package
# install can succeed yet the import raises ``ModuleNotFoundError: No
# module named 'pkg_resources'``. Detect that specific failure and ask the
# user to install setuptools instead of re-installing the models package.
_PKG_RESOURCES_HINT = (
    "face_recognition_models is installed but cannot be imported because\n"
    "``pkg_resources`` (part of setuptools) is missing from this Python\n"
    "environment. setuptools is no longer bundled with Python 3.12+ but\n"
    "face_recognition_models still uses ``pkg_resources`` at import time.\n"
    "Install setuptools into THIS Python environment:\n"
    "\n"
    "    {python} -m pip install setuptools\n"
    "\n"
    "Then re-run your pyimgtag faces command."
)


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
            into the correct environment, and discriminates between
            "package not installed" and "pkg_resources / setuptools
            missing" so the hint matches the real cause.
        ImportError: If ``face_recognition`` itself is not installed.
    """
    try:
        import face_recognition_models  # noqa: F401
    except ModuleNotFoundError as exc:
        # Disambiguate "models package missing" vs. "models package
        # installed but its `from pkg_resources import …` fails".
        if exc.name == "pkg_resources":
            hint = _PKG_RESOURCES_HINT
        else:
            hint = _MODELS_INSTALL_HINT
        raise MissingFaceModelsError(hint.format(python=sys.executable)) from None
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
