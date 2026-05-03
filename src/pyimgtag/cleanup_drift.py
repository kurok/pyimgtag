"""DB drift cleanup: detect ``processed_images`` rows whose backing file is gone.

The CLI and the Edit web page both use this module:

- :func:`scan_drift` walks every row in the progress DB and classifies
  each one as ``present`` / ``disk_missing`` / ``photos_missing``.
  - ``present``: file exists on disk **and** Photos.app has a media
    item with this UUID/filename. On non-macOS hosts (or when
    ``osascript`` can't return the membership map) every on-disk row
    collapses into ``present`` because Photos membership cannot be
    probed.
  - ``disk_missing``: file is gone from the local filesystem. The
    safest signal — no Photos lookup is needed.
  - ``photos_missing``: file is on disk but Photos.app does not index
    a media item with this UUID/filename. The DB row is stale even
    though the bytes still exist.

- :func:`prune_drift` deletes the dead rows in batches (``executemany``
  inside :meth:`pyimgtag.progress_db.ProgressDB.delete_image_rows`).

The scan is intentionally cheap: it never reads file contents, only
``Path.is_file()``. The Photos.app membership probe is one bulk
AppleScript call (see
:func:`pyimgtag.applescript_writer.fetch_photos_membership`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from pyimgtag.applescript_writer import _looks_like_uuid, fetch_photos_membership

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)

# How often the scanner emits a heartbeat line. Mirrors the
# ``photos_faces_importer`` cadence so the user sees a familiar pulse.
_PROGRESS_EVERY = 200

# Sample size returned by the web API panel — small enough to render
# inline, large enough to spot-check the upcoming prune.
DRIFT_SAMPLE_SIZE = 20

# Categories the scanner emits. Keep these as plain string literals so
# both the CLI summary and the JS panel can pin them without importing
# this module.
CAT_PRESENT = "present"
CAT_DISK_MISSING = "disk_missing"
CAT_PHOTOS_MISSING = "photos_missing"


@dataclass
class DriftReport:
    """Summary of a drift scan over the progress DB.

    ``dead_paths`` holds every row classified as either
    ``disk_missing`` or ``photos_missing`` — these are the rows the
    prune step deletes. The CLI prints a human-readable summary; the
    web API serialises a clipped sample for the panel.
    """

    total: int = 0
    disk_missing: int = 0
    photos_missing: int = 0
    present: int = 0
    photos_probe_error: str | None = None
    dead_paths: list[str] = field(default_factory=list)

    @property
    def dead_count(self) -> int:
        return self.disk_missing + self.photos_missing

    def sample(self, n: int = DRIFT_SAMPLE_SIZE) -> list[str]:
        return list(self.dead_paths[:n])


def _classify(
    path: str,
    photos_membership: set[str] | None,
) -> str:
    """Return the drift category for one DB row.

    ``photos_membership`` is the bulk-AppleScript output (a set of
    media-item ids and filenames). When it is ``None`` (non-macOS,
    parse error, etc.) the on-disk presence check is the only signal
    available — every existing file collapses into ``present``.
    """
    p = Path(path)
    try:
        on_disk = p.is_file()
    except OSError:
        # A broken symlink or a permission error makes the file
        # effectively missing. Treat the same as disk_missing rather
        # than raising — the prune step will just remove the dead row.
        on_disk = False
    if not on_disk:
        return CAT_DISK_MISSING

    if photos_membership is None:
        return CAT_PRESENT

    name = PurePosixPath(p.name).name
    stem = PurePosixPath(p.name).stem
    # Photos exposes media item id as either the UUID stem (for items
    # imported into the system library) or a free-form filename for
    # items still on disk. Probe both spellings before declaring the
    # row stale.
    if name in photos_membership:
        return CAT_PRESENT
    if _looks_like_uuid(stem) and stem in photos_membership:
        return CAT_PRESENT
    return CAT_PHOTOS_MISSING


def scan_drift(
    db: ProgressDB,
    *,
    fetch_membership: Callable[[], tuple[set[str], str | None]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> DriftReport:
    """Walk every row and classify it. Pure read; never deletes.

    Args:
        db: Open progress DB.
        fetch_membership: Test seam for the AppleScript bulk probe. Real
            callers leave it ``None`` to use
            :func:`pyimgtag.applescript_writer.fetch_photos_membership`.
            The callback returns ``(membership_set, error_or_none)``.
        progress: Optional callback receiving status strings (banner +
            heartbeat + final summary). When ``None`` no progress is
            emitted.

    Returns:
        :class:`DriftReport` describing the full scan.
    """
    emit = progress if progress is not None else (lambda _msg: None)
    fetcher = fetch_membership if fetch_membership is not None else fetch_photos_membership

    emit("Probing Apple Photos library for media-item ids…")
    membership, probe_error = fetcher()
    if probe_error is not None:
        emit(f"Photos probe unavailable ({probe_error}); falling back to disk-only drift check.")
        # An empty set + no membership signal means we cannot tell
        # photos_missing from present, so collapse them — this matches
        # the CLI's documented behaviour on non-macOS hosts.
        usable: set[str] | None = None
    else:
        emit(f"Photos library exposes {len(membership)} media items.")
        usable = membership

    report = DriftReport(photos_probe_error=probe_error)
    started = time.monotonic()

    for path in db.iter_image_paths():
        report.total += 1
        category = _classify(path, usable)
        if category == CAT_DISK_MISSING:
            report.disk_missing += 1
            report.dead_paths.append(path)
        elif category == CAT_PHOTOS_MISSING:
            report.photos_missing += 1
            report.dead_paths.append(path)
        else:
            report.present += 1

        if report.total % _PROGRESS_EVERY == 0:
            elapsed = int(time.monotonic() - started)
            emit(
                f"\r[drift] scanned {report.total} rows · "
                f"{report.dead_count} dead · elapsed {elapsed}s"
            )

    elapsed = int(time.monotonic() - started)
    emit(f"\r[drift] scanned {report.total} rows · {report.dead_count} dead · elapsed {elapsed}s")
    emit("")
    return report


def prune_drift(
    db: ProgressDB,
    paths: Iterable[str],
    *,
    batch_size: int = 500,
) -> int:
    """Delete every path in *paths* from ``processed_images``.

    Batched ``executemany`` keeps the DB writer single-statement-per-
    batch, which matters when the dead set is in the thousands.

    Returns the number of rows actually deleted (paths that were
    already gone are silently counted as 0).
    """
    deleted = 0
    batch: list[str] = []
    for p in paths:
        batch.append(p)
        if len(batch) >= batch_size:
            deleted += db.delete_image_rows(batch)
            batch = []
    if batch:
        deleted += db.delete_image_rows(batch)
    return deleted
