"""Output writers for JSON, CSV, and JSONL."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from pyimgtag.models import ImageResult

_CSV_FIELDS = [
    "file_path",
    "file_name",
    "source_type",
    "is_local",
    "image_date",
    "tags",
    "scene_summary",
    "gps_lat",
    "gps_lon",
    "nearest_place",
    "nearest_city",
    "nearest_region",
    "nearest_country",
    "processing_status",
    "error_message",
    "phash",
    "scene_category",
    "emotional_tone",
    "cleanup_class",
    "has_text",
    "text_summary",
    "event_hint",
    "significance",
]


def write_json(results: list[ImageResult], output_path: str | Path) -> None:
    """Write results as a pretty-printed JSON array to output_path.

    Raises:
        OSError: If writing the file fails.
    """
    try:
        Path(output_path).write_text(
            json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except OSError as e:
        raise OSError(f"Failed to write JSON to {output_path}: {e}") from e


def write_csv(results: list[ImageResult], output_path: str | Path) -> None:
    """Write results as CSV to output_path using only the fixed _CSV_FIELDS columns.

    Tags are serialized as a ';'-joined string.

    Raises:
        OSError: If writing the file fails.
    """
    try:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            writer.writeheader()
            for r in results:
                row = asdict(r)
                row["tags"] = ";".join(row.get("tags") or [])
                writer.writerow(row)
    except OSError as e:
        raise OSError(f"Failed to write CSV to {output_path}: {e}") from e


def result_to_jsonl(result: ImageResult) -> str:
    """Serialize one ImageResult as a single JSON line (no trailing newline; caller adds it)."""
    return json.dumps(asdict(result), ensure_ascii=False, default=str)
