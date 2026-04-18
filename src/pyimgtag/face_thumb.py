"""Face region crop and base64 thumbnail encoding."""

from __future__ import annotations

import base64
import io
import math

from PIL import Image


def face_thumbnail_b64(
    image_path: str,
    bbox_x: int,
    bbox_y: int,
    bbox_w: int,
    bbox_h: int,
    size: int = 120,
    padding: float = 0.3,
) -> str | None:
    """Crop a face region and return it as a base64-encoded JPEG string.

    Args:
        image_path: Full path to the source image.
        bbox_x: Left edge of detected face bounding box (pixels).
        bbox_y: Top edge of detected face bounding box (pixels).
        bbox_w: Width of bounding box.
        bbox_h: Height of bounding box.
        size: Output thumbnail size in pixels (square).
        padding: Fractional padding around the bounding box (0.3 = 30%).

    Returns:
        Base64-encoded JPEG string, or None on any error.
    """
    if bbox_w <= 0 or bbox_h <= 0:
        return None

    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:
        return None

    iw, ih = img.size
    pad_x = math.ceil(bbox_w * padding)
    pad_y = math.ceil(bbox_h * padding)

    left = max(0, bbox_x - pad_x)
    top = max(0, bbox_y - pad_y)
    right = min(iw, bbox_x + bbox_w + pad_x)
    bottom = min(ih, bbox_y + bbox_h + pad_y)

    if right <= left or bottom <= top:
        return None

    cropped = img.crop((left, top, right, bottom))
    thumb = cropped.resize((size, size), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    thumb.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii")
