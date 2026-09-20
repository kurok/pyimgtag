"""Output writers for JSON, CSV, and JSONL."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
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
    # Video rows carry these; a still exports "image" and an empty duration,
    # so the column set stays the same shape for every row.
    "media_type",
    "duration_sec",
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


def write_rows_csv(rows: list[dict], output_path: str | Path, fields: Sequence[str]) -> None:
    """Write dict rows as CSV using *fields* as the column order.

    The row-based twin of :func:`write_csv`. ``pyimgtag export`` carries
    columns an :class:`~pyimgtag.models.ImageResult` has no field for -- the
    judge score and the event a photo belongs to both come from their own
    tables -- so it cannot go through the dataclass writers. Keeping it here
    means both paths still agree on what a pyimgtag CSV looks like: same
    ``';'``-joined tags, same quoting, same newline handling.

    Raises:
        OSError: If writing the file fails.
    """
    try:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                flat = dict(row)
                value = flat.get("tags")
                if isinstance(value, (list, tuple)):
                    flat["tags"] = ";".join(str(v) for v in value)
                writer.writerow(flat)
    except OSError as e:
        raise OSError(f"Failed to write CSV to {output_path}: {e}") from e


def write_rows_json(rows: list[dict], output_path: str | Path) -> None:
    """Write dict rows as a pretty-printed JSON array.

    Tags stay a list here, unlike the CSV: JSON can represent one.

    Raises:
        OSError: If writing the file fails.
    """
    try:
        Path(output_path).write_text(
            json.dumps(rows, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except OSError as e:
        raise OSError(f"Failed to write JSON to {output_path}: {e}") from e


def result_to_jsonl(result: ImageResult) -> str:
    """Serialize one ImageResult as a single JSON line (no trailing newline; caller adds it)."""
    return json.dumps(asdict(result), ensure_ascii=False, default=str)
