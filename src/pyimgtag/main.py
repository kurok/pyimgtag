"""CLI entry point and orchestration for pyimgtag."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from platform import system as get_platform_name

from pyimgtag import __version__
from pyimgtag.exif_reader import read_exif
from pyimgtag.filters import passes_date_filter
from pyimgtag.geocoder import ReverseGeocoder
from pyimgtag.models import ExifData, ImageResult
from pyimgtag.ollama_client import OllamaClient
from pyimgtag.output_writer import result_to_jsonl, write_csv, write_json
from pyimgtag.preflight import check_ollama, run_preflight
from pyimgtag.progress_db import ProgressDB
from pyimgtag.scanner import scan_directory, scan_photos_library

_DEFAULT_DB_HELP = "Path to progress database (default: ~/.cache/pyimgtag/progress.db)"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyimgtag",
        description="Tag images using a local Ollama Gemma vision model "
        "with EXIF GPS reverse geocoding.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = p.add_subparsers(dest="subcommand")

    # --- run subcommand ---
    run_p = subparsers.add_parser("run", help="Tag images (default workhorse)")
    src = run_p.add_mutually_exclusive_group(required=False)
    src.add_argument("--input-dir", help="Path to an exported image folder")
    src.add_argument("--photos-library", help="Path to an Apple Photos library package")

    run_p.add_argument("--model", default="gemma4:e4b", help="Ollama model (default: gemma4:e4b)")
    run_p.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama base URL")
    run_p.add_argument(
        "--max-dim",
        type=int,
        default=1280,
        help="Max image dimension sent to model (default: 1280)",
    )
    run_p.add_argument("--timeout", type=int, default=120, help="Model request timeout in seconds")
    run_p.add_argument("--limit", type=int, help="Max images to process")
    run_p.add_argument("--date", help="Process only this date (YYYY-MM-DD)")
    run_p.add_argument("--date-from", help="Process images from this date (YYYY-MM-DD)")
    run_p.add_argument("--date-to", help="Process images up to this date (YYYY-MM-DD)")
    run_p.add_argument(
        "--extensions", default="jpg,jpeg,heic,png", help="Comma-separated extensions"
    )
    run_p.add_argument("--skip-no-gps", action="store_true", help="Skip images without GPS data")
    run_p.add_argument("--dry-run", action="store_true", help="Read-only mode, print results only")
    run_p.add_argument("--output-json", help="Write results to a JSON file")
    run_p.add_argument("--output-csv", help="Write results to a CSV file")
    run_p.add_argument("--jsonl-stdout", action="store_true", help="JSONL output to stdout")
    run_p.add_argument("--verbose", "-v", action="store_true", help="Verbose per-file output")
    run_p.add_argument("--cache-dir", help="Geocoding cache directory")
    run_p.add_argument(
        "--dedup", action="store_true", help="Detect and skip duplicate images via phash"
    )
    run_p.add_argument(
        "--dedup-threshold", type=int, default=5, help="Hamming distance threshold (default: 5)"
    )
    run_p.add_argument("--db", help=_DEFAULT_DB_HELP)
    run_p.add_argument(
        "--no-cache", action="store_true", help="Skip progress database, reprocess all images"
    )
    run_p.add_argument(
        "--write-back",
        action="store_true",
        help="Write tags/description back to Apple Photos (macOS + --photos-library only)",
    )
    run_p.add_argument(
        "--write-exif",
        action="store_true",
        help="Write description and keywords to image EXIF via exiftool",
    )
    run_p.add_argument(
        "--sidecar-only",
        action="store_true",
        help="Write metadata to an XMP sidecar (.xmp) instead of modifying the original file",
    )
    run_p.add_argument(
        "--metadata-format",
        choices=["auto", "xmp", "iptc", "exif"],
        default="auto",
        help="Metadata fields to write when using --write-exif (default: auto writes all fields)",
    )
    run_p.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only scan the top-level directory (do not descend into subdirectories)",
    )
    run_p.add_argument(
        "--newest-first",
        action="store_true",
        help="Process newest files first (by modification time)",
    )

    # --- status subcommand ---
    status_p = subparsers.add_parser("status", help="Show progress stats from the DB")
    status_p.add_argument("--db", help=_DEFAULT_DB_HELP)

    # --- reprocess subcommand ---
    reprocess_p = subparsers.add_parser(
        "reprocess", help="Reset DB entries so photos get re-tagged"
    )
    reprocess_p.add_argument("--db", help=_DEFAULT_DB_HELP)
    reprocess_p.add_argument(
        "--status",
        help="Only reset entries with this status (e.g. 'error'). Omit to reset everything.",
    )

    # --- preflight subcommand ---
    preflight_p = subparsers.add_parser(
        "preflight", help="Run preflight checks for prerequisites and exit"
    )
    preflight_p.add_argument(
        "--ollama-url", default="http://localhost:11434", help="Ollama base URL"
    )
    preflight_p.add_argument(
        "--model", default="gemma4:e4b", help="Ollama model (default: gemma4:e4b)"
    )
    src_pre = preflight_p.add_mutually_exclusive_group(required=False)
    src_pre.add_argument("--input-dir", help="Path to an exported image folder")
    src_pre.add_argument("--photos-library", help="Path to an Apple Photos library package")

    # --- cleanup subcommand ---
    cleanup_p = subparsers.add_parser(
        "cleanup", help="List photos flagged for cleanup (delete/review) and exit"
    )
    cleanup_p.add_argument("--db", help=_DEFAULT_DB_HELP)
    cleanup_p.add_argument(
        "--include-review",
        action="store_true",
        help="Also show photos flagged as 'review' (default: delete only)",
    )

    # --- faces subcommand group ---
    faces_p = subparsers.add_parser("faces", help="Face detection, clustering, and tagging")
    faces_sub = faces_p.add_subparsers(dest="faces_action")

    # faces scan
    faces_scan = faces_sub.add_parser("scan", help="Detect faces and compute embeddings")
    faces_scan_src = faces_scan.add_mutually_exclusive_group(required=True)
    faces_scan_src.add_argument("--input-dir", help="Path to an exported image folder")
    faces_scan_src.add_argument("--photos-library", help="Path to an Apple Photos library package")
    faces_scan.add_argument("--db", help=_DEFAULT_DB_HELP)
    faces_scan.add_argument(
        "--max-dim",
        type=int,
        default=1280,
        help="Max image dimension for face detection (default: 1280)",
    )
    faces_scan.add_argument(
        "--detection-model",
        choices=["hog", "cnn"],
        default="hog",
        help="Face detection model: hog (fast, CPU) or cnn (accurate, GPU) (default: hog)",
    )
    faces_scan.add_argument(
        "--extensions", default="jpg,jpeg,heic,png", help="Comma-separated extensions"
    )
    faces_scan.add_argument("--limit", type=int, help="Max images to scan")

    # faces cluster
    faces_cluster = faces_sub.add_parser("cluster", help="Cluster faces into person groups")
    faces_cluster.add_argument("--db", help=_DEFAULT_DB_HELP)
    faces_cluster.add_argument(
        "--eps", type=float, default=0.5, help="DBSCAN eps radius (default: 0.5)"
    )
    faces_cluster.add_argument(
        "--min-samples",
        type=int,
        default=2,
        help="Minimum faces to form a cluster (default: 2)",
    )

    # faces review
    faces_review = faces_sub.add_parser("review", help="List detected persons and face counts")
    faces_review.add_argument("--db", help=_DEFAULT_DB_HELP)

    # faces apply
    faces_apply = faces_sub.add_parser("apply", help="Write person keywords to image metadata")
    faces_apply.add_argument("--db", help=_DEFAULT_DB_HELP)
    faces_apply.add_argument(
        "--write-exif",
        action="store_true",
        help="Write person keywords to image EXIF via exiftool",
    )
    faces_apply.add_argument(
        "--sidecar-only",
        action="store_true",
        help="Write to XMP sidecar instead of modifying the original file",
    )
    faces_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview keyword changes without writing",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand is None:
        parser.print_help()
        return 1

    if args.subcommand == "run":
        return _handle_run(args, parser)

    if args.subcommand == "status":
        return _handle_status(args)

    if args.subcommand == "reprocess":
        return _handle_reprocess(args)

    if args.subcommand == "preflight":
        return _handle_preflight(args)

    if args.subcommand == "cleanup":
        return _handle_cleanup(args)

    if args.subcommand == "faces":
        return _handle_faces(args)

    # Should never reach here
    parser.print_help()
    return 1


def _handle_run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
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

    extensions = {e.strip().lower() for e in args.extensions.split(",")}

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

    if not files:
        print("No image files found.", file=sys.stderr)
        return 0

    if args.newest_first:
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    # --- dedup ---
    phash_map: dict[str, str] = {}
    skipped_dedup: set[str] = set()
    if args.dedup:
        from pyimgtag.dedup import compute_phash, find_duplicate_groups

        print("Computing perceptual hashes...", file=sys.stderr)
        records: list[tuple[str, str]] = []
        for f in files:
            h = compute_phash(f)
            if h is not None:
                records.append((str(f), h))
                phash_map[str(f)] = h
        groups = find_duplicate_groups(records, threshold=args.dedup_threshold)
        dup_count = 0
        for group in groups:
            for path in sorted(group)[1:]:
                skipped_dedup.add(path)
                dup_count += 1
        print(
            f"Found {len(groups)} duplicate groups ({dup_count} images skipped, "
            f"keeping 1 per group)",
            file=sys.stderr,
        )

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
                        print(f"  [dry-run] Would write to {target}:")
                        if rich_desc:
                            print(f"    description: {rich_desc[:80]}")
                        print(f"    keywords: {', '.join(result.tags)}")
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


def _handle_status(args: argparse.Namespace) -> int:
    """Show progress stats from the DB."""
    db = ProgressDB(db_path=args.db)
    try:
        stats = db.get_stats()
    finally:
        db.close()

    total = stats["total"]
    ok = stats["ok"]
    error = stats["error"]
    pending = total - ok - error

    pct = f"{ok * 100 // total}%" if total > 0 else "0%"
    print(f"Progress: {ok} / {total} ({pct})")
    print(f"  ok:      {ok}")
    print(f"  error:   {error}")
    print(f"  pending: {pending}")
    return 0


def _handle_reprocess(args: argparse.Namespace) -> int:
    """Reset DB entries so photos get re-tagged."""
    db = ProgressDB(db_path=args.db)
    try:
        if args.status:
            count = db.reset_by_status(args.status)
        else:
            count = db.reset_all()
    finally:
        db.close()

    print(f"Reset {count} entries for reprocessing.")
    return 0


def _handle_preflight(args: argparse.Namespace) -> int:
    """Run preflight checks, print results, and return exit code."""
    source_path: str | None = None
    source_type = "directory"
    if args.input_dir:
        source_path = args.input_dir
        source_type = "directory"
    elif args.photos_library:
        source_path = args.photos_library
        source_type = "photos_library"

    results = run_preflight(args.ollama_url, args.model, source_path, source_type)

    print("Preflight checks:")
    all_passed = True
    for name, passed, msg in results:
        label = "[PASS]" if passed else "[FAIL]"
        print(f"  {label} {msg}")
        if not passed:
            all_passed = False

    return 0 if all_passed else 1


def _handle_cleanup(args: argparse.Namespace) -> int:
    """List photos flagged for cleanup and exit."""
    db = ProgressDB(db_path=args.db)
    try:
        candidates = db.get_cleanup_candidates(include_review=args.include_review)
    finally:
        db.close()

    if not candidates:
        print("No cleanup candidates found.")
        return 0

    label = "delete + review" if args.include_review else "delete"
    print(f"Cleanup candidates ({label}): {len(candidates)}")
    print()
    for item in candidates:
        tags = ", ".join(json.loads(item["tags"])) if item.get("tags") else "(none)"
        loc = item.get("nearest_city") or ""
        if item.get("nearest_country") and loc:
            loc = f"{loc}, {item['nearest_country']}"
        parts = [f"[{item['cleanup_class']}]", item["file_path"]]
        if loc:
            parts.append(f"| {loc}")
        if item.get("image_date"):
            parts.append(f"| {item['image_date'][:10]}")
        parts.append(f"| tags: {tags}")
        print("  " + "  ".join(parts))
    return 0


def _handle_faces(args: argparse.Namespace) -> int:
    """Dispatch faces sub-actions."""
    if args.faces_action is None:
        print("Usage: pyimgtag faces {scan,cluster,review,apply}", file=sys.stderr)
        return 1

    if args.faces_action == "scan":
        return _handle_faces_scan(args)
    if args.faces_action == "cluster":
        return _handle_faces_cluster(args)
    if args.faces_action == "review":
        return _handle_faces_review(args)
    if args.faces_action == "apply":
        return _handle_faces_apply(args)

    return 1


def _handle_faces_scan(args: argparse.Namespace) -> int:
    """Detect faces and compute embeddings for all images."""
    try:
        from pyimgtag.face_embedding import scan_and_store
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    extensions = {e.strip().lower() for e in args.extensions.split(",")}

    try:
        if args.input_dir:
            files = scan_directory(args.input_dir, extensions)
        else:
            files = scan_photos_library(args.photos_library, extensions)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not files:
        print("No image files found.", file=sys.stderr)
        return 0

    db = ProgressDB(db_path=args.db)
    total_faces = 0
    scanned = 0
    try:
        for i, file_path in enumerate(files):
            if args.limit and i >= args.limit:
                break
            count = scan_and_store(file_path, db, max_dim=args.max_dim, model=args.detection_model)
            scanned += 1
            if count > 0:
                total_faces += count
                print(f"  {file_path.name}: {count} face(s)", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
    finally:
        db.close()

    print(f"\nScanned {scanned} images, detected {total_faces} faces.", file=sys.stderr)
    return 0


def _handle_faces_cluster(args: argparse.Namespace) -> int:
    """Cluster detected faces into person groups."""
    try:
        from pyimgtag.face_clustering import cluster_faces
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    db = ProgressDB(db_path=args.db)
    try:
        result = cluster_faces(db, eps=args.eps, min_samples=args.min_samples)
    finally:
        db.close()

    if not result:
        print("No clusters formed. Need more faces or adjust --eps/--min-samples.")
        return 0

    print(f"Created {len(result)} person cluster(s):")
    for person_id, face_ids in result.items():
        print(f"  Person {person_id}: {len(face_ids)} face(s)")
    return 0


def _handle_faces_review(args: argparse.Namespace) -> int:
    """List detected persons and face counts."""
    db = ProgressDB(db_path=args.db)
    try:
        persons = db.get_persons()
        total_faces = db.get_face_count()
    finally:
        db.close()

    if not persons and total_faces == 0:
        print("No faces detected yet. Run 'pyimgtag faces scan' first.")
        return 0

    assigned = sum(len(p.face_ids) for p in persons)
    unassigned = total_faces - assigned

    print(f"Faces: {total_faces} total, {assigned} assigned, {unassigned} unassigned")
    if persons:
        print(f"\nPersons ({len(persons)}):")
        for p in persons:
            status = "confirmed" if p.confirmed else "auto"
            label = p.label or f"(unlabelled #{p.person_id})"
            print(f"  [{status}] {label}: {len(p.face_ids)} face(s)")
    if unassigned > 0:
        print(f"\n{unassigned} face(s) not assigned to any person.")
        print("Run 'pyimgtag faces cluster' to group them.")
    return 0


def _handle_faces_apply(args: argparse.Namespace) -> int:
    """Write person keywords to image metadata."""
    db = ProgressDB(db_path=args.db)
    try:
        persons = db.get_persons()
        if not persons:
            print("No persons found. Run 'pyimgtag faces scan' and 'faces cluster' first.")
            return 0

        # Build face_id -> person label mapping
        face_to_label: dict[int, str] = {}
        for p in persons:
            label = p.label or f"person_{p.person_id}"
            for fid in p.face_ids:
                face_to_label[fid] = label

        # Build image_path -> list of person keywords
        image_keywords: dict[str, list[str]] = {}
        rows = db._conn.execute(
            "SELECT id, image_path FROM faces WHERE person_id IS NOT NULL"
        ).fetchall()
        for face_id, image_path in rows:
            label = face_to_label.get(face_id, "")
            if label:
                keyword = f"person:{label}"
                image_keywords.setdefault(image_path, [])
                if keyword not in image_keywords[image_path]:
                    image_keywords[image_path].append(keyword)
    finally:
        db.close()

    if not image_keywords:
        print("No face-to-person assignments to write.")
        return 0

    written = 0
    for image_path, keywords in sorted(image_keywords.items()):
        if args.dry_run:
            print(f"  [dry-run] {Path(image_path).name}: {', '.join(keywords)}")
            continue

        if not (args.write_exif or args.sidecar_only):
            print(f"  {Path(image_path).name}: {', '.join(keywords)}")
            continue

        err = _write_person_keywords(image_path, keywords, args)
        if err:
            print(f"  {Path(image_path).name}: FAILED - {err}", file=sys.stderr)
        else:
            written += 1
            print(f"  {Path(image_path).name}: {', '.join(keywords)}")

    if args.dry_run:
        print(f"\n[dry-run] Would write to {len(image_keywords)} image(s).")
    elif args.write_exif or args.sidecar_only:
        print(f"\nWrote person keywords to {written}/{len(image_keywords)} image(s).")
    else:
        print(f"\n{len(image_keywords)} image(s) have person keywords.")
        print("Use --write-exif or --sidecar-only to write them to files.")
    return 0


def _write_person_keywords(
    image_path: str,
    keywords: list[str],
    args: argparse.Namespace,
) -> str | None:
    """Write person keywords to one image. Returns error string or None."""
    from pyimgtag.exif_writer import (
        SUPPORTED_DIRECT_WRITE_EXTENSIONS,
        write_exif_description,
        write_xmp_sidecar,
    )

    if args.sidecar_only:
        return write_xmp_sidecar(image_path, keywords=keywords)

    ext = Path(image_path).suffix.lower()
    if ext not in SUPPORTED_DIRECT_WRITE_EXTENSIONS:
        return write_xmp_sidecar(image_path, keywords=keywords)

    return write_exif_description(image_path, keywords=keywords, merge=True)


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


def _new_stats(scanned: int) -> dict:
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
    print(" ".join(parts))


def _print_verbose(result: ImageResult, idx: int, total: int) -> None:
    print(f"[{idx}/{total}] {result.file_name}")
    print(f"  Path:     {result.file_path}")
    print(f"  Date:     {result.image_date or '(unknown)'}")
    tags = ", ".join(result.tags) if result.tags else "(none)"
    print(f"  Tags:     {tags}")
    if result.scene_summary:
        print(f"  Summary:  {result.scene_summary}")
    if result.scene_category:
        print(f"  Scene:    {result.scene_category}")
    if result.emotional_tone:
        print(f"  Tone:     {result.emotional_tone}")
    if result.cleanup_class:
        print(f"  Cleanup:  {result.cleanup_class}")
    if result.has_text:
        print("  Has text: yes")
        if result.text_summary:
            print(f"  Text:     {result.text_summary}")
    if result.event_hint:
        print(f"  Event:    {result.event_hint}")
    if result.significance:
        print(f"  Signif.:  {result.significance}")
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
    print(f"  Skipped (cached): {stats['skipped_cached']}", file=sys.stderr)
    print(f"  Skipped (dedup):  {stats['skipped_dedup']}", file=sys.stderr)
    print(f"  Model failures:   {stats['model_failures']}", file=sys.stderr)
    print(f"  Geocode failures: {stats['geocode_failures']}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
