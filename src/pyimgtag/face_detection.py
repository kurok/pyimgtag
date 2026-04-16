"""Face detection pipeline using face_recognition (dlib)."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from pyimgtag.heic_converter import convert_heic_to_jpeg, is_heic
from pyimgtag.models import FaceDetection

logger = logging.getLogger(__name__)

_DEFAULT_MAX_DIM = 1280


def _check_face_recognition() -> None:
    """Raise ImportError with a helpful message if face_recognition is missing."""
    try:
        import face_recognition  # noqa: F401
    except ImportError:
        raise ImportError(
            "face_recognition is not installed. "
            "Install the [face] extra: pip install pyimgtag[face]"
        ) from None


def _load_and_resize(image_path: Path, max_dim: int) -> Image.Image:
    """Load an image, converting HEIC if needed, and resize to fit max_dim."""
    path = image_path
    if is_heic(image_path):
        path = convert_heic_to_jpeg(image_path)

    img = Image.open(path)
    img.load()

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")  # type: ignore[assignment]

    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)  # type: ignore[assignment]

    return img


def detect_faces(
    image_path: str | Path,
    max_dim: int = _DEFAULT_MAX_DIM,
    model: str = "hog",
) -> list[FaceDetection]:
    """Detect faces in an image and return bounding boxes.

    Args:
        image_path: Path to the image file.
        max_dim: Maximum image dimension (longest side) before detection.
            Smaller values are faster but may miss small faces.
        model: Detection model — "hog" (fast, CPU) or "cnn" (accurate, GPU).

    Returns:
        List of FaceDetection objects with bounding box coordinates
        in the coordinate space of the resized image.

    Raises:
        ImportError: If face_recognition is not installed.
        FileNotFoundError: If the image file does not exist.
    """
    _check_face_recognition()
    import face_recognition
    import numpy as np

    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {path}")

    img = _load_and_resize(path, max_dim)
    img_array = np.array(img)

    # face_recognition returns (top, right, bottom, left) tuples
    locations = face_recognition.face_locations(img_array, model=model)

    results: list[FaceDetection] = []
    for top, right, bottom, left in locations:
        results.append(
            FaceDetection(
                image_path=str(path),
                bbox_x=left,
                bbox_y=top,
                bbox_w=right - left,
                bbox_h=bottom - top,
                confidence=1.0,
            )
        )

    return results
