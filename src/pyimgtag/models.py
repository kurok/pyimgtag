"""Data models for pyimgtag."""

from __future__ import annotations

from dataclasses import dataclass, field

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
            try:
                from datetime import datetime

                dt = datetime.strptime(self.image_date[:10], "%Y-%m-%d")
                parts.append(dt.strftime("%B %Y") + ".")
            except (ValueError, TypeError):
                pass

        return " ".join(parts)
