"""Live run dashboard FastAPI app."""

from __future__ import annotations

from typing import Any

from pyimgtag.run_registry import get_current

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag dashboard</title>
  <style>
    :root{--bg:#121212;--surface:#1e1e1e;--card:#252525;--accent:#bb86fc;
          --danger:#cf6679;--warn:#f9a825;--ok:#81c784;--text:#e0e0e0;
          --muted:#888;--border:#333}
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);
         color:var(--text);min-height:100vh;padding:1.5rem}
    header{display:flex;align-items:center;gap:1rem;margin-bottom:1rem}
    h1{color:var(--accent);font-size:1.1rem}
    .pill{padding:.2rem .6rem;border-radius:999px;font-size:.75rem;
          background:var(--surface);border:1px solid var(--border)}
    .pill.running{color:var(--ok);border-color:var(--ok)}
    .pill.pausing,.pill.paused{color:var(--warn);border-color:var(--warn)}
    .pill.failed,.pill.interrupted{color:var(--danger);border-color:var(--danger)}
    button{padding:.35rem .8rem;background:transparent;color:var(--text);
           border:1px solid var(--border);border-radius:4px;cursor:pointer;
           font-size:.8rem}
    button:hover{border-color:var(--accent);color:var(--accent)}
    button:disabled{opacity:.4;cursor:not-allowed}
    .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));
          gap:.5rem;margin:1rem 0}
    .card{background:var(--card);border:1px solid var(--border);border-radius:6px;
          padding:.6rem}
    .card .k{font-size:.7rem;color:var(--muted);text-transform:uppercase}
    .card .v{font-size:1.3rem;margin-top:.2rem}
    #current{font-family:ui-monospace,monospace;font-size:.8rem;color:var(--muted);
             word-break:break-all;margin:.5rem 0}
    #recent{margin-top:1rem;list-style:none;border-top:1px solid var(--border)}
    #recent li{font-size:.75rem;padding:.35rem 0;border-bottom:1px solid var(--border);
               display:flex;gap:.5rem}
    #recent li .s{font-weight:600;text-transform:uppercase;font-size:.65rem}
    #recent li .s.ok{color:var(--ok)}
    #recent li .s.error{color:var(--danger)}
    #recent li .p{font-family:ui-monospace,monospace;color:var(--muted);
                  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
    #error{color:var(--danger);font-size:.8rem;margin-top:.5rem}
    .muted{color:var(--muted);font-size:.8rem}
  </style>
</head>
<body>
  <header>
    <h1>pyimgtag</h1>
    <span id="state" class="pill">idle</span>
    <span id="command" class="muted"></span>
    <div style="flex:1"></div>
    <button id="pauseBtn">Pause</button>
    <button id="unpauseBtn" disabled>Unpause</button>
  </header>

  <div id="current">(no active item)</div>
  <div id="counters" class="grid"></div>
  <div id="error"></div>

  <h2 class="muted" style="margin-top:1rem;font-size:.8rem;text-transform:uppercase">Recent</h2>
  <ul id="recent"></ul>

  <script>
    const stateEl = document.getElementById('state');
    const cmdEl = document.getElementById('command');
    const currentEl = document.getElementById('current');
    const countersEl = document.getElementById('counters');
    const recentEl = document.getElementById('recent');
    const errorEl = document.getElementById('error');
    const pauseBtn = document.getElementById('pauseBtn');
    const unpauseBtn = document.getElementById('unpauseBtn');

    async function fetchSnapshot() {
      try {
        const r = await fetch('/api/run/current');
        if (!r.ok) return;
        render(await r.json());
      } catch (e) { /* transient */ }
    }

    function render(snap) {
      if (!snap.active) {
        stateEl.textContent = 'no active run';
        stateEl.className = 'pill';
        cmdEl.textContent = '';
        currentEl.textContent = '(no active run)';
        countersEl.innerHTML = '';
        recentEl.innerHTML = '';
        errorEl.textContent = '';
        pauseBtn.disabled = true;
        unpauseBtn.disabled = true;
        return;
      }
      stateEl.textContent = snap.state;
      stateEl.className = 'pill ' + snap.state;
      cmdEl.textContent = snap.command + ' · ' + snap.run_id;
      currentEl.textContent = snap.current_item || '(idle between items)';
      countersEl.innerHTML = Object.entries(snap.counters || {})
        .map(([k, v]) => `<div class="card"><div class="k">${k}</div>`
                       + `<div class="v">${v}</div></div>`)
        .join('');
      recentEl.innerHTML = (snap.recent || []).slice().reverse()
        .map(e => `<li><span class="s ${e.status}">${e.status}</span>`
                + `<span class="p">${e.path}</span></li>`)
        .join('');
      errorEl.textContent = snap.last_error ? 'last error: ' + snap.last_error : '';
      pauseBtn.disabled = !(snap.state === 'running' || snap.state === 'starting');
      unpauseBtn.disabled = !(snap.state === 'paused' || snap.state === 'pausing');
    }

    async function post(path) {
      await fetch(path, { method: 'POST' });
      fetchSnapshot();
    }
    pauseBtn.addEventListener('click', () => post('/api/run/current/pause'));
    unpauseBtn.addEventListener('click', () => post('/api/run/current/unpause'));

    fetchSnapshot();
    setInterval(fetchSnapshot, 1500);
  </script>
</body>
</html>
"""


def create_app() -> Any:
    """Return the dashboard FastAPI app.

    Raises:
        ImportError: If ``fastapi`` / ``uvicorn`` are not installed.
    """
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse
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

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _HTML

    @app.get("/api/run/current")
    async def current_run() -> dict:
        session = get_current()
        if session is None:
            return {"active": False}
        return {"active": True, **session.snapshot()}

    @app.post("/api/run/current/pause")
    async def pause_current() -> dict:
        from fastapi import HTTPException

        session = get_current()
        if session is None:
            raise HTTPException(status_code=404, detail="no active run")
        session.request_pause()
        return session.snapshot()

    @app.post("/api/run/current/unpause")
    async def unpause_current() -> dict:
        from fastapi import HTTPException

        session = get_current()
        if session is None:
            raise HTTPException(status_code=404, detail="no active run")
        session.resume()
        return session.snapshot()

    return app
