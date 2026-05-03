"""Face embedding pipeline — compute and store 128-d face encodings."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pyimgtag._face_dep_check import _ensure_face_dep
from pyimgtag.face_detection import _load_and_resize, detect_faces
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
) -> list[np.ndarray]:
    """Compute 128-d face encodings for known face locations.

    Args:
        image_path: Path to the image file.
        faces: Face detections with bounding boxes (from detect_faces).
        max_dim: Must match the max_dim used during detection so that
            bounding box coordinates align with the image.

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

    encodings = face_recognition.face_encodings(img_array, known_face_locations=known_locations)
    return list(encodings)


def scan_and_store(
    image_path: str | Path,
    db: ProgressDB,
    max_dim: int = _DEFAULT_MAX_DIM,
    model: str = "hog",
) -> int:
    """Detect faces, compute embeddings, and store everything in the DB.

    Skips images that already have faces recorded in the database.

    Args:
        image_path: Path to the image file.
        db: ProgressDB instance with face tables.
        max_dim: Max image dimension for detection and embedding.
        model: Detection model — "hog" or "cnn".

    Returns:
        Number of faces detected and stored for this image.
    """
    path_str = str(Path(image_path))

    if db.get_faces_for_image(path_str):
        return 0

    faces = detect_faces(image_path, max_dim=max_dim, model=model)
    if not faces:
        return 0

    embeddings = compute_embeddings(image_path, faces, max_dim=max_dim)

    for i, detection in enumerate(faces):
        embedding = embeddings[i] if i < len(embeddings) else None
        db.insert_face(path_str, detection, embedding=embedding)

    return len(faces)
