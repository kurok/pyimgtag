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
    """
    # ``--dry-run`` is the default behaviour. ``--prune`` is the only
    # way to actually delete rows; the mutex group guards against
    # ``--dry-run --prune`` being passed together.
    do_prune = bool(getattr(args, "prune", False))

    with ProgressDB(db_path=args.db) as db:
        report = scan_drift(db, progress=_emit)

        for sample in report.sample():
            print(sample)

        deleted = prune_drift(db, report.dead_paths) if do_prune else 0

    verb = "deleted" if do_prune else "would delete"
    deleted_count = deleted if do_prune else report.dead_count
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
    return 0
