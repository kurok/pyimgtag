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
# at import time. There are two ways this can fail:
#
# 1. ``setuptools`` itself is missing — Python 3.12+ no longer bundles it.
# 2. ``setuptools`` is installed at version 81.0.0 or newer, but
#    setuptools 81 *removed* the bundled ``pkg_resources`` module while
#    leaving the install metadata intact. So ``pip show setuptools``
#    succeeds yet ``import pkg_resources`` still raises
#    ``ModuleNotFoundError: No module named 'pkg_resources'``.
#
# Both surface the same exception, so this hint covers both — the
# ``setuptools<81`` pin is the load-bearing detail.
_PKG_RESOURCES_HINT = (
    "face_recognition_models is installed but cannot be imported because\n"
    "``pkg_resources`` is not available in this Python environment.\n"
    "``pkg_resources`` used to ship with setuptools, but setuptools\n"
    "**81.0.0 removed it** from the package while leaving the install\n"
    "metadata intact — so ``pip show setuptools`` succeeds yet\n"
    "``import pkg_resources`` raises ``ModuleNotFoundError``. Pin\n"
    "setuptools below 81 to bring ``pkg_resources`` back (the same\n"
    "command installs setuptools if it was missing entirely):\n"
    "\n"
    "    {python} -m pip install 'setuptools<81'\n"
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
