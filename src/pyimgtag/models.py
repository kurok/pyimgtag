"""Data models for pyimgtag."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

DEFAULT_MAX_TAGS = 5


def normalize_tags(
    tags: list[str],
    max_tags: int = DEFAULT_MAX_TAGS,
) -> list[str]:
    """Normalize a list of tags: lowercase, strip whitespace, deduplicate, cap count.

    Args:
        tags: Raw tag strings from model output.
        max_tags: Maximum number of tags to return.

    Returns:
        Cleaned, deduplicated, capped list preserving original order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for t in tags:
        if not t:
            continue
        cleaned = str(t).lower().strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= max_tags:
            break
    return result


@dataclass
class ExifData:
    """EXIF metadata extracted from an image."""

    gps_lat: float | None = None
    gps_lon: float | None = None
    date_original: str | None = None
    has_gps: bool = False


@dataclass
class TagResult:
    """Result from Ollama vision model."""

    tags: list[str] = field(default_factory=list)
    summary: str | None = None
    raw_response: str | None = None
    error: str | None = None
    scene_category: str | None = None
    emotional_tone: str | None = None
    cleanup_class: str | None = None
    has_text: bool = False
    text_summary: str | None = None
    event_hint: str | None = None
    significance: str | None = None


@dataclass
class GeoResult:
    """Result from reverse geocoding."""

    nearest_place: str | None = None
    nearest_city: str | None = None
    nearest_region: str | None = None
    nearest_country: str | None = None
    error: str | None = None


@dataclass
class ImageResult:
    """Complete result for one processed image."""

    file_path: str = ""
    file_name: str = ""
    source_type: str = ""
    is_local: bool = True
    image_date: str | None = None
    tags: list[str] = field(default_factory=list)
    scene_summary: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    nearest_place: str | None = None
    nearest_city: str | None = None
    nearest_region: str | None = None
    nearest_country: str | None = None
    processing_status: str = "ok"
    error_message: str | None = None
    phash: str | None = None
    scene_category: str | None = None
    emotional_tone: str | None = None
    cleanup_class: str | None = None
    has_text: bool = False
    text_summary: str | None = None
    event_hint: str | None = None
    significance: str | None = None

    def build_description(self) -> str | None:
        """Build a human-readable description from summary, location, and date.

        Example: "Golden hour sunset over the Pacific. San Francisco, California, US. April 2026."
        Returns None if no summary is available.
        """
        if not self.scene_summary:
            return None

        parts = [self.scene_summary.rstrip(".") + "."]

        loc_parts = [p for p in [self.nearest_city, self.nearest_region, self.nearest_country] if p]
        if loc_parts:
            parts.append(", ".join(loc_parts) + ".")

        if self.image_date:
            with contextlib.suppress(ValueError, TypeError):
                from datetime import datetime

                dt = datetime.strptime(self.image_date[:10], "%Y-%m-%d")
                parts.append(dt.strftime("%B %Y") + ".")

        return " ".join(parts)


@dataclass
class FaceDetection:
    """A single face detected in an image."""

    image_path: str = ""
    bbox_x: int = 0
    bbox_y: int = 0
    bbox_w: int = 0
    bbox_h: int = 0
    confidence: float = 0.0


@dataclass
class FaceEmbedding:
    """128-d face encoding associated with a detected face."""

    face_id: int = 0
    image_path: str = ""
    embedding: np.ndarray | None = None


@dataclass
class PersonCluster:
    """A cluster of faces representing one person."""

    person_id: int = 0
    label: str = ""
    confirmed: bool = False
    face_ids: list[int] = field(default_factory=list)


@dataclass
class JudgeScores:
    """Rubric scores from the photo-judge prompt (1-5 each)."""

    impact: float
    story_subject: float
    composition_center: float
    lighting: float
    creativity_style: float
    color_mood: float
    presentation_crop: float
    technical_excellence: float
    focus_sharpness: float
    exposure_tonal: float
    noise_cleanliness: float
    subject_separation: float
    edit_integrity: float
    verdict: str = ""


@dataclass
class JudgeResult:
    """Complete judge output for one image."""

    file_path: str
    file_name: str
    scores: JudgeScores
    weighted_score: float
    core_score: float
    visible_score: float
