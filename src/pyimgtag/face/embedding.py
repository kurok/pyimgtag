"""Face embedding pipeline — compute and store 128-d face encodings."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pyimgtag._face_dep_check import _ensure_face_dep
from pyimgtag.face.detection import _load_and_resize, detect_faces
from pyimgtag.models import FaceDetection

if TYPE_CHECKING:
    import numpy as np

    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)

_DEFAULT_MAX_DIM = 1280


def compute_embeddings(
    image_path: str | Path,
    faces: list[FaceDetection],
    max_dim: int = _DEFAULT_MAX_DIM,
    num_jitters: int = 1,
) -> list[np.ndarray]:
    """Compute 128-d face encodings for known face locations.

    Args:
        image_path: Path to the image file.
        faces: Face detections with bounding boxes (from detect_faces).
        max_dim: Must match the max_dim used during detection so that
            bounding box coordinates align with the image.
        num_jitters: How many times to re-sample each face when computing its
            encoding (dlib's ``num_jitters``). Higher values yield more robust
            encodings — improving clustering/matching — at roughly linear extra
            cost per face.

    Returns:
        List of 128-d numpy float64 arrays, one per input face.
        The list may be shorter than ``faces`` if encoding fails for some.

    Raises:
        MissingFaceModelsError: If ``face_recognition_models`` is missing.
        ImportError: If face_recognition is not installed.
        FileNotFoundError: If the image file does not exist.
    """
    if not faces:
        return []

    face_recognition = _ensure_face_dep()
    import numpy as np

    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {path}")

    img = _load_and_resize(path, max_dim)
    img_array = np.array(img)

    # Convert bbox (x, y, w, h) back to face_recognition's (top, right, bottom, left)
    known_locations = [
        (f.bbox_y, f.bbox_x + f.bbox_w, f.bbox_y + f.bbox_h, f.bbox_x) for f in faces
    ]

    encodings = face_recognition.face_encodings(
        img_array, known_face_locations=known_locations, num_jitters=num_jitters
    )
    return list(encodings)


def scan_and_store(
    image_path: str | Path,
    db: ProgressDB,
    max_dim: int = _DEFAULT_MAX_DIM,
    model: str = "hog",
    upsample: int = 1,
    num_jitters: int = 1,
    min_face_size: int = 0,
) -> int:
    """Detect faces, compute embeddings, and store everything in the DB.

    Skips images already marked as scanned in a previous run, even if no
    faces were found.

    Args:
        image_path: Path to the image file.
        db: ProgressDB instance with face tables.
        max_dim: Max image dimension for detection and embedding.
        model: Detection model — "hog" or "cnn".
        upsample: dlib upsample passes for detection (finds smaller faces).
        num_jitters: dlib re-samples per face when encoding (better encodings).
        min_face_size: Drop detections smaller than this (shorter side, px).

    Returns:
        Number of faces detected and stored for this image.
    """
    path_str = str(Path(image_path))

    # Skip images that were already scanned in a previous run (regardless of
    # whether any faces were found — this is what allows incremental resumption).
    if db.is_face_scanned(path_str):
        return 0

    results = detect_and_encode(
        image_path,
        max_dim=max_dim,
        model=model,
        upsample=upsample,
        num_jitters=num_jitters,
        min_face_size=min_face_size,
    )
    for detection, embedding in results:
        db.insert_face(path_str, detection, embedding=embedding)

    # Always mark as scanned so zero-face images are never re-processed.
    db.mark_face_scanned(path_str)
    return len(results)


def detect_and_encode(
    image_path: str | Path,
    *,
    max_dim: int = _DEFAULT_MAX_DIM,
    model: str = "hog",
    upsample: int = 1,
    num_jitters: int = 1,
    min_face_size: int = 0,
) -> list[tuple[FaceDetection, "np.ndarray | None"]]:
    """Detect faces and compute embeddings for one image **without** any DB access.

    Pure CPU work over a single image: it takes a path + settings and returns a
    picklable list of ``(FaceDetection, embedding-or-None)`` pairs. That makes it
    safe to run in a worker process for a parallel scan — the caller (main
    process) does the SQLite writes. :func:`scan_and_store` wraps this with the
    already-scanned skip and the inserts for the serial path.
    """
    faces = detect_faces(
        image_path,
        max_dim=max_dim,
        model=model,
        upsample=upsample,
        min_face_size=min_face_size,
    )
    if not faces:
        return []
    embeddings = compute_embeddings(image_path, faces, max_dim=max_dim, num_jitters=num_jitters)
    if len(embeddings) != len(faces):
        # Positional alignment between faces[i] and embeddings[i] only holds when
        # the counts match; a short result means some faces have no embedding.
        logger.warning(
            "embedding count %d != face count %d for %s; "
            "faces beyond the encoded count are stored without embeddings",
            len(embeddings),
            len(faces),
            str(Path(image_path)),
        )
    return [(faces[i], embeddings[i] if i < len(embeddings) else None) for i in range(len(faces))]
