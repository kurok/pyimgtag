"""Face region crop and base64 thumbnail encoding."""

from __future__ import annotations

import base64
import contextlib
import io
import logging
import math

from PIL import Image

logger = logging.getLogger(__name__)

# face.detection resizes images to this max dimension before detecting faces,
# so all stored bbox coords are in that coordinate space.
_DETECT_MAX_DIM = 1280


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

    converted = None
    try:
        from pyimgtag.heic_converter import convert_heic_to_jpeg, is_heic

        if is_heic(image_path):
            # convert_heic_to_jpeg with no output_dir hands us a JPEG inside a
            # fresh temp dir that *we* own — clean it up once pixels are read.
            converted = convert_heic_to_jpeg(image_path)
            src_path = str(converted)
        else:
            src_path = image_path

        with Image.open(src_path) as src:
            iw, ih = src.size

            # bbox coords are in detection space (max_dim=1280); scale to full image.
            if max(iw, ih) > _DETECT_MAX_DIM:
                det_scale = _DETECT_MAX_DIM / max(iw, ih)
                rw = int(iw * det_scale)
                inv = iw / rw
                bbox_x = round(bbox_x * inv)
                bbox_y = round(bbox_y * inv)
                bbox_w = round(bbox_w * inv)
                bbox_h = round(bbox_h * inv)

            pad_x = math.ceil(bbox_w * padding)
            pad_y = math.ceil(bbox_h * padding)

            left = max(0, bbox_x - pad_x)
            top = max(0, bbox_y - pad_y)
            right = min(iw, bbox_x + bbox_w + pad_x)
            bottom = min(ih, bbox_y + bbox_h + pad_y)

            if right <= left or bottom <= top:
                return None

            cropped = src.crop((left, top, right, bottom)).convert("RGB")
    except Exception as exc:  # noqa: BLE001 — thumbnail rendering must never crash the faces UI
        logger.debug("face thumbnail failed for %s: %s", image_path, exc)
        return None
    finally:
        if converted is not None:
            converted.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                converted.parent.rmdir()  # removes the owned mkdtemp dir only when empty

    thumb = cropped.resize((size, size), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    thumb.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii")
