"""Face pipeline subpackage.

Groups the face-recognition flow — detection, embedding, clustering,
naming (reference matching and screenshot OCR), thumbnail rendering, and
the Apple Photos person importer — under one namespace. The dependency
preflight stays at :mod:`pyimgtag._face_dep_check`.
"""

from __future__ import annotations

from pyimgtag.face.clustering import cluster_faces, recluster_auto
from pyimgtag.face.detection import detect_faces
from pyimgtag.face.embedding import compute_embeddings, detect_and_encode, scan_and_store
from pyimgtag.face.naming import (
    NameMatch,
    apply_matches,
    load_reference_embeddings,
    match_clusters_to_references,
)
from pyimgtag.face.ocr import (
    OcrText,
    OcrUnavailableError,
    build_references_from_screenshot,
    capture_people_screenshot,
    pair_faces_with_names,
    recognize_text,
)
from pyimgtag.face.photos_importer import import_photos_persons
from pyimgtag.face.thumb import face_thumbnail_b64

__all__ = [
    "NameMatch",
    "OcrText",
    "OcrUnavailableError",
    "apply_matches",
    "build_references_from_screenshot",
    "capture_people_screenshot",
    "cluster_faces",
    "compute_embeddings",
    "detect_and_encode",
    "detect_faces",
    "face_thumbnail_b64",
    "import_photos_persons",
    "load_reference_embeddings",
    "match_clusters_to_references",
    "pair_faces_with_names",
    "recluster_auto",
    "recognize_text",
    "scan_and_store",
]
