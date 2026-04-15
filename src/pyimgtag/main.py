"""CLI entry point and orchestration for pyimgtag."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pyimgtag import __version__
from pyimgtag.exif_reader import read_exif
from pyimgtag.filters import passes_date_filter
from pyimgtag.geocoder import ReverseGeocoder
from pyimgtag.models import ExifData, ImageResult
from pyimgtag.ollama_client import OllamaClient
from pyimgtag.output_writer import result_to_jsonl, write_csv, write_json
from pyimgtag.scanner import scan_directory, scan_photos_library

# ------------------------------------------------------------------
# CLI argument parser
# ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyimgtag",
        description="Tag images using a local Ollama Gemma vision model "
        "with EXIF GPS reverse geocoding.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input-dir", help="Path to an exported image folder")
    src.add_argument("--photos-library", help="Path to an Apple Photos library package")

    p.add_argument("--model", default="gemma4:e4b", help="Ollama model (default: gemma4:e4b)")
    p.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama base URL")
    p.add_argument(
        "--max-dim",
        type=int,
        default=1280,
        help="Max image dimension sent to model (default: 1280)",
    )
    p.add_argument("--timeout", type=int, default=120, help="Model request timeout in seconds")

    p.add_argument("--limit", type=int, help="Max images to process")
    p.add_argument("--date", help="Process only this date (YYYY-MM-DD)")
    p.add_argument("--date-from", help="Process images from this date (YYYY-MM-DD)")
    p.add_argument("--date-to", help="Process images up to this date (YYYY-MM-DD)")
    p.add_argument("--extensions", default="jpg,jpeg,heic,png", help="Comma-separated extensions")
    p.add_argument("--skip-no-gps", action="store_true", help="Skip images without GPS data")

    p.add_argument("--dry-run", action="store_true", help="Read-only mode, print results only")
    p.add_argument("--output-json", help="Write results to a JSON file")
    p.add_argument("--output-csv", help="Write results to a CSV file")
    p.add_argument("--jsonl-stdout", action="store_true", help="JSONL output to stdout")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose per-file output")
    p.add_argument("--cache-dir", help="Geocoding cache directory")

    return p


# ------------------------------------------------------------------
# main entry point
# ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    extensions = {e.strip().lower() for e in args.extensions.split(",")}

    # --- scan ---
    try:
        if args.input_dir:
            source_type = "directory"
            files = scan_directory(args.input_dir, extensions)
        else:
            source_type = "photos_library"
            files = scan_photos_library(args.photos_library, extensions)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not files:
        print("No image files found.", file=sys.stderr)
        return 0

    # --- init services ---
    ollama = OllamaClient(
        model=args.model, base_url=args.ollama_url, max_dim=args.max_dim, timeout=args.timeout
    )
    geocoder = ReverseGeocoder(cache_dir=args.cache_dir)

    results: list[ImageResult] = []
    stats = _new_stats(len(files))

    # --- process ---
    try:
        for file_path in files:
            if args.limit and stats["processed"] >= args.limit:
                break

            result = _process_one(file_path, source_type, args, ollama, geocoder, stats)
            if result is None:
                continue

            results.append(result)
            stats["processed"] += 1

            if args.jsonl_stdout:
                print(result_to_jsonl(result))
            elif args.verbose or args.dry_run:
                _print_verbose(result, stats["processed"], args.limit or stats["scanned"])
            else:
                _print_brief(result, stats["processed"], args.limit or stats["scanned"])

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
    finally:
        ollama.close()
        geocoder.close()

    # --- output files ---
    if args.output_json:
        write_json(results, args.output_json)
        print(f"Wrote {len(results)} results to {args.output_json}", file=sys.stderr)
    if args.output_csv:
        write_csv(results, args.output_csv)
        print(f"Wrote {len(results)} results to {args.output_csv}", file=sys.stderr)

    # --- summary ---
    _print_summary(stats)
    return 0


# ------------------------------------------------------------------
# per-file processing
# ------------------------------------------------------------------


def _process_one(
    file_path: Path,
    source_type: str,
    args: argparse.Namespace,
    ollama: OllamaClient,
    geocoder: ReverseGeocoder,
    stats: dict,
) -> ImageResult | None:
    """Process one image.  Returns ``None`` when filtered out."""
    result = ImageResult(
        file_path=str(file_path), file_name=file_path.name, source_type=source_type
    )

    # local availability
    try:
        if not file_path.exists() or file_path.stat().st_size == 0:
            stats["skipped_no_local"] += 1
            return None
    except OSError:
        stats["skipped_no_local"] += 1
        return None

    # EXIF
    try:
        exif = read_exif(file_path)
    except Exception:
        exif = ExifData()
    result.image_date = exif.date_original
    result.gps_lat = exif.gps_lat
    result.gps_lon = exif.gps_lon

    # date filter
    if not passes_date_filter(exif, file_path, args.date, args.date_from, args.date_to):
        stats["skipped_date"] += 1
        return None

    # GPS filter
    if args.skip_no_gps and not exif.has_gps:
        stats["skipped_no_gps"] += 1
        return None

    # --- tag with model ---
    tag_result = ollama.tag_image(str(file_path))
    if tag_result.error:
        result.processing_status = "error"
        result.error_message = tag_result.error
        stats["model_failures"] += 1
    else:
        result.tags = tag_result.tags
        result.scene_summary = tag_result.summary

    # --- reverse geocode ---
    if exif.has_gps:
        geo = geocoder.resolve(exif.gps_lat, exif.gps_lon)
        if geo.error:
            stats["geocode_failures"] += 1
        else:
            result.nearest_place = geo.nearest_place
            result.nearest_city = geo.nearest_city
            result.nearest_region = geo.nearest_region
            result.nearest_country = geo.nearest_country

    return result


# ------------------------------------------------------------------
# output helpers
# ------------------------------------------------------------------


def _new_stats(scanned: int) -> dict:
    return {
        "scanned": scanned,
        "processed": 0,
        "skipped_date": 0,
        "skipped_no_gps": 0,
        "skipped_no_local": 0,
        "model_failures": 0,
        "geocode_failures": 0,
    }


def _print_brief(result: ImageResult, idx: int, total: int) -> None:
    tags = ", ".join(result.tags) if result.tags else "(none)"
    loc = result.nearest_city or result.nearest_place or ""
    if result.nearest_country and loc:
        loc = f"{loc}, {result.nearest_country}"
    status = result.processing_status
    if result.error_message:
        status = f"error: {result.error_message[:60]}"
    parts = [f"[{idx}/{total}]", result.file_name, "->", tags]
    if loc:
        parts.append(f"| {loc}")
    parts.append(f"| {status}")
    print(" ".join(parts))


def _print_verbose(result: ImageResult, idx: int, total: int) -> None:
    print(f"[{idx}/{total}] {result.file_name}")
    print(f"  Path:     {result.file_path}")
    print(f"  Date:     {result.image_date or '(unknown)'}")
    tags = ", ".join(result.tags) if result.tags else "(none)"
    print(f"  Tags:     {tags}")
    if result.scene_summary:
        print(f"  Summary:  {result.scene_summary}")
    if result.gps_lat is not None:
        print(f"  GPS:      {result.gps_lat}, {result.gps_lon}")
    else:
        print("  GPS:      (none)")
    loc_parts = [
        p
        for p in [
            result.nearest_place,
            result.nearest_city,
            result.nearest_region,
            result.nearest_country,
        ]
        if p
    ]
    print(f"  Location: {', '.join(loc_parts) if loc_parts else '(none)'}")
    print(f"  Status:   {result.processing_status}")
    if result.error_message:
        print(f"  Error:    {result.error_message}")
    print()


def _print_summary(stats: dict) -> None:
    print("\n--- Summary ---", file=sys.stderr)
    print(f"  Scanned:          {stats['scanned']}", file=sys.stderr)
    print(f"  Processed:        {stats['processed']}", file=sys.stderr)
    print(f"  Skipped (date):   {stats['skipped_date']}", file=sys.stderr)
    print(f"  Skipped (no GPS): {stats['skipped_no_gps']}", file=sys.stderr)
    print(f"  Skipped (no file):{stats['skipped_no_local']}", file=sys.stderr)
    print(f"  Model failures:   {stats['model_failures']}", file=sys.stderr)
    print(f"  Geocode failures: {stats['geocode_failures']}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
