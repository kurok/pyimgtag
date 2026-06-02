"""Handler for the ``cleanup-drift`` subcommand."""

from __future__ import annotations

import argparse
import sys

from pyimgtag.cleanup_drift import prune_drift, scan_drift
from pyimgtag.progress_db import ProgressDB


def _emit(message: str) -> None:
    """Print progress to stderr so stdout stays a clean machine-readable summary."""
    print(message, file=sys.stderr, flush=True)


def cmd_cleanup_drift(args: argparse.Namespace) -> int:
    """List or prune ``processed_images`` rows whose backing file is gone.

    ``--dry-run`` (the default) only reports counts; ``--prune`` deletes
    the dead rows. The summary line at the end mirrors the wording used
    by the Edit panel so script wrappers can pin one shape:
    ``N rows in DB · K with missing file · L deleted`` (or
    ``would delete``).

    Returns:
        Always ``0``; a degraded Photos.app probe
        (``report.photos_probe_error`` set) is reported as a note on
        stderr, not signalled via the exit code.
    """
    # ``--dry-run`` is the default. ``--prune`` deletes only ``disk_missing``
    # rows — the file is genuinely gone, so removing the row is always safe.
    # ``photos_missing`` ("on disk but Apple Photos doesn't index it") is a soft
    # signal that can be wrong (filename-spelling/HEIC↔JPEG differences, or a
    # partial Photos enumeration that silently skips items) and pruning it has
    # deleted nearly-whole DBs, so it requires the explicit
    # ``--prune-photos-missing`` opt-in.
    do_prune_photos = bool(getattr(args, "prune_photos_missing", False))
    do_prune = bool(getattr(args, "prune", False)) or do_prune_photos

    with ProgressDB(db_path=args.db) as db:
        report = scan_drift(db, progress=_emit)

        for sample in report.sample():
            print(sample)

        deleted = 0
        if do_prune:
            deleted += prune_drift(db, report.disk_missing_paths)
            if do_prune_photos:
                deleted += prune_drift(db, report.photos_missing_paths)

    if do_prune:
        verb = "deleted"
        deleted_count = deleted
    else:
        # A dry run reports what the default ``--prune`` would remove.
        verb = "would delete"
        deleted_count = report.disk_missing
    summary = (
        f"{report.total} rows in DB · "
        f"{report.dead_count} with missing file "
        f"(disk_missing={report.disk_missing}, "
        f"photos_missing={report.photos_missing}) · "
        f"{deleted_count} {verb}"
    )
    print(summary)
    if report.photos_probe_error is not None:
        _emit(
            f"note: Photos.app probe degraded ({report.photos_probe_error}); "
            "only disk_missing rows are detectable in this run."
        )
    if report.photos_missing and not do_prune_photos:
        _emit(
            f"note: {report.photos_missing} photos_missing row(s) left untouched; "
            "pass --prune-photos-missing to also delete rows whose file is on disk "
            "but absent from Apple Photos (verify the Photos probe first — a degraded "
            "or partial probe can flag live photos)."
        )
    return 0
