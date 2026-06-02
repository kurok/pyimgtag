"""CLI entry point and orchestration for pyimgtag."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from pyimgtag import __version__
from pyimgtag.webapp.config import add_web_flags

_DEFAULT_DB_HELP = "Path to progress database (default: ~/.cache/pyimgtag/progress.db)"


# Per-subcommand help. Each entry is (summary, description, epilog):
#   - summary:     the one-liner shown under ``pyimgtag -h``
#   - description: the overview shown at the top of ``pyimgtag <name> -h``
#   - epilog:      a worked-example block shown at the bottom of ``pyimgtag <name> -h``
# The example blocks rely on a raw formatter (see ``_sub``) to keep their line breaks.
_SUBCOMMAND_HELP: dict[str, tuple[str, str, str]] = {
    "run": (
        "Tag images (default workhorse)",
        "Tag images with the local vision model and EXIF GPS reverse geocoding.\n"
        "Each image gets tags, a scene summary, and rich metadata; results can be\n"
        "written back to Apple Photos (macOS) or to image EXIF/XMP sidecars.",
        """\
Examples:
  # Tag every image in an exported folder
  pyimgtag run --input-dir ~/Pictures/export

  # Tag an Apple Photos library and write keywords + description back (macOS)
  pyimgtag run --photos-library ~/Pictures/Photos.photoslibrary --write-back

  # Resume a large library, reusing cached model results for unchanged files
  pyimgtag run --photos-library LIB --resume-from-db

  # Fastest resume: fully skip photos already complete in the DB
  pyimgtag run --photos-library LIB --skip-existing

  # Use a hosted backend instead of local Ollama
  pyimgtag run --input-dir DIR --backend anthropic --api-key sk-...
""",
    ),
    "status": (
        "Show progress stats from the DB",
        "Print counts of processed images by status (ok / error) from the progress\ndatabase.",
        """\
Examples:
  pyimgtag status
  pyimgtag status --db ./my-run.db
""",
    ),
    "reprocess": (
        "Reset DB entries so photos get re-tagged",
        "Clear processed-image rows so the next run re-tags them. Omit --status to\n"
        "reset everything (requires --yes), or pass --status error to retry only\n"
        "failed images.",
        """\
Examples:
  # Retry only the images that errored last run
  pyimgtag reprocess --status error

  # Reset the whole database (clears all tagging progress)
  pyimgtag reprocess --yes
""",
    ),
    "preflight": (
        "Run preflight checks for prerequisites and exit",
        "Check that prerequisites are in place (Ollama reachable, model pulled,\n"
        "exiftool installed, source path readable) and exit without tagging.",
        """\
Examples:
  pyimgtag preflight
  pyimgtag preflight --model gemma4:e4b --input-dir ~/Pictures/export
""",
    ),
    "cleanup": (
        "List photos flagged for cleanup (delete/review) and exit",
        "List photos the model flagged for cleanup. Shows 'delete' candidates by\n"
        "default; add --include-review to also list 'review' candidates.",
        """\
Examples:
  pyimgtag cleanup
  pyimgtag cleanup --include-review
""",
    ),
    "cleanup-drift": (
        "Find DB rows whose backing file is gone (and, on macOS, photos that "
        "Apple Photos no longer indexes). Use --prune to actually delete them.",
        "Find progress-DB rows whose backing file is gone (and, on macOS, photos\n"
        "Apple Photos no longer indexes). Lists dead rows by default; use --prune\n"
        "to delete them from the database.",
        """\
Examples:
  # Dry run: list the dead rows (default behaviour)
  pyimgtag cleanup-drift

  # Actually delete the dead rows from the DB
  pyimgtag cleanup-drift --prune
""",
    ),
    "review": (
        "Launch the local review UI (requires pyimgtag[review])",
        "Start the local web UI to browse, filter, and edit tagged images.\n"
        "Requires the [review] extra: pip install 'pyimgtag[review]'.",
        """\
Examples:
  pyimgtag review
  pyimgtag review --port 9000 --no-browser
""",
    ),
    "faces": (
        "Face detection, clustering, and tagging",
        "Detect faces, cluster them into people, review/label the clusters, and\n"
        "write person keywords back to images. Requires the [face] extra. Run the\n"
        "sub-actions in order: scan -> cluster -> review -> apply.",
        """\
Examples:
  pyimgtag faces scan --input-dir ~/Pictures/export
  pyimgtag faces cluster --eps 0.5 --min-samples 2
  pyimgtag faces review
  pyimgtag faces apply --write-exif
  pyimgtag faces ui            # web UI for naming and merging people

  pyimgtag faces recluster --yes        # clear auto-clusters and re-cluster
  pyimgtag faces reset-untrusted --yes  # drop non-trusted faces, keep named people
  pyimgtag faces reset --yes            # wipe ALL faces/persons and start over

Reset/recluster actions show a preview of what would change; add --yes to apply.
Run them when no 'faces scan' is in progress — a scan re-clusters in the
background, which would race a concurrent reset/recluster.
Run 'pyimgtag faces <action> -h' for action-specific options.
""",
    ),
    "query": (
        "Query images with advanced filters",
        "Query tagged images with filters (tag, city, country, scene category,\n"
        "text presence, cleanup class, status) and print them as a table, JSON,\n"
        "or bare file paths.",
        """\
Examples:
  pyimgtag query --tag sunset --country US
  pyimgtag query --scene-category outdoor_travel --format paths
  pyimgtag query --status error --format json
""",
    ),
    "judge": (
        "Score photos with the professional photo-judge rubric",
        "Score photos 1-10 with the photo-judge rubric and print a ranked list.\n"
        "Optionally write the score back to Apple Photos as a keyword.",
        """\
Examples:
  pyimgtag judge --input-dir ~/Pictures/export --min-score 7
  pyimgtag judge --photos-library LIB --sort-by score --verbose
""",
    ),
    "tags": (
        "Manage tags across the image database",
        "List, rename, delete, or merge tags across all images in the database.",
        """\
Examples:
  pyimgtag tags list
  pyimgtag tags rename beach seaside
  pyimgtag tags merge seaside beach
  pyimgtag tags delete blurry --dry-run

Run 'pyimgtag tags <action> -h' for action-specific options.
""",
    ),
}


def _sub(subparsers: Any, name: str) -> argparse.ArgumentParser:
    """Register a top-level subcommand with a documented ``-h`` page.

    Looks up the summary, description, and worked-example epilog from
    :data:`_SUBCOMMAND_HELP` and wires up
    :class:`argparse.RawDescriptionHelpFormatter` so the example block's line
    breaks survive into the rendered help.

    Args:
        subparsers: The subparsers action returned by ``add_subparsers``.
        name: The subcommand name; must be a key in :data:`_SUBCOMMAND_HELP`.

    Returns:
        The created :class:`argparse.ArgumentParser` for the subcommand.
    """
    summary, description, epilog = _SUBCOMMAND_HELP[name]
    return subparsers.add_parser(
        name,
        help=summary,
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def _add_run_subcommand(subparsers: Any) -> None:
    run_p = _sub(subparsers, "run")
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
        "--skip-existing",
        action="store_true",
        help=(
            "Fully skip any unchanged photo that already has a usable result in the DB "
            "(status ok + non-empty tags): no EXIF re-read, geocoding, write-back, or DB "
            "rewrite. Fastest way to resume a large, mostly-tagged library. Note: cached "
            "photos are NOT (re)written even with --write-back/--write-exif. Takes "
            "precedence over --resume-from-db. Ignored when --no-cache is set."
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


def _add_status_reprocess_preflight_subcommands(subparsers: Any) -> None:
    status_p = _sub(subparsers, "status")
    status_p.add_argument("--db", help=_DEFAULT_DB_HELP)

    reprocess_p = _sub(subparsers, "reprocess")
    reprocess_p.add_argument("--db", help=_DEFAULT_DB_HELP)
    reprocess_p.add_argument(
        "--status",
        help="Only reset entries with this status (e.g. 'error'). Omit to reset everything.",
    )
    reprocess_p.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Confirm resetting ALL rows (required when --status is omitted).",
    )

    preflight_p = _sub(subparsers, "preflight")
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


def _add_cleanup_subcommands(subparsers: Any) -> None:
    cleanup_p = _sub(subparsers, "cleanup")
    cleanup_p.add_argument("--db", help=_DEFAULT_DB_HELP)
    cleanup_p.add_argument(
        "--include-review",
        action="store_true",
        help="Also show photos flagged as 'review' (default: delete only)",
    )

    drift_p = _sub(subparsers, "cleanup-drift")
    drift_p.add_argument("--db", help=_DEFAULT_DB_HELP)
    drift_mode = drift_p.add_mutually_exclusive_group()
    drift_mode.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="List dead rows and exit (default behaviour).",
    )
    drift_mode.add_argument(
        "--prune",
        action="store_true",
        default=False,
        help="Delete rows whose backing file is gone from disk (disk_missing).",
    )
    drift_p.add_argument(
        "--prune-photos-missing",
        action="store_true",
        default=False,
        help=(
            "Also delete rows whose file is still on disk but not indexed by "
            "Apple Photos (implies --prune). Riskier: a degraded or partial "
            "Photos probe can flag live photos, so verify the dry-run first."
        ),
    )


def _add_review_subcommand(subparsers: Any) -> None:
    review_p = _sub(subparsers, "review")
    review_p.add_argument("--db", help=_DEFAULT_DB_HELP)
    review_p.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    review_p.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    review_p.add_argument(
        "--no-browser", action="store_true", help="Do not open the browser automatically"
    )


def _add_faces_subcommand(subparsers: Any) -> None:
    faces_p = _sub(subparsers, "faces")
    faces_sub = faces_p.add_subparsers(dest="faces_action")

    faces_scan = faces_sub.add_parser("scan", help="Detect faces and compute embeddings")
    faces_scan_src = faces_scan.add_mutually_exclusive_group(required=True)
    faces_scan_src.add_argument("--input-dir", help="Path to an exported image folder")
    faces_scan_src.add_argument("--photos-library", help="Path to an Apple Photos library package")
    faces_scan.add_argument("--db", help=_DEFAULT_DB_HELP)
    faces_scan.add_argument(
        "--quality",
        choices=["fast", "balanced", "accurate"],
        default="balanced",
        help="Detection quality preset (default: balanced). 'fast' matches the "
        "old behaviour (hog, no upsample/jitter); 'accurate' uses the cnn model "
        "(much slower on CPU). The individual flags below override the preset.",
    )
    faces_scan.add_argument(
        "--max-dim",
        type=int,
        default=None,
        help="Override the preset's max image dimension for detection",
    )
    faces_scan.add_argument(
        "--detection-model",
        choices=["hog", "cnn"],
        default=None,
        help="Override the preset's model: hog (fast, CPU) or cnn (accurate, GPU)",
    )
    faces_scan.add_argument(
        "--upsample",
        type=int,
        default=None,
        help="Override upsample passes; higher finds smaller faces but is slower",
    )
    faces_scan.add_argument(
        "--num-jitters",
        type=int,
        default=None,
        help="Override encoding jitters; higher improves matching but is slower",
    )
    faces_scan.add_argument(
        "--min-face-size",
        type=int,
        default=0,
        help="Drop faces smaller than N px (shorter side) (default: 0 = keep all)",
    )
    faces_scan.add_argument(
        "--extensions", default="jpg,jpeg,heic,png", help="Comma-separated extensions"
    )
    faces_scan.add_argument("--limit", type=int, help="Max images to scan")
    add_web_flags(faces_scan)

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

    faces_review = faces_sub.add_parser("review", help="List detected persons and face counts")
    faces_review.add_argument("--db", help=_DEFAULT_DB_HELP)

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

    faces_import = faces_sub.add_parser(
        "import-photos", help="Import named persons from Apple Photos library"
    )
    faces_import.add_argument("--db", help=_DEFAULT_DB_HELP)
    faces_import.add_argument(
        "--library",
        help="Path to a .photoslibrary for the osxphotos reader "
        "(default: auto-detect the system library).",
    )

    faces_match = faces_sub.add_parser(
        "match-references",
        help="Name auto clusters from a folder of labeled reference faces",
    )
    faces_match.add_argument(
        "reference_dir",
        help="Folder of labeled face images: 'Name.jpg' or 'Name/<images>'.",
    )
    faces_match.add_argument("--db", help=_DEFAULT_DB_HELP)
    faces_match.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Max embedding distance for a match (default: 0.5; lower = stricter).",
    )
    faces_match.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write the names (without it, only a preview of proposed matches is shown).",
    )

    faces_ui = faces_sub.add_parser("ui", help="Start face management web UI")
    faces_ui.add_argument("--db", help=_DEFAULT_DB_HELP)
    faces_ui.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    faces_ui.add_argument("--port", type=int, default=8766, help="Port (default: 8766)")

    _RESET_YES_HELP = (
        "Perform the reset (without it, only a preview of what would be removed is shown)"
    )

    faces_reset = faces_sub.add_parser(
        "reset", help="Delete ALL faces, persons (incl. trusted), and the scan cache"
    )
    faces_reset.add_argument("--db", help=_DEFAULT_DB_HELP)
    faces_reset.add_argument("--yes", action="store_true", help=_RESET_YES_HELP)

    faces_reset_unt = faces_sub.add_parser(
        "reset-untrusted",
        help="Delete non-trusted faces and clusters; keep trusted/named people",
    )
    faces_reset_unt.add_argument("--db", help=_DEFAULT_DB_HELP)
    faces_reset_unt.add_argument("--yes", action="store_true", help=_RESET_YES_HELP)

    faces_recluster = faces_sub.add_parser(
        "recluster",
        help="Clear auto-clusters and re-cluster from scratch (keeps trusted people)",
    )
    faces_recluster.add_argument("--db", help=_DEFAULT_DB_HELP)
    faces_recluster.add_argument(
        "--eps", type=float, default=0.5, help="DBSCAN eps radius (default: 0.5)"
    )
    faces_recluster.add_argument(
        "--min-samples", type=int, default=2, help="Minimum faces to form a cluster (default: 2)"
    )
    faces_recluster.add_argument("--yes", action="store_true", help=_RESET_YES_HELP)


def _add_query_subcommand(subparsers: Any) -> None:
    query_p = _sub(subparsers, "query")
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


def _add_judge_subcommand(subparsers: Any) -> None:
    judge_p = _sub(subparsers, "judge")
    judge_src = judge_p.add_mutually_exclusive_group(required=False)
    judge_src.add_argument("--input-dir", metavar="DIR", help="Directory of images to judge")
    judge_src.add_argument(
        "--photos-library", metavar="LIBRARY", help="Path to Photos library (.photoslibrary)"
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


def _add_tags_subcommand(subparsers: Any) -> None:
    tags_p = _sub(subparsers, "tags")
    tags_sub = tags_p.add_subparsers(dest="tags_action")

    tags_list_p = tags_sub.add_parser("list", help="List all tags with image counts")
    tags_list_p.add_argument("--db", help=_DEFAULT_DB_HELP)

    tags_rename_p = tags_sub.add_parser("rename", help="Rename a tag across all images")
    tags_rename_p.add_argument("old_tag", help="Tag to rename")
    tags_rename_p.add_argument("new_tag", help="Replacement tag")
    tags_rename_p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    tags_rename_p.add_argument("--db", help=_DEFAULT_DB_HELP)

    tags_delete_p = tags_sub.add_parser("delete", help="Delete a tag from all images")
    tags_delete_p.add_argument("tag", help="Tag to delete")
    tags_delete_p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    tags_delete_p.add_argument("--db", help=_DEFAULT_DB_HELP)

    tags_merge_p = tags_sub.add_parser(
        "merge", help="Merge source tag into target tag across all images"
    )
    tags_merge_p.add_argument("source_tag", help="Tag to replace")
    tags_merge_p.add_argument("target_tag", help="Tag to add (source is removed)")
    tags_merge_p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    tags_merge_p.add_argument("--db", help=_DEFAULT_DB_HELP)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyimgtag",
        description="Tag images using a local Ollama Gemma vision model "
        "with EXIF GPS reverse geocoding.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = p.add_subparsers(dest="subcommand")

    _add_run_subcommand(subparsers)
    _add_status_reprocess_preflight_subcommands(subparsers)
    _add_cleanup_subcommands(subparsers)
    _add_review_subcommand(subparsers)
    _add_faces_subcommand(subparsers)
    _add_query_subcommand(subparsers)
    _add_judge_subcommand(subparsers)
    _add_tags_subcommand(subparsers)

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
    """Parse argv, dispatch to the selected subcommand, and return its exit code.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]`` when ``None``.

    Returns:
        Process exit code; 1 when no subcommand or an unknown one is given,
        otherwise the handler's exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand is None:
        parser.print_help()
        return 1

    _check_for_update()

    from pyimgtag.commands.cleanup_drift import cmd_cleanup_drift
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
        "cleanup-drift": lambda: cmd_cleanup_drift(args),
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
