"""CLIP image/text embedding, and the protocol that lets tests avoid it.

Indexing and searching depend on :class:`Embedder`, never on ONNX directly.
That is what keeps the test suite honest about the repo's no-network rule: a
stub with fixed vectors satisfies the protocol, so ranking can be tested
deterministically without downloading 153 MB or running inference.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np
    from PIL import Image

#: Identifier stored next to every embedding. Vectors from different models are
#: not comparable, so this is what makes a model change invalidate the index.
DEFAULT_MODEL_ID = "clip-vit-base-patch32-quantized"

#: Embedding width of the pinned model.
EMBEDDING_DIM = 512

_MISSING_EXTRA = (
    "Semantic search needs the [search] extra. Install it with:\n    pip install 'pyimgtag[search]'"
)

# CLIP's own preprocessing constants, from the pinned repo's
# preprocessor_config.json. Hardcoded rather than read from the file so a
# missing download fails at the download, not halfway through indexing.
_IMAGE_SIZE = 224
_IMAGE_MEAN = (0.48145466, 0.4578275, 0.40821073)
_IMAGE_STD = (0.26862954, 0.26130258, 0.27577711)

#: CLIP truncates to 77 tokens including the BOS/EOS pair.
_MAX_TEXT_TOKENS = 77


@runtime_checkable
class Embedder(Protocol):
    """Maps images and text into one shared vector space.

    Implementations return **unit-normalised** vectors, so a dot product
    between any two is their cosine similarity and the storage layer never has
    to normalise again.
    """

    @property
    def model_id(self) -> str:
        """Identifier stored alongside each embedding."""

    @property
    def dim(self) -> int:
        """Width of the vectors this embedder produces."""

    def embed_image(self, path: Path) -> np.ndarray:
        """Embed one image file."""

    def embed_text(self, text: str) -> np.ndarray:
        """Embed one free-text query."""


def _require(module: str):  # type: ignore[no-untyped-def]
    try:
        return __import__(module)
    except ImportError as exc:  # pragma: no cover - exercised via load_embedder
        raise ImportError(_MISSING_EXTRA) from exc


class ClipOnnxEmbedder:
    """CLIP ViT-B/32 (int8 ONNX) running locally on the CPU.

    The two towers are separate sessions and are created lazily: ``index`` only
    ever needs the vision tower and ``search`` only the text tower, so neither
    pays for the other's 60-90 MB.
    """

    def __init__(
        self,
        vision_model: Path,
        text_model: Path,
        tokenizer_file: Path,
        model_id: str = DEFAULT_MODEL_ID,
    ) -> None:
        """Bind to already-cached model files (see :mod:`~pyimgtag.search.model_cache`)."""
        self._vision_path = vision_model
        self._text_path = text_model
        self._tokenizer_path = tokenizer_file
        self._model_id = model_id
        # Typed Any: the concrete classes live in the optional [search] extra,
        # so they cannot be named here without importing it at module scope.
        self._vision: Any = None
        self._text: Any = None
        self._tokenizer: Any = None

    @property
    def model_id(self) -> str:
        """Identifier stored alongside each embedding."""
        return self._model_id

    @property
    def dim(self) -> int:
        """Width of the vectors this embedder produces."""
        return EMBEDDING_DIM

    def _session(self, path: Path):  # type: ignore[no-untyped-def]
        ort = _require("onnxruntime")
        # Single-threaded by default is wrong for a batch job and right for a
        # one-shot query; onnxruntime's own default (all cores) suits both
        # better than anything guessed here.
        return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])

    @staticmethod
    def _normalise(vector: np.ndarray) -> np.ndarray:
        import numpy as np

        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else vector

    def _preprocess(self, image: Image.Image) -> np.ndarray:
        """Resize-shortest-edge, centre-crop, rescale and normalise, as CLIP expects."""
        import numpy as np
        from PIL import Image as PILImage

        image = image.convert("RGB")
        width, height = image.size
        scale = _IMAGE_SIZE / min(width, height)
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            PILImage.Resampling.BICUBIC,
        )
        width, height = image.size
        left, top = (width - _IMAGE_SIZE) // 2, (height - _IMAGE_SIZE) // 2
        image = image.crop((left, top, left + _IMAGE_SIZE, top + _IMAGE_SIZE))

        array = np.asarray(image, dtype=np.float32) / 255.0
        mean = np.array(_IMAGE_MEAN, dtype=np.float32)
        std = np.array(_IMAGE_STD, dtype=np.float32)
        normalised: np.ndarray = (array - mean) / std
        return normalised.transpose(2, 0, 1)[None, ...].astype(np.float32)

    def embed_image(self, path: Path) -> np.ndarray:
        """Embed one image file into the shared space."""
        from PIL import Image as PILImage

        if self._vision is None:
            self._vision = self._session(self._vision_path)
        with PILImage.open(path) as image:
            pixels = self._preprocess(image)
        out = self._vision.run(["image_embeds"], {"pixel_values": pixels})[0]
        return self._normalise(out[0])

    def embed_text(self, text: str) -> np.ndarray:
        """Embed one free-text query into the shared space."""
        import numpy as np

        if self._text is None:
            self._text = self._session(self._text_path)
        if self._tokenizer is None:
            tokenizers = _require("tokenizers")
            self._tokenizer = tokenizers.Tokenizer.from_file(str(self._tokenizer_path))
        ids = self._tokenizer.encode(text).ids[:_MAX_TEXT_TOKENS]
        out = self._text.run(["text_embeds"], {"input_ids": np.array([ids], dtype=np.int64)})[0]
        return self._normalise(out[0])


def load_embedder(model_dir: Path | None = None, *, progress: bool = True) -> ClipOnnxEmbedder:
    """Build the default embedder, downloading and verifying models if needed.

    Raises:
        ImportError: The ``[search]`` extra is not installed.
        pyimgtag.search.model_cache.ModelDownloadError: A model file could not
            be fetched or failed its checksum.
    """
    from pyimgtag.search.model_cache import TEXT_MODEL, TOKENIZER, VISION_MODEL, ensure_file

    # Fail on the missing extra before spending a 153 MB download on a machine
    # that cannot run the result.
    _require("onnxruntime")
    _require("tokenizers")
    return ClipOnnxEmbedder(
        ensure_file(VISION_MODEL, model_dir, progress=progress),
        ensure_file(TEXT_MODEL, model_dir, progress=progress),
        ensure_file(TOKENIZER, model_dir, progress=progress),
    )
