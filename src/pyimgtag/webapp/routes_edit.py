"""Edit page: bulk-delete photos marked ``cleanup_class='delete'`` from Photos.

The page hosts a single destructive action — ask Apple Photos to delete
every photo whose progress-DB row has ``cleanup_class='delete'``. Photos
moves them to *Recently Deleted* automatically, where they sit
recoverable for 30 days, so the action is reversible. The UI still
demands explicit confirmation (a checkbox the user must tick) before the
button is enabled.

Implementation notes:

- One job at a time. ``_JOB_LOCK`` plus a single module-level ``_JOB``
  reject overlapping ``POST /edit/api/run`` calls with HTTP 400 and a
  stable ``error="job_already_running"`` so the JS can surface a clean
  message.
- Errors from the AppleScript layer are mapped onto a small fixed set of
  category strings (mirroring ``routes_review.open_in_photos`` after
  PR #160). The verbose ``osascript`` stderr is logged server-side and
  never returned to the browser.
- Ordering matters: the row is removed from ``processed_images`` only
  **after** Photos confirms the delete. A failed AppleScript call leaves
  the row alone so the user can retry without losing track.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)

try:
    from pydantic import BaseModel as _BaseModel

    class _RunBody(_BaseModel):
        confirm: bool = False

    class _PruneBody(_BaseModel):
        confirm: bool = False

except ImportError:  # pragma: no cover — exercised in minimal envs only
    _RunBody = None  # type: ignore[assignment,misc]
    _PruneBody = None  # type: ignore[assignment,misc]


# How many recent per-photo events to retain for the live status panel.
_RECENT_LIMIT = 25


@dataclass
class _Job:
    """In-process state for a single Edit run.

    The dataclass is deliberately tiny — just enough fields for the
    ``GET /edit/api/status`` poller to render its panel. Mutated only
    while the job is running; published via the snapshot helper.
    """

    job_id: str
    state: str = "idle"  # idle | running | done | error
    total: int = 0
    done: int = 0
    ok: int = 0
    failed: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    last_error: str | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=_RECENT_LIMIT))


# Module-level singletons. ``check_same_thread=False`` on the underlying
# sqlite connection means the worker thread can hit the DB directly.
_JOB_LOCK = threading.Lock()
_JOB: _Job = _Job(job_id="", state="idle")


def _categorise_applescript_error(err: str) -> str:
    """Collapse a verbose AppleScript error onto a stable category string.

    Mirrors :func:`pyimgtag.webapp.routes_review.open_in_photos` so the
    browser only ever sees a small fixed set of labels and the
    ``osascript`` line/column references stay server-side. The
    UI-scripting path that performs the actual delete (System Events
    keystroke into Photos.app) introduces a new failure mode the
    reveal/write paths don't see: Accessibility permission denied —
    surfaced as ``-1719`` / ``-25204`` from System Events.
    """
    low = err.lower()
    if "macos" in low:
        return "platform_unsupported"
    if "timed out" in low or "timeout" in low:
        return "photos_timeout"
    # System Events accessibility-denied must be checked BEFORE the
    # generic ``applescript``/``osascript`` mention because the
    # accessibility-denied stderr always begins with
    # ``AppleScript error …``. Markers we accept: literal AppleEvent
    # codes ``(-1719)`` / ``(-25204)``, the substring "assistive
    # access" (used by System Events' own message), or any explicit
    # mention of accessibility.
    if "(-1719)" in err or "(-25204)" in err or "assistive access" in low or "accessibility" in low:
        return "accessibility_denied"
    # Photo not in Photos.app even though the file sits on disk inside
    # the Photos library bundle (deleted from Photos manually, orphaned
    # original, etc.). ``applescript_writer._filename_scan_block``
    # raises ``error "Photo not found: <name>"`` (-2700). Surface that
    # as its own category so the dashboard can render "Photos.app
    # doesn't have this image" rather than the misleading
    # ``photos_unavailable``. Checked before the generic ``applescript``
    # branch because the stderr begins with ``AppleScript error …``.
    if "photo not found" in low or "(-2700)" in err:
        return "photo_not_in_library"
    if "osascript" in low or "applescript" in low:
        return "photos_unavailable"
    return "photos_error"


def _snapshot(job: _Job) -> dict[str, Any]:
    """Return a JSON-safe snapshot of ``job`` for the status endpoint."""
    return {
        "job_id": job.job_id,
        "state": job.state,
        "total": job.total,
        "done": job.done,
        "ok": job.ok,
        "failed": job.failed,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "last_error": job.last_error,
        "recent": list(job.recent),
    }


def _run_job(db: ProgressDB, job: _Job) -> None:
    """Walk every ``cleanup_class='delete'`` row and ask Photos to delete it.

    Runs in a worker thread. On success, the row is removed from
    ``processed_images``; on any AppleScript error the row stays put so
    the user can retry. Concurrency is enforced by the caller — this
    function assumes it owns ``job`` exclusively.
    """
    # Imported lazily so the module can be imported on non-macOS hosts
    # for testing without dragging Photos.app dependencies along.
    from pyimgtag.applescript_writer import delete_from_photos

    try:
        targets = db.get_images(
            limit=10_000_000,  # effectively "all"
            offset=0,
            cleanup_class="delete",
        )
    except Exception as exc:  # noqa: BLE001 — surface any DB error to the UI
        logger.exception("edit job: failed to load delete targets")
        with _JOB_LOCK:
            job.state = "error"
            job.last_error = "db_error"
            job.finished_at = time.time()
        # Re-raise so a unit test exercising the failure path sees it.
        raise RuntimeError("failed to load delete targets") from exc

    with _JOB_LOCK:
        job.total = len(targets)

    for row in targets:
        path = row["file_path"]
        name = row.get("file_name") or path
        err = delete_from_photos(path)
        if err is None:
            # Delete from DB only after Photos confirms — a failed
            # AppleScript leaves the DB row in place so retrying is safe.
            try:
                db.delete_image(path)
            except Exception:  # noqa: BLE001
                logger.exception("edit job: failed to delete DB row for %s", path)
                with _JOB_LOCK:
                    job.failed += 1
                    job.done += 1
                    job.recent.append({"file_name": name, "status": "error", "error": "db_error"})
                continue
            with _JOB_LOCK:
                job.ok += 1
                job.done += 1
                job.recent.append({"file_name": name, "status": "ok"})
        else:
            logger.warning("edit job: delete_from_photos failed for %s: %s", path, err)
            category = _categorise_applescript_error(err)
            with _JOB_LOCK:
                job.failed += 1
                job.done += 1
                job.last_error = category
                job.recent.append({"file_name": name, "status": "error", "error": category})

    with _JOB_LOCK:
        job.state = "done"
        job.finished_at = time.time()


def _run_drift_prune_job(db: ProgressDB, job: _Job) -> None:
    """Scan the DB for stale rows and delete the ones with missing files.

    Reuses the same ``_Job`` shape as the delete-from-Photos worker so
    the existing status poller renders this run with no extra glue —
    ``total`` is set to the ``disk_missing`` row count (the only category
    the web action prunes), ``done`` is set once after the single prune
    batch completes, and ``recent`` records up to ``_RECENT_LIMIT``
    pruned paths.

    Errors from the AppleScript probe are surfaced as a job-level
    ``last_error`` (categorised by :func:`_categorise_applescript_error`
    when applicable) but never abort the run: the disk-only fallback
    still removes every row whose backing file is gone.
    """
    from pyimgtag.cleanup_drift import prune_drift, scan_drift

    try:
        report = scan_drift(db)
    except Exception as exc:  # noqa: BLE001 — surface to the UI as job error
        logger.exception("drift job: scan failed")
        with _JOB_LOCK:
            job.state = "error"
            job.last_error = "scan_failed"
            job.finished_at = time.time()
        raise RuntimeError("drift scan failed") from exc

    # Only prune ``disk_missing`` rows (the file is genuinely gone). The
    # ``photos_missing`` category is a soft signal prone to false positives
    # (filename spelling, HEIC↔JPEG, or a partial Photos enumeration) and
    # pruning it from a one-click web action has wiped nearly-whole DBs, so it
    # is deliberately left for the explicit ``--prune-photos-missing`` CLI flag.
    prune_paths = report.disk_missing_paths
    with _JOB_LOCK:
        job.total = len(prune_paths)
        if report.photos_probe_error is not None:
            # The AppleScript probe degraded — surface the category but
            # keep going. ``photos_missing`` and ``present`` collapse,
            # so only ``disk_missing`` rows will actually get pruned.
            job.last_error = report.photos_probe_error

    if not prune_paths:
        with _JOB_LOCK:
            job.state = "done"
            job.finished_at = time.time()
        return

    try:
        deleted = prune_drift(db, prune_paths)
    except Exception as exc:  # noqa: BLE001 — propagate as job error
        logger.exception("drift job: prune failed")
        with _JOB_LOCK:
            job.state = "error"
            job.last_error = "prune_failed"
            job.finished_at = time.time()
        raise RuntimeError("drift prune failed") from exc

    with _JOB_LOCK:
        job.ok = deleted
        job.done = len(prune_paths)
        # Keep the deleted-row sample short so the events panel stays
        # readable on a 22 k-photo library.
        for path in prune_paths[:_RECENT_LIMIT]:
            job.recent.append({"file_name": path, "status": "ok"})
        job.state = "done"
        job.finished_at = time.time()


def render_edit_html(api_base: str = "") -> str:
    """Return the Edit UI HTML with the given API base prefix."""
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav
    from pyimgtag.webapp.templating import Markup, render

    return render(
        "edit.html",
        api_base=Markup(api_base),
        nav=Markup(render_nav("edit")),
        nav_styles=Markup(NAV_STYLES),
    )


def build_edit_router(db: ProgressDB, api_base: str = "") -> Any:
    """Build and return a FastAPI APIRouter exposing the Edit page + API.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into the rendered HTML (e.g. ``"/edit"``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, Body
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:  # pragma: no cover — same guard as siblings
        raise ImportError(
            "fastapi and uvicorn are required for the edit UI. "
            "Install with: pip install 'pyimgtag[review]'"
        ) from exc

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_edit_html(api_base)

    @router.get("/api/marked")
    async def get_marked() -> dict:
        """Return the count of cleanup=delete rows + a small filename sample.

        The sample (first 20 file_names by path) is rendered in the
        confirm card so the user can scan what's about to happen before
        ticking the checkbox.
        """
        total = db.count_images(cleanup_class="delete")
        rows = db.get_images(limit=20, offset=0, cleanup_class="delete")
        sample = [r.get("file_name") or r["file_path"] for r in rows]
        return {"count": total, "sample": sample}

    @router.post("/api/run")
    async def run_job(body: _RunBody = Body(...)) -> Any:
        """Spawn the background delete job. One job at a time.

        Rejects overlapping calls with HTTP 400 and a stable
        ``error="job_already_running"`` so the JS can show a clean
        message without parsing free-form text. Missing / false
        confirmation is rejected the same way.
        """
        if not body.confirm:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "confirmation_required"},
            )

        global _JOB
        with _JOB_LOCK:
            if _JOB.state == "running":
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": "job_already_running"},
                )
            new_job = _Job(
                job_id=uuid.uuid4().hex,
                state="running",
                started_at=time.time(),
            )
            _JOB = new_job

        def _runner() -> None:
            try:
                _run_job(db, new_job)
            except Exception:  # noqa: BLE001,S110 — already logged inside _run_job
                # _run_job marks the job state on failure; nothing else to do.
                # The verbose error has already been written via logger.exception
                # so swallowing here only prevents an "unhandled exception in
                # thread" stderr noise on top of the structured log entry.
                logger.debug("edit job: worker exited with handled exception")

        threading.Thread(target=_runner, name="pyimgtag-edit-job", daemon=True).start()
        return {"ok": True, "job_id": new_job.job_id}

    @router.get("/api/status")
    async def get_status() -> dict:
        """Return a JSON snapshot of the current (or most recent) job."""
        with _JOB_LOCK:
            return _snapshot(_JOB)

    @router.get("/api/drift")
    async def get_drift() -> dict:
        """Summarise stale ``processed_images`` rows for the panel.

        Runs the full drift scan synchronously — the panel pulls counts
        + a 20-row sample on page load. The bulk Photos.app probe is
        capped server-side so a single slow library call cannot hang
        the request indefinitely. ``photos_probe_error`` is forwarded
        unchanged so the UI can hint when the macOS-only signal
        degraded.
        """
        from pyimgtag.cleanup_drift import DRIFT_SAMPLE_SIZE, scan_drift

        report = scan_drift(db)
        return {
            "total": report.total,
            "disk_missing": report.disk_missing,
            "photos_missing": report.photos_missing,
            "sample": report.sample(DRIFT_SAMPLE_SIZE),
            "photos_probe_error": report.photos_probe_error,
        }

    @router.post("/api/prune-drift")
    async def prune_drift_job(body: _PruneBody = Body(...)) -> Any:
        """Spawn the background drift-prune job. Shares the edit-job lock.

        The drift prune walks the same ``_JOB`` singleton + ``_JOB_LOCK``
        as the delete-from-Photos worker so the two destructive actions
        cannot run at the same time. Mirrors the response shape of
        ``POST /edit/api/run`` for the JS — ``{ok, job_id}`` on success,
        HTTP 400 + ``error="job_already_running"`` on overlap.
        """
        if not body.confirm:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "confirmation_required"},
            )

        global _JOB
        with _JOB_LOCK:
            if _JOB.state == "running":
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": "job_already_running"},
                )
            new_job = _Job(
                job_id=uuid.uuid4().hex,
                state="running",
                started_at=time.time(),
            )
            _JOB = new_job

        def _runner() -> None:
            try:
                _run_drift_prune_job(db, new_job)
            except Exception:  # noqa: BLE001 — already logged inside the worker
                logger.debug("drift job: worker exited with handled exception")

        threading.Thread(target=_runner, name="pyimgtag-drift-prune-job", daemon=True).start()
        return {"ok": True, "job_id": new_job.job_id}

    return router


def _reset_job_for_tests() -> None:
    """Reset the module-level job state. Test-only helper.

    The Edit job is a process-wide singleton, so every test that touches
    ``POST /edit/api/run`` must reset the lock or risk leaking state into
    the next test (xdist runs each module in its own worker, but tests
    inside a worker still share state).
    """
    global _JOB
    with _JOB_LOCK:
        _JOB = _Job(job_id="", state="idle")
