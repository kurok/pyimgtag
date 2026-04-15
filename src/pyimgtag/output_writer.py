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
]


def write_json(results: list[ImageResult], output_path: str | Path) -> None:
    Path(output_path).write_text(
        json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False, default=str)
    )


def write_csv(results: list[ImageResult], output_path: str | Path) -> None:
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for r in results:
            row = asdict(r)
            row["tags"] = ";".join(row.get("tags") or [])
            writer.writerow(row)


def result_to_jsonl(result: ImageResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, default=str)
