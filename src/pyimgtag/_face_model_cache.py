"""Auto-download and cache the dlib face recognition model files.

``face_recognition_models`` was never published to PyPI; it only exists as a
git URL. This module fetches the same ``.dat`` files on demand from the
GitHub CDN and injects a synthetic ``face_recognition_models`` module so that
``face_recognition`` finds its models without a manual install step.

Files are cached in ``~/.cache/pyimgtag/face_models/`` (or the directory in
``PYIMGTAG_FACE_MODEL_DIR``). Each file is fetched at most once.

The ``inject_shim`` entry-point is called by ``_face_dep_check._ensure_face_dep``
before ``face_recognition`` is imported for the first time.
"""

from __future__ import annotations

import os
import sys
import types
import urllib.request
import warnings
from pathlib import Path

# Canonical source: GitHub CDN for git-LFS blobs in ageitgey/face_recognition_models
_BASE = (
    "https://media.githubusercontent.com/media/ageitgey/face_recognition_models"
    "/master/face_recognition_models/models"
)

# (filename, approx_bytes) — sizes shown in download warnings so users know
# how long to wait.
_MODEL_FILES: list[tuple[str, int]] = [
    ("dlib_face_recognition_resnet_model_v1.dat", 22_272_889),
    ("shape_predictor_68_face_landmarks.dat", 99_693_738),
    ("shape_predictor_5_face_landmarks.dat", 6_843_592),
    ("mmod_human_face_detector.dat", 712_291),
]

_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "pyimgtag" / "face_models"


def _cache_dir() -> Path:
    env = os.environ.get("PYIMGTAG_FACE_MODEL_DIR")
    return Path(env) if env else _DEFAULT_CACHE_DIR


def _download(name: str, approx_bytes: int, dest: Path) -> None:
    mb = approx_bytes / 1_048_576
    warnings.warn(
        f"pyimgtag: downloading face model {name} (~{mb:.0f} MB) to {dest}",
        stacklevel=5,
    )
    url = f"{_BASE}/{name}"
    tmp = dest.with_suffix(".tmp")
    try:
        urllib.request.urlretrieve(url, tmp)  # nosec B310 — _BASE is a hardcoded https:// URL
        tmp.rename(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def ensure_models_cached(cache_dir: Path | None = None) -> dict[str, Path]:
    """Return name→Path for each model file, downloading any that are absent."""
    d = cache_dir if cache_dir is not None else _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, size in _MODEL_FILES:
        dest = d / name
        if not dest.exists():
            _download(name, size, dest)
        paths[name] = dest
    return paths


def inject_shim(cache_dir: Path | None = None) -> None:
    """Inject a ``face_recognition_models`` shim backed by cached model files.

    If the real package is already importable and functional, this is a no-op.
    Otherwise model files are downloaded (once) and a lightweight shim that
    implements the four location functions is installed into ``sys.modules``.
    """
    try:
        import face_recognition_models as _frm

        _frm.face_recognition_model_location()
        return  # real package present and working — nothing to do
    except Exception:
        pass

    paths = ensure_models_cached(cache_dir)

    shim = types.ModuleType("face_recognition_models")
    shim.face_recognition_model_location = lambda: str(  # type: ignore[attr-defined]
        paths["dlib_face_recognition_resnet_model_v1.dat"]
    )
    shim.pose_predictor_model_location = lambda: str(  # type: ignore[attr-defined]
        paths["shape_predictor_68_face_landmarks.dat"]
    )
    shim.pose_predictor_five_point_model_location = lambda: str(  # type: ignore[attr-defined]
        paths["shape_predictor_5_face_landmarks.dat"]
    )
    shim.cnn_face_detector_model_location = lambda: str(  # type: ignore[attr-defined]
        paths["mmod_human_face_detector.dat"]
    )
    sys.modules["face_recognition_models"] = shim
