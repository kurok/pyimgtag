"""Handler for the ``run`` subcommand (image tagging workhorse)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from platform import system as get_platform_name
from typing import Any

from pyimgtag import run_registry
from pyimgtag.applescript_writer import read_keywords_from_photos
from pyimgtag.cloud_clients import CloudClientError, make_image_client
from pyimgtag.exif_reader import read_exif
from pyimgtag.filters import passes_date_filter
from pyimgtag.geocoder import ReverseGeocoder
from pyimgtag.models import ExifData, ImageResult
from pyimgtag.ollama_client import OllamaClient  # noqa: F401  (kept for test patching)
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
        pass  # dialog is best-effort; silently ignore launch and timeout failures


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

    if args.skip_if_tagged and args.input_dir:
        print("Warning: --skip-if-tagged has no effect with --input-dir", file=sys.stderr)

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

    backend = getattr(args, "backend", "ollama")
    if not isinstance(backend, str):
        backend = "ollama"
    if backend == "ollama":
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

    if backend == "ollama":
        # Constructed directly so tests that patch
        # ``pyimgtag.commands.run.OllamaClient`` keep working.
        ollama = OllamaClient(
            model=args.model or "gemma4:e4b",
            base_url=args.ollama_url,
            max_dim=args.max_dim,
            timeout=args.timeout,
        )
    else:
        try:
            ollama = make_image_client(
                backend,
                model=args.model,
                max_dim=args.max_dim,
                timeout=args.timeout,
                api_key=getattr(args, "api_key", None),
                api_base=getattr(args, "api_base", None),
            )
        except CloudClientError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    geocoder = ReverseGeocoder(cache_dir=args.cache_dir)
    progress_db: ProgressDB | None = None
    if not args.no_cache and not args.dry_run:
        progress_db = ProgressDB(db_path=args.db)

    results: list[ImageResult] = []
    stats = _new_stats(len(files))

    from pyimgtag.webapp.bootstrap import start_dashboard_for

    session, dashboard = start_dashboard_for(args, command="run")
    if session is not None:
        session.set_counter("scanned", stats["scanned"])
        session.mark_running()

    try:
        use_resume = getattr(args, "resume_from_db", False) and not args.no_cache
        use_threaded = use_resume and getattr(args, "resume_threaded", False)

        if use_threaded and progress_db is not None:
            import queue as _queue
            import threading as _threading

            cached_files = [
                f
                for f in files
                if str(f) not in skipped_dedup and progress_db.has_usable_model_result(f)
            ]
            cached_set = {str(f) for f in cached_files}
            fresh_files = [
                f for f in files if str(f) not in skipped_dedup and str(f) not in cached_set
            ]
            stats["skipped_dedup"] = sum(1 for f in files if str(f) in skipped_dedup)
            result_q: _queue.Queue = _queue.Queue()
            thread_stats: dict = {k: 0 for k in stats if k != "scanned"}
            stop_event = _threading.Event()

            def _cache_worker() -> None:
                thread_db = ProgressDB(db_path=args.db)
                thread_geo = ReverseGeocoder(cache_dir=args.cache_dir)
                try:
                    for fp in cached_files:
                        if stop_event.is_set():
                            break
                        r = _hydrate_from_db(
                            fp, source_type, args, thread_geo, thread_stats, thread_db
                        )
                        if r is not None:
                            result_q.put(r)
                finally:
                    thread_db.close()
                    thread_geo.close()
                    result_q.put(None)  # sentinel

            worker_thread = _threading.Thread(target=_cache_worker, daemon=True)
            worker_thread.start()

            def _drain(sentinel_seen: bool) -> bool:
                while True:
                    try:
                        item = result_q.get_nowait()
                    except _queue.Empty:
                        break
                    if item is None:
                        sentinel_seen = True
                    else:
                        _finalize_result(
                            item, Path(item.file_path), args, progress_db, phash_map, results, stats
                        )
                return sentinel_seen

            sentinel_seen = False
            try:
                for file_path in fresh_files:
                    if args.limit and stats["processed"] >= args.limit:
                        break

                    if session is not None:
                        session.wait_if_paused()
                        session.set_current(str(file_path))

                    sentinel_seen = _drain(sentinel_seen)
                    result = _process_one(
                        file_path, source_type, args, ollama, geocoder, stats, progress_db
                    )
                    if result is not None:
                        _finalize_result(
                            result, file_path, args, progress_db, phash_map, results, stats
                        )
                        if session is not None:
                            status = "ok" if result.processing_status == "ok" else "error"
                            session.record_item(
                                str(file_path),
                                status,
                                error=result.error_message,
                            )
                            for k, v in stats.items():
                                session.set_counter(k, v)
                    if session is not None:
                        session.set_current(None)
            except KeyboardInterrupt:
                stop_event.set()
                if session is not None:
                    session.mark_interrupted()
                print("\nInterrupted.", file=sys.stderr)
            finally:
                if not stop_event.is_set():
                    worker_thread.join()
                    while not sentinel_seen:
                        sentinel_seen = _drain(sentinel_seen)
                for k, v in thread_stats.items():
                    stats[k] += v
                ollama.close()
                geocoder.close()
                if progress_db is not None:
                    progress_db.close()
        else:
            try:
                for file_path in files:
                    if args.limit and stats["processed"] >= args.limit:
                        break

                    if session is not None:
                        session.wait_if_paused()
                        session.set_current(str(file_path))

                    if str(file_path) in skipped_dedup:
                        stats["skipped_dedup"] += 1
                        continue

                    result = _process_one(
                        file_path, source_type, args, ollama, geocoder, stats, progress_db
                    )
                    if result is None:
                        if session is not None:
                            session.set_current(None)
                        continue

                    _finalize_result(
                        result, file_path, args, progress_db, phash_map, results, stats
                    )

                    if session is not None:
                        status = "ok" if result.processing_status == "ok" else "error"
                        session.record_item(
                            str(file_path),
                            status,
                            error=result.error_message,
                        )
                        for k, v in stats.items():
                            session.set_counter(k, v)
                        session.set_current(None)

            except KeyboardInterrupt:
                if session is not None:
                    session.mark_interrupted()
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
        if session is not None:
            for k, v in stats.items():
                session.set_counter(k, v)
            session.mark_completed()
    finally:
        if dashboard is not None:
            dashboard.stop()
        run_registry.set_current(None)
    return 0


def _process_one(
    file_path: Path,
    source_type: str,
    args: argparse.Namespace,
    ollama: Any,
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
        if not getattr(args, "resume_from_db", False):
            stats["skipped_cached"] += 1
            return None
        # --resume-from-db: fall through to the resume check below

    resume = getattr(args, "resume_from_db", False) and not getattr(args, "no_cache", False)
    if resume and progress_db is not None and progress_db.has_usable_model_result(file_path):
        return _hydrate_from_db(file_path, source_type, args, geocoder, stats, progress_db)

    if args.skip_if_tagged and source_type == "photos_library":
        existing = read_keywords_from_photos(str(file_path))
        if existing:
            stats["skipped_tagged"] += 1
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


def _hydrate_from_db(
    file_path: Path,
    source_type: str,
    args: argparse.Namespace,
    geocoder: ReverseGeocoder,
    stats: dict,
    progress_db: ProgressDB,
) -> ImageResult | None:
    """Load a cached ImageResult from DB and enrich it with fresh EXIF/geocode data."""
    result = progress_db.get_cached_result(file_path)
    if result is None:
        return None

    result.source_type = source_type
    result.is_local = True
    result.file_path = str(file_path)
    result.file_name = file_path.name

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

    if exif.has_gps:
        geo = geocoder.resolve(exif.gps_lat, exif.gps_lon)
        if not geo.error:
            result.nearest_place = geo.nearest_place
            result.nearest_city = geo.nearest_city
            result.nearest_region = geo.nearest_region
            result.nearest_country = geo.nearest_country
        else:
            stats["geocode_failures"] += 1

    progress_db.update_missing_fields(file_path, result)
    stats["resumed_from_db"] += 1
    return result


def _finalize_result(
    result: ImageResult,
    file_path: Path,
    args: argparse.Namespace,
    progress_db: ProgressDB | None,
    phash_map: dict,
    results: list,
    stats: dict,
) -> None:
    """Mark done in DB, handle write-back, append to results list, print progress."""
    if progress_db is not None:
        progress_db.mark_done(file_path, result)

    rich_desc = result.build_description()

    if (
        args.write_back
        and not args.dry_run
        and result.source_type == "photos_library"
        and result.tags
    ):
        from pyimgtag.applescript_writer import write_to_photos

        err = write_to_photos(
            result.file_name,
            result.tags,
            rich_desc,
            title=result.scene_summary,
            mode=args.write_back_mode,
        )
        if err:
            print(f"  Write-back failed: {err}", file=sys.stderr)

    if (getattr(args, "write_exif", False) or getattr(args, "sidecar_only", False)) and result.tags:
        if args.dry_run:
            if args.verbose:
                target = "sidecar" if getattr(args, "sidecar_only", False) else "file"
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


def _new_stats(scanned: int) -> dict[str, int]:
    return {
        "scanned": scanned,
        "processed": 0,
        "skipped_date": 0,
        "skipped_no_gps": 0,
        "skipped_no_local": 0,
        "skipped_cached": 0,
        "skipped_dedup": 0,
        "skipped_tagged": 0,
        "model_failures": 0,
        "geocode_failures": 0,
        "resumed_from_db": 0,
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
    print(f"  Skipped (tagged): {stats['skipped_tagged']}", file=sys.stderr)
    print(f"  Model failures:   {stats['model_failures']}", file=sys.stderr)
    print(f"  Geocode failures: {stats['geocode_failures']}", file=sys.stderr)
    print(f"  Resumed (DB):     {stats['resumed_from_db']}", file=sys.stderr)
