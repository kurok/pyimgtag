"""Live run dashboard FastAPI app."""

from __future__ import annotations

from typing import Any

from pyimgtag.run_registry import get_current


def _render_html() -> str:
    """Assemble and return the dashboard HTML page."""
    from pyimgtag.webapp.nav import DESIGN_CSS, render_nav

    nav_html = render_nav("dashboard")
    return (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1.0'>"
        "<title>pyimgtag dashboard</title>"
        "<style>"
        + DESIGN_CSS
        + """
.dash-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));
           gap:12px;padding:20px 32px 0}
.pill-state{padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;
            border:1px solid var(--border);background:var(--surface)}
.pill-state.running{color:var(--ok);border-color:var(--ok)}
.pill-state.pausing,.pill-state.paused{color:var(--warn);border-color:var(--warn)}
.pill-state.failed,.pill-state.interrupted{color:var(--danger);border-color:var(--danger)}
.recent-list{list-style:none;margin:0 32px;border-top:1px solid var(--border)}
.recent-li{display:flex;align-items:center;gap:8px;padding:8px 0;
           border-bottom:1px solid var(--border);font-size:12px}
.recent-status{font-weight:700;font-size:10px;text-transform:uppercase;min-width:36px}
.recent-status.ok{color:var(--ok)}.recent-status.error{color:var(--danger)}
.recent-path{font-family:ui-monospace,'SF Mono',monospace;color:var(--muted);
             white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
#current{font-family:ui-monospace,'SF Mono',monospace;font-size:13px;color:var(--muted);
         padding:12px 32px 0;word-break:break-all}
#current a,.recent-path a{color:inherit;text-decoration:none;border-bottom:1px dotted var(--muted)}
#current a:hover,.recent-path a:hover{color:var(--accent);border-bottom-color:var(--accent)}
#error{color:var(--danger);font-size:13px;padding:8px 32px 0}
.ctrl-btn{padding:6px 14px;border-radius:var(--radius-sm);font-size:12px;font-weight:500;
          border:1px solid var(--border);background:var(--surface);color:var(--text);
          cursor:pointer;transition:all .15s}
.ctrl-btn:hover{border-color:var(--accent);color:var(--accent)}
.ctrl-btn:disabled{opacity:.4;cursor:not-allowed}
</style></head><body>"""
        + nav_html
        + """
<div class="page-hdr" style="flex-wrap:wrap;gap:10px">
  <h1 class="page-title">Dashboard</h1>
  <span id="state" class="pill-state">idle</span>
  <span id="command" style="font-size:13px;color:var(--muted)"></span>
  <span style="flex:1"></span>
  <button id="pauseBtn" class="ctrl-btn">Pause</button>
  <button id="unpauseBtn" class="ctrl-btn" disabled>Unpause</button>
</div>
<div id="current">(no active item)</div>
<div class="dash-grid" id="counters"></div>
<div id="error"></div>
<div style="padding:20px 32px 0">
  <p style="font-size:11px;font-weight:600;text-transform:uppercase;
            letter-spacing:.8px;color:var(--muted);margin-bottom:10px">Recent</p>
  <ul id="recent" class="recent-list"></ul>
</div>
<script>
  const stateEl = document.getElementById('state');
  const cmdEl = document.getElementById('command');
  const currentEl = document.getElementById('current');
  const countersEl = document.getElementById('counters');
  const recentEl = document.getElementById('recent');
  const errorEl = document.getElementById('error');
  const pauseBtn = document.getElementById('pauseBtn');
  const unpauseBtn = document.getElementById('unpauseBtn');

  async function refresh() {
    try {
      const r = await fetch('/api/run/current');
      const d = await r.json();
      if (!d.active) {
        stateEl.textContent = 'idle';
        stateEl.className = 'pill-state';
        cmdEl.textContent = '';
        currentEl.textContent = '(no active item)';
        countersEl.innerHTML = '';
        recentEl.innerHTML = '';
        errorEl.textContent = '';
        pauseBtn.disabled = true;
        unpauseBtn.disabled = true;
        return;
      }
      stateEl.textContent = d.state || 'running';
      stateEl.className = 'pill-state ' + (d.state || 'running');
      cmdEl.textContent = d.command || '';
      currentEl.innerHTML = '';
      if (d.current_file) {
        // Click-through into the review page filtered to this single image
        // so the user can inspect the model's output for the file currently
        // being processed.
        const a = document.createElement('a');
        a.href = '/review?file=' + encodeURIComponent(d.current_file);
        a.textContent = d.current_file;
        a.title = 'View result for this image';
        currentEl.appendChild(a);
      } else {
        currentEl.textContent = '(no active item)';
      }
      errorEl.textContent = d.error || '';
      pauseBtn.disabled = d.state !== 'running';
      unpauseBtn.disabled = d.state !== 'paused';
      countersEl.innerHTML = '';
      for (const [k, v] of Object.entries(d.counters || {})) {
        const card = document.createElement('div');
        card.className = 'stat-card';
        const val = document.createElement('div');
        val.className = 'stat-val';
        val.textContent = v;
        const lbl = document.createElement('div');
        lbl.className = 'stat-label';
        lbl.textContent = k;
        card.appendChild(val);
        card.appendChild(lbl);
        countersEl.appendChild(card);
      }
      recentEl.innerHTML = '';
      for (const item of (d.recent || [])) {
        const li = document.createElement('li');
        li.className = 'recent-li';
        const s = document.createElement('span');
        s.className = 'recent-status ' + (item.status || '');
        s.textContent = item.status || '';
        const p = document.createElement('span');
        p.className = 'recent-path';
        if (item.path) {
          // Click any recent path to jump straight to its review card.
          const a = document.createElement('a');
          a.href = '/review?file=' + encodeURIComponent(item.path);
          a.textContent = item.path;
          a.title = 'View result for this image';
          p.appendChild(a);
        }
        li.appendChild(s);
        li.appendChild(p);
        recentEl.appendChild(li);
      }
    } catch (e) { errorEl.textContent = String(e); }
  }

  pauseBtn.addEventListener('click', async () => {
    await fetch('/api/run/current/pause', {method: 'POST'});
    refresh();
  });
  unpauseBtn.addEventListener('click', async () => {
    await fetch('/api/run/current/unpause', {method: 'POST'});
    refresh();
  });

  refresh();
  setInterval(refresh, 1500);
</script></body></html>"""
    )


def build_dashboard_router() -> Any:
    """Return an APIRouter exposing the dashboard endpoints.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, HTTPException
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi and uvicorn are required for the dashboard. "
            "Install with: pip install 'pyimgtag[review]'"
        ) from exc

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _render_html()

    @router.get("/api/run/current")
    async def current_run() -> dict:
        session = get_current()
        if session is None:
            return {"active": False}
        return {"active": True, **session.snapshot()}

    @router.post("/api/run/current/pause")
    async def pause_current() -> dict:
        session = get_current()
        if session is None:
            raise HTTPException(status_code=404, detail="no active run")
        session.request_pause()
        return session.snapshot()

    @router.post("/api/run/current/unpause")
    async def unpause_current() -> dict:
        session = get_current()
        if session is None:
            raise HTTPException(status_code=404, detail="no active run")
        session.resume()
        return session.snapshot()

    return router


def create_app() -> Any:
    """Return the standalone dashboard FastAPI app.

    Raises:
        ImportError: If ``fastapi`` / ``uvicorn`` are not installed.
    """
    try:
        from fastapi import FastAPI
    except ImportError as exc:
        raise ImportError(
            "fastapi and uvicorn are required for the dashboard. "
            "Install with: pip install 'pyimgtag[review]'"
        ) from exc

    app = FastAPI(
        title="pyimgtag Dashboard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(build_dashboard_router())
    return app
