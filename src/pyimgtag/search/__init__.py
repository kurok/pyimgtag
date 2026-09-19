"""Local semantic search: CLIP-family embeddings over the photo library.

Everything here runs on-device. The model files are downloaded once and cached;
query text is embedded locally and never leaves the machine.

The public surface is deliberately small:

- :class:`~pyimgtag.search.embedder.Embedder` — the protocol indexing and
  searching depend on, so tests can substitute a deterministic stub instead of
  downloading 153 MB of ONNX.
- :func:`~pyimgtag.search.embedder.load_embedder` — the real ONNX-backed
  implementation.
- :func:`~pyimgtag.search.indexer.build_index` — incremental embedding.
"""

from __future__ import annotations

from pyimgtag.search.embedder import DEFAULT_MODEL_ID, Embedder, load_embedder
from pyimgtag.search.indexer import IndexStats, build_index

__all__ = [
    "DEFAULT_MODEL_ID",
    "Embedder",
    "IndexStats",
    "build_index",
    "load_embedder",
]
