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
    ``total`` is set to the dead-row count, ``done`` increments per
    deleted batch, and ``recent`` records each batch as a single event.

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

    with _JOB_LOCK:
        job.total = report.dead_count
        if report.photos_probe_error is not None:
            # The AppleScript probe degraded — surface the category but
            # keep going. ``photos_missing`` and ``present`` collapse,
            # so only ``disk_missing`` rows will actually get pruned.
            job.last_error = report.photos_probe_error

    if not report.dead_paths:
        with _JOB_LOCK:
            job.state = "done"
            job.finished_at = time.time()
        return

    try:
        deleted = prune_drift(db, report.dead_paths)
    except Exception as exc:  # noqa: BLE001 — propagate as job error
        logger.exception("drift job: prune failed")
        with _JOB_LOCK:
            job.state = "error"
            job.last_error = "prune_failed"
            job.finished_at = time.time()
        raise RuntimeError("drift prune failed") from exc

    with _JOB_LOCK:
        job.ok = deleted
        job.done = report.dead_count
        # Keep the deleted-row sample short so the events panel stays
        # readable on a 22 k-photo library.
        for path in report.dead_paths[:_RECENT_LIMIT]:
            job.recent.append({"file_name": path, "status": "ok"})
        job.state = "done"
        job.finished_at = time.time()


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag Edit</title>
  <style>
    __NAV_STYLES__
    .edit-wrap{max-width:880px;margin:24px auto;padding:0 24px}
    .edit-card{background:var(--surface);border-radius:var(--radius-md);
               box-shadow:var(--shadow-md);padding:22px 24px;margin-bottom:18px}
    .edit-card h2{font-size:16px;font-weight:600;margin-bottom:6px;
                  letter-spacing:-.2px}
    .edit-card p{font-size:13px;line-height:1.55;color:var(--muted);
                 margin-bottom:6px}
    .summary-num{font-size:42px;font-weight:700;letter-spacing:-1px;
                 color:var(--text);margin:6px 0 2px}
    .summary-num.zero{color:var(--muted)}
    .danger-note{padding:10px 14px;background:rgba(255,59,48,.08);
                 border:1px solid rgba(255,59,48,.18);border-radius:8px;
                 font-size:12.5px;color:var(--text);line-height:1.5;
                 margin:14px 0}
    .danger-note strong{color:var(--danger)}
    .confirm-row{display:flex;align-items:center;gap:10px;margin:14px 0 6px;
                 font-size:13px;color:var(--text)}
    .confirm-row input[type=checkbox]{transform:scale(1.15)}
    .btn-danger{background:var(--danger);color:#fff}
    .btn-danger:hover{background:#e0342a}
    .btn-danger:disabled{background:#ffb6b1;cursor:not-allowed}
    .sample-list{margin:6px 0 0;padding:0;list-style:none;font-size:12px;
                 color:var(--muted);max-height:160px;overflow-y:auto;
                 font-family:ui-monospace,'SF Mono',monospace;line-height:1.55}
    .sample-list li{padding:1px 0;
                    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .progress-row{display:flex;align-items:center;gap:14px;margin:8px 0 12px;
                  font-size:13px}
    .progress-bar-bg{flex:1;height:8px;background:rgba(0,0,0,.06);
                     border-radius:4px;overflow:hidden}
    .progress-bar-fill{height:100%;background:var(--accent);
                       transition:width .25s}
    .progress-label{font-family:ui-monospace,'SF Mono',monospace;
                    font-size:12px;color:var(--muted);min-width:80px;
                    text-align:right}
    .events-list{margin:0;padding:0;list-style:none;max-height:280px;
                 overflow-y:auto;border-top:1px solid var(--border)}
    .events-list li{display:flex;gap:10px;align-items:center;padding:7px 0;
                    border-bottom:1px solid var(--border);font-size:12px}
    .ev-status{font-weight:700;font-size:10px;text-transform:uppercase;
               min-width:48px}
    .ev-status.ok{color:var(--ok)}
    .ev-status.error{color:var(--danger)}
    .ev-name{font-family:ui-monospace,'SF Mono',monospace;font-size:12px;
             color:var(--text);flex:1;white-space:nowrap;overflow:hidden;
             text-overflow:ellipsis}
    .ev-err{font-size:11px;color:var(--danger);
            font-family:ui-monospace,'SF Mono',monospace}
    .state-pill{display:inline-block;padding:3px 10px;border-radius:12px;
                font-size:11px;font-weight:600;letter-spacing:.4px;
                text-transform:uppercase;border:1px solid var(--border);
                background:var(--surface);color:var(--muted);
                margin-left:8px;vertical-align:middle}
    .state-pill.running{color:var(--accent);border-color:var(--accent)}
    .state-pill.done{color:var(--ok);border-color:var(--ok)}
    .state-pill.error{color:var(--danger);border-color:var(--danger)}
  </style>
</head>
<body>
__NAV__
<div class="edit-wrap">
  <h1 class="page-title" style="padding:8px 0 4px">Edit</h1>
  <p class="page-meta" style="padding:0 0 12px">
    Apply destructive actions queued in the progress DB.
  </p>

  <div class="edit-card">
    <h2>Delete photos marked for cleanup</h2>
    <p>The summary below counts every row in this DB whose
       <code>cleanup_class</code> is <code>delete</code>. Running the action
       sends each one to Apple Photos for deletion.</p>
    <div class="summary-num zero" id="markedCount">…</div>
    <p id="markedLabel">photos in this DB are marked <code>delete</code>.</p>

    <ul class="sample-list" id="sampleList"></ul>

    <div class="danger-note">
      <strong>Recoverable for 30 days.</strong> Photos.app moves deleted
      items to <em>Recently Deleted</em>; this page never empties that
      bin. On a successful Photos delete, the matching row is also
      removed from the progress DB so a re-scan won't re-process the
      file.
    </div>

    <div class="confirm-row">
      <input type="checkbox" id="confirmChk" onchange="updateRunButton()">
      <label for="confirmChk">I understand and want to delete these photos.</label>
    </div>
    <button class="btn btn-danger" id="runBtn" disabled onclick="runJob()">
      Delete <span id="runBtnCount">0</span> from Photos
    </button>
  </div>

  <div class="edit-card">
    <h2>DB drift</h2>
    <p>Compares every row in the progress DB against the file on disk
       and (on macOS) Apple Photos.app's media-item set. Rows whose
       backing file is gone become safe to prune.</p>
    <div class="summary-num zero" id="driftDeadCount">…</div>
    <p id="driftLabel">stale rows: <span id="driftDiskMissing">0</span> file
       missing on disk · <span id="driftPhotosMissing">0</span> not in Photos.app
       (of <span id="driftTotal">0</span> total).</p>

    <ul class="sample-list" id="driftSample"></ul>

    <div class="danger-note">
       <strong>DB-only delete.</strong> This action only removes rows
       from <code>processed_images</code>; the photos themselves are
       already gone (or no longer indexed by Photos.app). A re-scan
       will re-process anything still present.
    </div>

    <div class="confirm-row">
      <input type="checkbox" id="driftConfirmChk" onchange="updatePruneButton()">
      <label for="driftConfirmChk">I understand and want to prune these rows.</label>
    </div>
    <button class="btn btn-danger" id="pruneBtn" disabled onclick="pruneDrift()">
      Prune <span id="pruneBtnCount">0</span> stale rows
    </button>
  </div>

  <div class="edit-card">
    <h2>Status<span class="state-pill" id="statePill">idle</span></h2>
    <div class="progress-row">
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" id="progressFill" style="width:0"></div>
      </div>
      <span class="progress-label" id="progressLabel">0 / 0</span>
    </div>
    <p style="font-size:12px;color:var(--muted);margin-bottom:8px"
       id="finalSummary"></p>
    <ul class="events-list" id="eventsList"></ul>
  </div>
</div>
<script>
let _markedCount = 0;
let _driftCount = 0;
let _polling = false;
let _pollHandle = null;

async function loadMarked() {
  try {
    const r = await fetch('__API_BASE__/api/marked');
    const d = await r.json();
    _markedCount = d.count || 0;
    const numEl = document.getElementById('markedCount');
    numEl.textContent = _markedCount;
    if (_markedCount === 0) numEl.classList.add('zero');
    else numEl.classList.remove('zero');
    document.getElementById('runBtnCount').textContent = _markedCount;
    const list = document.getElementById('sampleList');
    list.innerHTML = '';
    for (const name of (d.sample || [])) {
      const li = document.createElement('li');
      li.textContent = name;
      list.appendChild(li);
    }
    if ((d.sample || []).length < _markedCount) {
      const li = document.createElement('li');
      li.style.color = 'var(--muted)';
      li.textContent = '... and ' + (_markedCount - d.sample.length) + ' more';
      list.appendChild(li);
    }
    updateRunButton();
  } catch (e) { /* leave the placeholder */ }
}

function updateRunButton() {
  const btn = document.getElementById('runBtn');
  const chk = document.getElementById('confirmChk');
  btn.disabled = !(chk.checked && _markedCount > 0 && !_polling);
}

async function loadDrift() {
  try {
    const r = await fetch('__API_BASE__/api/drift');
    const d = await r.json();
    _driftCount = d.disk_missing + d.photos_missing;
    document.getElementById('driftDeadCount').textContent = _driftCount;
    document.getElementById('driftDiskMissing').textContent = d.disk_missing;
    document.getElementById('driftPhotosMissing').textContent = d.photos_missing;
    document.getElementById('driftTotal').textContent = d.total;
    document.getElementById('pruneBtnCount').textContent = _driftCount;
    const numEl = document.getElementById('driftDeadCount');
    if (_driftCount === 0) numEl.classList.add('zero');
    else numEl.classList.remove('zero');
    const list = document.getElementById('driftSample');
    list.innerHTML = '';
    for (const path of (d.sample || [])) {
      const li = document.createElement('li');
      li.textContent = path;
      list.appendChild(li);
    }
    if ((d.sample || []).length < _driftCount) {
      const li = document.createElement('li');
      li.style.color = 'var(--muted)';
      li.textContent = '... and ' + (_driftCount - d.sample.length) + ' more';
      list.appendChild(li);
    }
    updatePruneButton();
  } catch (e) { /* leave the placeholder */ }
}

function updatePruneButton() {
  const btn = document.getElementById('pruneBtn');
  const chk = document.getElementById('driftConfirmChk');
  btn.disabled = !(chk.checked && _driftCount > 0 && !_polling);
}

async function pruneDrift() {
  const btn = document.getElementById('pruneBtn');
  btn.disabled = true;
  document.getElementById('finalSummary').textContent = '';
  let r;
  try {
    r = await fetch('__API_BASE__/api/prune-drift', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirm: true}),
    });
  } catch (e) {
    alert('Failed to start: ' + e);
    updatePruneButton();
    return;
  }
  if (!r.ok) {
    let err = 'unknown_error';
    try { err = (await r.json()).error || err; } catch (_) {}
    alert('Failed to start: ' + err);
    updatePruneButton();
    return;
  }
  startPolling();
}

async function runJob() {
  const btn = document.getElementById('runBtn');
  btn.disabled = true;
  document.getElementById('finalSummary').textContent = '';
  let r;
  try {
    r = await fetch('__API_BASE__/api/run', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirm: true}),
    });
  } catch (e) {
    alert('Failed to start: ' + e);
    updateRunButton();
    return;
  }
  if (!r.ok) {
    let err = 'unknown_error';
    try { err = (await r.json()).error || err; } catch (_) {}
    alert('Failed to start: ' + err);
    updateRunButton();
    return;
  }
  startPolling();
}

function startPolling() {
  _polling = true;
  document.getElementById('confirmChk').checked = false;
  document.getElementById('driftConfirmChk').checked = false;
  if (_pollHandle) clearInterval(_pollHandle);
  // 1 s cadence — the loop is a thin DB-row-by-row walk and the user
  // wants visible per-photo progress without spamming the server.
  _pollHandle = setInterval(pollStatus, 1000);
  pollStatus();
}

async function pollStatus() {
  let d;
  try {
    const r = await fetch('__API_BASE__/api/status');
    d = await r.json();
  } catch (e) { return; }
  const pill = document.getElementById('statePill');
  pill.textContent = d.state;
  pill.className = 'state-pill ' + (d.state || '');
  document.getElementById('progressLabel').textContent =
    (d.done || 0) + ' / ' + (d.total || 0);
  const pct = d.total ? Math.min(100, Math.round((d.done / d.total) * 100)) : 0;
  document.getElementById('progressFill').style.width = pct + '%';
  const ev = document.getElementById('eventsList');
  ev.innerHTML = '';
  for (const e of (d.recent || []).slice().reverse()) {
    const li = document.createElement('li');
    const st = document.createElement('span');
    st.className = 'ev-status ' + (e.status || '');
    st.textContent = e.status || '';
    const nm = document.createElement('span');
    nm.className = 'ev-name';
    nm.textContent = e.file_name || '';
    li.appendChild(st);
    li.appendChild(nm);
    if (e.error) {
      const er = document.createElement('span');
      er.className = 'ev-err';
      er.textContent = e.error;
      li.appendChild(er);
    }
    ev.appendChild(li);
  }
  if (d.state === 'done' || d.state === 'error') {
    if (_pollHandle) { clearInterval(_pollHandle); _pollHandle = null; }
    _polling = false;
    document.getElementById('finalSummary').textContent =
      'Finished: ' + (d.ok || 0) + ' deleted, ' + (d.failed || 0) + ' failed.';
    loadMarked();
    loadDrift();
  }
}

loadMarked();
loadDrift();
// Pick up an in-flight job if the user navigates back mid-run.
pollStatus();
</script>
</body>
</html>"""


def render_edit_html(api_base: str = "") -> str:
    """Return the Edit UI HTML with the given API base prefix."""
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav

    return (
        _HTML_TEMPLATE.replace("__API_BASE__", api_base)
        .replace("__NAV__", render_nav("edit"))
        .replace("__NAV_STYLES__", NAV_STYLES)
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
