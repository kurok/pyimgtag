"""CLI entry point and orchestration for pyimgtag."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from pyimgtag import __version__
from pyimgtag.webapp.config import add_web_flags

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

    run_p.add_argument(
        "--backend",
        choices=("ollama", "anthropic", "openai", "gemini"),
        default=os.environ.get("PYIMGTAG_BACKEND", "ollama"),
        help=(
            "Vision-model backend. 'ollama' (default) calls a local or remote "
            "Ollama server; 'anthropic', 'openai', and 'gemini' call hosted "
            "APIs and require an API key (ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "GOOGLE_API_KEY respectively, or pass --api-key)."
        ),
    )
    run_p.add_argument(
        "--model",
        default=None,
        help=(
            "Model name. Backend-specific defaults: ollama=gemma4:e4b, "
            "anthropic=claude-sonnet-4-6, openai=gpt-4o-mini, gemini=gemini-1.5-flash."
        ),
    )
    run_p.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        help="Ollama base URL (used when --backend=ollama; supports remote Ollama too)",
    )
    run_p.add_argument(
        "--api-base",
        default=None,
        help="Override the cloud-API base URL (anthropic / openai / gemini)",
    )
    run_p.add_argument(
        "--api-key",
        default=None,
        help="Cloud-API key. Defaults to the provider's conventional env var.",
    )
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
        "--extensions",
        default="jpg,jpeg,heic,png",
        help=(
            "Comma-separated file extensions to scan (default: jpg,jpeg,heic,png). "
            "Add RAW formats as needed, e.g. jpg,jpeg,cr2,nef,arw,dng,raf,orf,rw2"
        ),
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
        "--skip-if-tagged",
        action="store_true",
        help=(
            "Skip Ollama processing for photos that already have keywords in Apple Photos "
            "(--photos-library only; reads existing keywords before tagging)"
        ),
    )
    run_p.add_argument(
        "--resume-from-db",
        action="store_true",
        help=(
            "Reuse cached model results for unchanged files; only re-runs local enrichment "
            "(EXIF, geocoding). Ignored when --no-cache is set."
        ),
    )
    run_p.add_argument(
        "--resume-threaded",
        action="store_true",
        help=(
            "With --resume-from-db: enrich cached items in a background thread while "
            "the main thread keeps sending uncached files to Ollama."
        ),
    )
    run_p.add_argument(
        "--write-back",
        action="store_true",
        help="Write tags/description back to Apple Photos (macOS + --photos-library only)",
    )
    run_p.add_argument(
        "--write-back-mode",
        choices=("overwrite", "append"),
        default="overwrite",
        help=(
            "Write-back strategy: overwrite replaces all keywords; "
            "append merges new tags with existing ones (default: overwrite)"
        ),
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
    add_web_flags(run_p)

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
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        help="Ollama base URL",
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

    # --- review subcommand ---
    review_p = subparsers.add_parser(
        "review", help="Launch the local review UI (requires pyimgtag[review])"
    )
    review_p.add_argument("--db", help=_DEFAULT_DB_HELP)
    review_p.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    review_p.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    review_p.add_argument(
        "--no-browser", action="store_true", help="Do not open the browser automatically"
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
    add_web_flags(faces_scan)

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

    # faces import-photos
    faces_import = faces_sub.add_parser(
        "import-photos", help="Import named persons from Apple Photos library"
    )
    faces_import.add_argument("--db", help=_DEFAULT_DB_HELP)

    # faces ui
    faces_ui = faces_sub.add_parser("ui", help="Start face management web UI")
    faces_ui.add_argument("--db", help=_DEFAULT_DB_HELP)
    faces_ui.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    faces_ui.add_argument("--port", type=int, default=8766, help="Port (default: 8766)")

    # --- query subcommand ---
    query_p = subparsers.add_parser("query", help="Query images with advanced filters")
    query_p.add_argument("--db", help=_DEFAULT_DB_HELP)
    query_p.add_argument("--tag", help="Filter by tag (case-insensitive substring match)")
    query_text_grp = query_p.add_mutually_exclusive_group()
    query_text_grp.add_argument(
        "--has-text", action="store_true", default=False, help="Only images with detected text"
    )
    query_text_grp.add_argument(
        "--no-text", action="store_true", default=False, help="Only images without detected text"
    )
    query_p.add_argument(
        "--cleanup", metavar="CLASS", help="Filter by cleanup_class (e.g. delete, review)"
    )
    query_p.add_argument("--scene-category", help="Filter by scene_category (exact match)")
    query_p.add_argument("--city", help="Filter by nearest_city (case-insensitive substring)")
    query_p.add_argument("--country", help="Filter by nearest_country (case-insensitive substring)")
    query_p.add_argument("--status", choices=["ok", "error"], help="Filter by processing status")
    query_p.add_argument(
        "--format",
        choices=["table", "json", "paths"],
        default="table",
        help="Output format (default: table)",
    )
    query_p.add_argument("--limit", type=int, help="Max results to return")

    # --- judge subcommand ---
    judge_p = subparsers.add_parser(
        "judge",
        help="Score photos with the professional photo-judge rubric",
    )
    judge_src = judge_p.add_mutually_exclusive_group(required=False)
    judge_src.add_argument(
        "--input-dir",
        metavar="DIR",
        help="Directory of images to judge",
    )
    judge_src.add_argument(
        "--photos-library",
        metavar="LIBRARY",
        help="Path to Photos library (.photoslibrary)",
    )
    judge_p.add_argument(
        "--extensions",
        default="jpg,jpeg,heic,png,tiff,webp",
        help="Comma-separated file extensions (default: jpg,jpeg,heic,png,tiff,webp)",
    )
    judge_p.add_argument("--limit", type=int, metavar="N", help="Process at most N images")
    judge_p.add_argument(
        "--min-score",
        type=float,
        metavar="SCORE",
        help="Only show images with weighted score >= SCORE",
    )
    judge_p.add_argument(
        "--sort-by",
        choices=("score", "name"),
        default="score",
        help="Final sort order (default: score)",
    )
    judge_p.add_argument("--output-json", metavar="FILE", help="Write results to JSON file")
    judge_p.add_argument(
        "--verbose", action="store_true", help="Show detailed per-criterion breakdown"
    )
    judge_p.add_argument("--no-recursive", action="store_true", help="Do not scan subdirectories")
    judge_p.add_argument(
        "--write-back",
        action="store_true",
        help="Write score keyword back to Apple Photos (macOS + --photos-library only)",
    )
    judge_p.add_argument(
        "--write-back-mode",
        choices=("overwrite", "append"),
        default="overwrite",
        help=(
            "Write-back strategy: overwrite replaces all keywords; "
            "append merges score keyword with existing ones (default: overwrite)"
        ),
    )
    judge_p.add_argument("--db", help=_DEFAULT_DB_HELP)
    judge_p.add_argument(
        "--backend",
        choices=("ollama", "anthropic", "openai", "gemini"),
        default=os.environ.get("PYIMGTAG_BACKEND", "ollama"),
        help=(
            "Vision-model backend. 'ollama' (default) calls a local or remote "
            "Ollama server; the others call hosted APIs and need an API key."
        ),
    )
    judge_p.add_argument(
        "--model",
        default=None,
        help=(
            "Model name. Backend-specific defaults: ollama=gemma4:e4b, "
            "anthropic=claude-sonnet-4-6, openai=gpt-4o-mini, gemini=gemini-1.5-flash."
        ),
    )
    judge_p.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        metavar="URL",
        help="Ollama API base URL (used when --backend=ollama; supports remote Ollama)",
    )
    judge_p.add_argument(
        "--api-base",
        default=None,
        help="Override the cloud-API base URL (anthropic / openai / gemini)",
    )
    judge_p.add_argument(
        "--api-key",
        default=None,
        help="Cloud-API key. Defaults to the provider's conventional env var.",
    )
    judge_p.add_argument(
        "--max-dim",
        type=int,
        default=1280,
        metavar="PX",
        help="Max image dimension before resize",
    )
    judge_p.add_argument(
        "--timeout",
        type=int,
        default=120,
        metavar="SEC",
        help="Ollama request timeout",
    )
    judge_p.add_argument(
        "--skip-judged",
        action="store_true",
        help=(
            "Skip images that already have a row in the judge_scores DB. "
            "Lets repeat runs over the same source pick up where the last "
            "one left off instead of rescoring from scratch."
        ),
    )
    add_web_flags(judge_p)

    # --- tags subcommand group ---
    tags_p = subparsers.add_parser("tags", help="Manage tags across the image database")
    tags_sub = tags_p.add_subparsers(dest="tags_action")

    # tags list
    tags_list_p = tags_sub.add_parser("list", help="List all tags with image counts")
    tags_list_p.add_argument("--db", help=_DEFAULT_DB_HELP)

    # tags rename
    tags_rename_p = tags_sub.add_parser("rename", help="Rename a tag across all images")
    tags_rename_p.add_argument("old_tag", help="Tag to rename")
    tags_rename_p.add_argument("new_tag", help="Replacement tag")
    tags_rename_p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    tags_rename_p.add_argument("--db", help=_DEFAULT_DB_HELP)

    # tags delete
    tags_delete_p = tags_sub.add_parser("delete", help="Delete a tag from all images")
    tags_delete_p.add_argument("tag", help="Tag to delete")
    tags_delete_p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    tags_delete_p.add_argument("--db", help=_DEFAULT_DB_HELP)

    # tags merge
    tags_merge_p = tags_sub.add_parser(
        "merge", help="Merge source tag into target tag across all images"
    )
    tags_merge_p.add_argument("source_tag", help="Tag to replace")
    tags_merge_p.add_argument("target_tag", help="Tag to add (source is removed)")
    tags_merge_p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    tags_merge_p.add_argument("--db", help=_DEFAULT_DB_HELP)

    return p


def _check_for_update() -> None:
    """Print a one-line banner if a newer pyimgtag is on PyPI.

    Best-effort and short-circuited by the ``PYIMGTAG_NO_UPDATE_CHECK``
    env var so CI / scripted invocations don't pay an HTTP round-trip.
    Failure modes (no network, PyPI down, malformed response) silently
    drop the banner — the check must never block a real run.
    """
    if os.environ.get("PYIMGTAG_NO_UPDATE_CHECK"):
        return
    try:
        from pyimgtag.webapp.routes_about import _is_newer, _latest_version
    except ImportError:
        return  # webapp extras not installed → silent no-op
    try:
        latest = _latest_version()
    except Exception:  # noqa: BLE001 — never let the check block a run
        return
    if latest and _is_newer(latest, __version__):
        print(
            f"pyimgtag {latest} is available (you're on {__version__}). "
            "Run: pip install --upgrade pyimgtag",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand is None:
        parser.print_help()
        return 1

    _check_for_update()

    from pyimgtag.commands.db import cmd_cleanup, cmd_reprocess, cmd_status
    from pyimgtag.commands.faces import cmd_faces
    from pyimgtag.commands.judge import cmd_judge
    from pyimgtag.commands.preflight_cmd import cmd_preflight
    from pyimgtag.commands.query import cmd_query
    from pyimgtag.commands.review_cmd import cmd_review
    from pyimgtag.commands.run import cmd_run
    from pyimgtag.commands.tags import cmd_tags
    from pyimgtag.progress_db import ProgressDB

    progress_db: ProgressDB | None = None
    if args.subcommand == "judge":
        # Always open the progress DB for judge — without this, omitting
        # ``--db`` silently dropped every score because ``cmd_judge``'s
        # ``_db`` parameter stayed ``None`` and ``save_judge_result`` was
        # never called. The dashboard then opened the default
        # ``~/.cache/pyimgtag/progress.db`` and showed "0 scored" while
        # the CLI was happily printing scores.
        progress_db = ProgressDB(db_path=getattr(args, "db", None))

    dispatch: dict[str, Any] = {
        "run": lambda: cmd_run(args, parser),
        "status": lambda: cmd_status(args),
        "reprocess": lambda: cmd_reprocess(args),
        "preflight": lambda: cmd_preflight(args),
        "cleanup": lambda: cmd_cleanup(args),
        "review": lambda: cmd_review(args),
        "faces": lambda: cmd_faces(args),
        "query": lambda: cmd_query(args),
        "judge": lambda: cmd_judge(args, progress_db),
        "tags": lambda: cmd_tags(args),
    }

    handler = dispatch.get(args.subcommand)
    if handler is None:
        parser.print_help()
        return 1
    try:
        exit_code = handler()
    finally:
        if progress_db is not None:
            progress_db.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
