"""Handler for the ``run`` subcommand (image tagging workhorse)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from platform import system as get_platform_name

from pyimgtag.exif_reader import read_exif
from pyimgtag.filters import passes_date_filter
from pyimgtag.geocoder import ReverseGeocoder
from pyimgtag.models import ExifData, ImageResult
from pyimgtag.ollama_client import OllamaClient
from pyimgtag.output_writer import result_to_jsonl, write_csv, write_json
from pyimgtag.preflight import check_ollama
from pyimgtag.progress_db import ProgressDB
from pyimgtag.scanner import scan_directory, scan_photos_library

_FDA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"

_FDA_DIALOG_SCRIPT = (
    'tell application "System Events"\n'
    "    set btn to button returned of (display dialog \u00ac\n"
    '        "pyimgtag cannot read your Photos Library." & return & return & \u00ac\n'
    '        "Grant Full Disk Access to Terminal in:" & return & \u00ac\n'
    '        "System Settings \u2192 Privacy & Security \u2192 Full Disk Access" \u00ac\n'
    '        buttons {"Open System Settings", "Cancel"} \u00ac\n'
    '        default button "Open System Settings" \u00ac\n'
    "        with icon caution \u00ac\n"
    '        with title "Photos Library Access Required")\n'
    '    if btn is "Open System Settings" then\n'
    "        open location " + '"' + _FDA_SETTINGS_URL + '"\n'
    "    end if\n"
    "end tell"
)


def _request_photos_access_dialog() -> None:
    """Show a native macOS dialog offering to open Full Disk Access settings."""
    if get_platform_name() != "Darwin" or not shutil.which("osascript"):
        return
    try:
        subprocess.run(  # noqa: S603
            ["osascript", "-e", _FDA_DIALOG_SCRIPT],  # noqa: S607
            check=False,
            timeout=120,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _compute_dedup_map(files: list[Path], threshold: int) -> tuple[dict[str, str], set[str]]:
    """Build a perceptual-hash dedup map and the set of paths to skip.

    Returns a tuple of:
      - phash_map: dict mapping file path (str) to its perceptual hash
      - skipped_dedup: set of file paths that are duplicates to be skipped
    """
    from pyimgtag.dedup import compute_phash, find_duplicate_groups

    print("Computing perceptual hashes...", file=sys.stderr)
    records: list[tuple[str, str]] = []
    phash_map: dict[str, str] = {}
    for f in files:
        h = compute_phash(f)
        if h is not None:
            records.append((str(f), h))
            phash_map[str(f)] = h
    groups = find_duplicate_groups(records, threshold=threshold)
    skipped_dedup: set[str] = set()
    dup_count = 0
    for group in groups:
        for path in sorted(group)[1:]:
            skipped_dedup.add(path)
            dup_count += 1
    print(
        f"Found {len(groups)} duplicate groups ({dup_count} images skipped, keeping 1 per group)",
        file=sys.stderr,
    )
    return phash_map, skipped_dedup


def cmd_run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Execute the run subcommand (image tagging)."""
    if not args.input_dir and not args.photos_library:
        parser.error("one of the arguments --input-dir --photos-library is required")

    if args.write_back and args.input_dir:
        print("Warning: --write-back has no effect with --input-dir", file=sys.stderr)

    if args.write_back and get_platform_name() != "Darwin":
        print(
            "Warning: --write-back requires macOS; feature is disabled on this system",
            file=sys.stderr,
        )

    if (args.write_exif or args.sidecar_only) and args.dry_run:
        print(
            "Info: --write-exif/--sidecar-only disabled in --dry-run mode"
            " (use --verbose to preview proposed metadata)",
            file=sys.stderr,
        )

    extensions = {e.strip().lstrip(".").lower() for e in args.extensions.split(",")}

    ok, msg = check_ollama(args.ollama_url)
    if not ok:
        print(f"Warning: {msg}", file=sys.stderr)

    try:
        if args.input_dir:
            source_type = "directory"
            files = scan_directory(args.input_dir, extensions, recursive=not args.no_recursive)
        else:
            source_type = "photos_library"
            files = scan_photos_library(args.photos_library, extensions)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except PermissionError as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.photos_library:
            _request_photos_access_dialog()
        return 1

    if not files:
        print("No image files found.", file=sys.stderr)
        return 0

    if args.newest_first:

        def _mtime(f: Path) -> float:
            try:
                return f.stat().st_mtime
            except OSError:
                return 0.0

        files.sort(key=_mtime, reverse=True)

    # --- dedup ---
    phash_map: dict[str, str] = {}
    skipped_dedup: set[str] = set()
    if args.dedup:
        phash_map, skipped_dedup = _compute_dedup_map(files, args.dedup_threshold)

    ollama = OllamaClient(
        model=args.model, base_url=args.ollama_url, max_dim=args.max_dim, timeout=args.timeout
    )
    geocoder = ReverseGeocoder(cache_dir=args.cache_dir)
    progress_db: ProgressDB | None = None
    if not args.no_cache:
        progress_db = ProgressDB(db_path=args.db)

    results: list[ImageResult] = []
    stats = _new_stats(len(files))

    try:
        for file_path in files:
            if args.limit and stats["processed"] >= args.limit:
                break

            if str(file_path) in skipped_dedup:
                stats["skipped_dedup"] += 1
                continue

            result = _process_one(
                file_path, source_type, args, ollama, geocoder, stats, progress_db
            )
            if result is None:
                continue

            if progress_db is not None:
                progress_db.mark_done(file_path, result)

            rich_desc = result.build_description()

            if args.write_back and result.source_type == "photos_library" and result.tags:
                from pyimgtag.applescript_writer import write_to_photos

                err = write_to_photos(
                    result.file_name, result.tags, rich_desc, title=result.scene_summary
                )
                if err:
                    print(f"  Write-back failed: {err}", file=sys.stderr)

            if (args.write_exif or args.sidecar_only) and result.tags:
                if args.dry_run:
                    if args.verbose:
                        target = "sidecar" if args.sidecar_only else "file"
                        print(f"  [dry-run] Would write to {target}:", file=sys.stderr)
                        if rich_desc:
                            print(f"    description: {rich_desc[:80]}", file=sys.stderr)
                        print(f"    keywords: {', '.join(result.tags)}", file=sys.stderr)
                else:
                    _write_metadata(result, rich_desc, args)

            result.phash = phash_map.get(str(file_path))
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
        if progress_db is not None:
            progress_db.close()

    if args.output_json:
        write_json(results, args.output_json)
        print(f"Wrote {len(results)} results to {args.output_json}", file=sys.stderr)
    if args.output_csv:
        write_csv(results, args.output_csv)
        print(f"Wrote {len(results)} results to {args.output_csv}", file=sys.stderr)

    _print_summary(stats)
    return 0


def _process_one(
    file_path: Path,
    source_type: str,
    args: argparse.Namespace,
    ollama: OllamaClient,
    geocoder: ReverseGeocoder,
    stats: dict,
    progress_db: ProgressDB | None = None,
) -> ImageResult | None:
    """Process one image.  Returns ``None`` when filtered out."""
    try:
        if not file_path.exists() or file_path.stat().st_size == 0:
            stats["skipped_no_local"] += 1
            return None
    except OSError:
        stats["skipped_no_local"] += 1
        return None

    if progress_db is not None and progress_db.is_processed(file_path):
        stats["skipped_cached"] += 1
        return None

    result = ImageResult(
        file_path=str(file_path), file_name=file_path.name, source_type=source_type
    )

    try:
        exif = read_exif(file_path)
    except Exception:
        exif = ExifData()
    result.image_date = exif.date_original
    result.gps_lat = exif.gps_lat
    result.gps_lon = exif.gps_lon

    if not passes_date_filter(exif, file_path, args.date, args.date_from, args.date_to):
        stats["skipped_date"] += 1
        return None

    if args.skip_no_gps and not exif.has_gps:
        stats["skipped_no_gps"] += 1
        return None

    # --- reverse geocode (before tagging so context is available) ---
    geo = None
    if exif.has_gps:
        geo = geocoder.resolve(exif.gps_lat, exif.gps_lon)
        if geo.error:
            stats["geocode_failures"] += 1
        else:
            result.nearest_place = geo.nearest_place
            result.nearest_city = geo.nearest_city
            result.nearest_region = geo.nearest_region
            result.nearest_country = geo.nearest_country

    # --- build context for model ---
    context: dict = {}
    if exif.date_original:
        context["date"] = exif.date_original
    if exif.has_gps:
        context["lat"] = exif.gps_lat
        context["lon"] = exif.gps_lon
    if geo and not geo.error:
        if geo.nearest_city:
            context["city"] = geo.nearest_city
        if geo.nearest_region:
            context["region"] = geo.nearest_region
        if geo.nearest_country:
            context["country"] = geo.nearest_country

    # --- tag with model ---
    tag_result = ollama.tag_image(str(file_path), context=context)
    if tag_result.error:
        result.processing_status = "error"
        result.error_message = tag_result.error
        stats["model_failures"] += 1
    else:
        result.tags = tag_result.tags
        result.scene_summary = tag_result.summary
        result.scene_category = tag_result.scene_category
        result.emotional_tone = tag_result.emotional_tone
        result.cleanup_class = tag_result.cleanup_class
        result.has_text = tag_result.has_text
        result.text_summary = tag_result.text_summary
        result.event_hint = tag_result.event_hint
        result.significance = tag_result.significance

    return result


def _write_metadata(
    result: ImageResult,
    rich_desc: str | None,
    args: argparse.Namespace,
) -> None:
    """Write metadata for one image: sidecar-only, direct, or auto-fallback."""
    from pyimgtag.exif_writer import (
        SUPPORTED_DIRECT_WRITE_EXTENSIONS,
        write_exif_description,
        write_xmp_sidecar,
    )

    if args.sidecar_only:
        err = write_xmp_sidecar(result.file_path, description=rich_desc, keywords=result.tags)
        if err:
            print(f"  Sidecar write failed: {err}", file=sys.stderr)
        return

    # Direct write with auto-fallback for unsupported extensions
    ext = Path(result.file_path).suffix.lower()
    if ext not in SUPPORTED_DIRECT_WRITE_EXTENSIONS:
        print(
            f"  [{ext}] not supported for direct write; falling back to XMP sidecar",
            file=sys.stderr,
        )
        err = write_xmp_sidecar(result.file_path, description=rich_desc, keywords=result.tags)
        if err:
            print(f"  Sidecar write failed: {err}", file=sys.stderr)
        return

    err = write_exif_description(
        result.file_path,
        description=rich_desc,
        keywords=result.tags,
        fmt=args.metadata_format,
    )
    if err:
        print(f"  EXIF write failed: {err}", file=sys.stderr)


def _new_stats(scanned: int) -> dict[str, int]:
    return {
        "scanned": scanned,
        "processed": 0,
        "skipped_date": 0,
        "skipped_no_gps": 0,
        "skipped_no_local": 0,
        "skipped_cached": 0,
        "skipped_dedup": 0,
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
    print(" ".join(parts), file=sys.stderr)


def _print_verbose(result: ImageResult, idx: int, total: int) -> None:
    print(f"[{idx}/{total}] {result.file_name}", file=sys.stderr)
    print(f"  Path:     {result.file_path}", file=sys.stderr)
    print(f"  Date:     {result.image_date or '(unknown)'}", file=sys.stderr)
    tags = ", ".join(result.tags) if result.tags else "(none)"
    print(f"  Tags:     {tags}", file=sys.stderr)
    if result.scene_summary:
        print(f"  Summary:  {result.scene_summary}", file=sys.stderr)
    if result.scene_category:
        print(f"  Scene:    {result.scene_category}", file=sys.stderr)
    if result.emotional_tone:
        print(f"  Tone:     {result.emotional_tone}", file=sys.stderr)
    if result.cleanup_class:
        print(f"  Cleanup:  {result.cleanup_class}", file=sys.stderr)
    if result.has_text:
        print("  Has text: yes", file=sys.stderr)
        if result.text_summary:
            print(f"  Text:     {result.text_summary}", file=sys.stderr)
    if result.event_hint:
        print(f"  Event:    {result.event_hint}", file=sys.stderr)
    if result.significance:
        print(f"  Signif.:  {result.significance}", file=sys.stderr)
    if result.gps_lat is not None:
        print(f"  GPS:      {result.gps_lat}, {result.gps_lon}", file=sys.stderr)
    else:
        print("  GPS:      (none)", file=sys.stderr)
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
    print(f"  Location: {', '.join(loc_parts) if loc_parts else '(none)'}", file=sys.stderr)
    print(f"  Status:   {result.processing_status}", file=sys.stderr)
    if result.error_message:
        print(f"  Error:    {result.error_message}", file=sys.stderr)
    print(file=sys.stderr)


def _print_summary(stats: dict) -> None:
    print("\n--- Summary ---", file=sys.stderr)
    print(f"  Scanned:          {stats['scanned']}", file=sys.stderr)
    print(f"  Processed:        {stats['processed']}", file=sys.stderr)
    print(f"  Skipped (date):   {stats['skipped_date']}", file=sys.stderr)
    print(f"  Skipped (no GPS): {stats['skipped_no_gps']}", file=sys.stderr)
    print(f"  Skipped (no file):{stats['skipped_no_local']}", file=sys.stderr)
    print(f"  Skipped (cached): {stats['skipped_cached']}", file=sys.stderr)
    print(f"  Skipped (dedup):  {stats['skipped_dedup']}", file=sys.stderr)
    print(f"  Model failures:   {stats['model_failures']}", file=sys.stderr)
    print(f"  Geocode failures: {stats['geocode_failures']}", file=sys.stderr)
