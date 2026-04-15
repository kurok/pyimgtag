"""Data models for pyimgtag."""

from __future__ import annotations

from dataclasses import dataclass, field


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
