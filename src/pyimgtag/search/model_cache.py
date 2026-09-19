"""Download and cache the CLIP ONNX model files used by semantic search.

Mirrors the :mod:`pyimgtag._face_model_cache` pattern: files land in
``~/.cache/pyimgtag/search_models/`` (or ``PYIMGTAG_SEARCH_MODEL_DIR``), each is
fetched at most once, and a failed download explains how to place the files by
hand — which is also how an air-gapped install works.

Unlike the face models these are checksum-verified. They are pinned to one
immutable revision of one Hugging Face repository, so a mismatch means the
bytes are not what this code was written against.
"""

from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class ModelDownloadError(RuntimeError):
    """A model file could not be fetched or did not match its checksum.

    Carries *reason* and *command* as separate fields rather than one blob of
    text. The web UI shows them to a browser, and building that reply out of
    named parts keeps exception text from flowing into a response by accident.
    """

    def __init__(self, reason: str, command: str | None = None) -> None:
        """Record a human-readable *reason* and, when there is one, a fix *command*."""
        super().__init__(f"{reason}\n{command}" if command else reason)
        self.reason = reason
        self.command = command


@dataclass(frozen=True)
class ModelFile:
    """One cached file: where it comes from and what it must hash to."""

    name: str
    sha256: str
    size: int

    @property
    def mb(self) -> float:
        """Approximate size in MiB, for progress messages."""
        return self.size / 1_048_576


#: Immutable revision of ``Xenova/clip-vit-base-patch32``. Pinned rather than
#: ``main`` so the checksums below stay meaningful.
MODEL_REVISION = "d15189d7028b43f1d3e65039190477f6af591c2a"
_BASE = f"https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/{MODEL_REVISION}"

#: The int8-quantized exports: 153 MB for both towers against 605 MB for the
#: float32 build, and CPU inference is the target. Checksums were taken from
#: the pinned revision.
VISION_MODEL = ModelFile(
    "vision_model_quantized.onnx",
    "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299",
    89_117_001,
)
TEXT_MODEL = ModelFile(
    "text_model_quantized.onnx",
    "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a",
    64_504_507,
)
TOKENIZER = ModelFile(
    "tokenizer.json",
    "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd",
    2_224_119,
)

#: Remote sub-paths; the ONNX exports live under ``onnx/`` upstream but are
#: cached flat.
_REMOTE_SUBDIR = {VISION_MODEL.name: "onnx", TEXT_MODEL.name: "onnx"}

_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "pyimgtag" / "search_models"

_MANUAL_REASON = (
    "Could not download {name}: {error}. Place the file yourself to use "
    "pyimgtag search offline, or point PYIMGTAG_SEARCH_MODEL_DIR at a "
    "directory that already has it."
)
_MANUAL_COMMAND = "mkdir -p {cache} && curl -L -o {dest} {url}"


def cache_dir() -> Path:
    """Directory holding the cached model files."""
    env = os.environ.get("PYIMGTAG_SEARCH_MODEL_DIR")
    return Path(env) if env else _DEFAULT_CACHE_DIR


def _url(model: ModelFile) -> str:
    sub = _REMOTE_SUBDIR.get(model.name)
    return f"{_BASE}/{sub}/{model.name}" if sub else f"{_BASE}/{model.name}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(model: ModelFile, dest: Path, *, progress: bool = True) -> None:
    url = _url(model)
    if progress:
        print(f"pyimgtag: downloading {model.name} (~{model.mb:.0f} MB) to {dest}")
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        # nosec B310 — _BASE is a hardcoded https:// URL with a pinned revision.
        urllib.request.urlretrieve(url, tmp)  # nosec B310
    except (urllib.error.URLError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise ModelDownloadError(
            _MANUAL_REASON.format(name=model.name, error=exc),
            _MANUAL_COMMAND.format(cache=dest.parent, dest=dest, url=url),
        ) from exc

    actual = _sha256(tmp)
    if actual != model.sha256:
        tmp.unlink(missing_ok=True)
        raise ModelDownloadError(
            f"Checksum mismatch for {model.name}: expected {model.sha256}, got {actual}. "
            f"The download was truncated or the file is not the pinned revision "
            f"({MODEL_REVISION})."
        )
    tmp.rename(dest)


def ensure_file(model: ModelFile, directory: Path | None = None, *, progress: bool = True) -> Path:
    """Return the cached path for *model*, downloading and verifying if absent.

    A file that is already present is trusted without re-hashing: hashing 89 MB
    on every search would cost more than the search. It is verified once, at
    download time, and a file placed by hand is the user's own.
    """
    d = directory if directory is not None else cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / model.name
    if not dest.exists():
        _download(model, dest, progress=progress)
    return dest


def ensure_models(directory: Path | None = None, *, progress: bool = True) -> dict[str, Path]:
    """Cache every file semantic search needs; returns name→path."""
    return {
        m.name: ensure_file(m, directory, progress=progress)
        for m in (VISION_MODEL, TEXT_MODEL, TOKENIZER)
    }
