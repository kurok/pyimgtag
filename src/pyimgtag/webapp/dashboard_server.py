"""Live run dashboard FastAPI app."""

from __future__ import annotations

from typing import Any

from pyimgtag.run_registry import get_current
from pyimgtag.webapp.nav import NAV_STYLES, render_nav


def _render_html() -> str:
    """Assemble and return the dashboard HTML page."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        "  <title>pyimgtag dashboard</title>\n"
        "  <style>\n"
        "    :root{--bg:#121212;--surface:#1e1e1e;--card:#252525;--accent:#bb86fc;\n"
        "          --danger:#cf6679;--warn:#f9a825;--ok:#81c784;--text:#e0e0e0;\n"
        "          --muted:#888;--border:#333}\n"
        "    *{box-sizing:border-box;margin:0;padding:0}\n"
        "    body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);\n"
        "         color:var(--text);min-height:100vh}\n"
        "    .page{padding:1.5rem}\n"
        "    header.dash{display:flex;align-items:center;gap:1rem;margin-bottom:1rem}\n"
        "    h1{color:var(--accent);font-size:1.1rem}\n"
        "    .pill{padding:.2rem .6rem;border-radius:999px;font-size:.75rem;\n"
        "          background:var(--surface);border:1px solid var(--border)}\n"
        "    .pill.running{color:var(--ok);border-color:var(--ok)}\n"
        "    .pill.pausing,.pill.paused{color:var(--warn);border-color:var(--warn)}\n"
        "    .pill.failed,.pill.interrupted{color:var(--danger);border-color:var(--danger)}\n"
        "    button{padding:.35rem .8rem;background:transparent;color:var(--text);\n"
        "           border:1px solid var(--border);border-radius:4px;cursor:pointer;\n"
        "           font-size:.8rem}\n"
        "    button:hover{border-color:var(--accent);color:var(--accent)}\n"
        "    button:disabled{opacity:.4;cursor:not-allowed}\n"
        "    .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));\n"
        "          gap:.5rem;margin:1rem 0}\n"
        "    .card{background:var(--card);border:1px solid var(--border);border-radius:6px;\n"
        "          padding:.6rem}\n"
        "    .card .k{font-size:.7rem;color:var(--muted);text-transform:uppercase}\n"
        "    .card .v{font-size:1.3rem;margin-top:.2rem}\n"
        "    #current{font-family:ui-monospace,monospace;font-size:.8rem;color:var(--muted);\n"
        "             word-break:break-all;margin:.5rem 0}\n"
        "    #recent{margin-top:1rem;list-style:none;border-top:1px solid var(--border)}\n"
        "    #recent li{font-size:.75rem;padding:.35rem 0;border-bottom:1px solid var(--border);\n"
        "               display:flex;gap:.5rem}\n"
        "    #recent li .s{font-weight:600;text-transform:uppercase;font-size:.65rem}\n"
        "    #recent li .s.ok{color:var(--ok)}\n"
        "    #recent li .s.error{color:var(--danger)}\n"
        "    #recent li .p{font-family:ui-monospace,monospace;color:var(--muted);\n"
        "                  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}\n"
        "    #error{color:var(--danger);font-size:.8rem;margin-top:.5rem}\n"
        "    .muted{color:var(--muted);font-size:.8rem}\n" + NAV_STYLES + "  </style>\n"
        "</head>\n"
        "<body>\n" + render_nav("dashboard") + "\n"
        '  <div class="page">\n'
        '  <header class="dash">\n'
        "    <h1>pyimgtag</h1>\n"
        '    <span id="state" class="pill">idle</span>\n'
        '    <span id="command" class="muted"></span>\n'
        '    <div style="flex:1"></div>\n'
        '    <button id="pauseBtn">Pause</button>\n'
        '    <button id="unpauseBtn" disabled>Unpause</button>\n'
        "  </header>\n"
        "\n"
        '  <div id="current">(no active item)</div>\n'
        '  <div id="counters" class="grid"></div>\n'
        '  <div id="error"></div>\n'
        "\n"
        '  <h2 class="muted" style="margin-top:1rem;font-size:.8rem;text-transform:uppercase">'
        "Recent</h2>\n"
        '  <ul id="recent"></ul>\n'
        "\n"
        "  <script>\n"
        "    const stateEl = document.getElementById('state');\n"
        "    const cmdEl = document.getElementById('command');\n"
        "    const currentEl = document.getElementById('current');\n"
        "    const countersEl = document.getElementById('counters');\n"
        "    const recentEl = document.getElementById('recent');\n"
        "    const errorEl = document.getElementById('error');\n"
        "    const pauseBtn = document.getElementById('pauseBtn');\n"
        "    const unpauseBtn = document.getElementById('unpauseBtn');\n"
        "\n"
        "    async function fetchSnapshot() {\n"
        "      try {\n"
        "        const r = await fetch('/api/run/current');\n"
        "        if (!r.ok) return;\n"
        "        render(await r.json());\n"
        "      } catch (e) { /* transient */ }\n"
        "    }\n"
        "\n"
        "    function render(snap) {\n"
        "      if (!snap.active) {\n"
        "        stateEl.textContent = 'no active run';\n"
        "        stateEl.className = 'pill';\n"
        "        cmdEl.textContent = '';\n"
        "        currentEl.textContent = '(no active run)';\n"
        "        countersEl.innerHTML = '';\n"
        "        recentEl.innerHTML = '';\n"
        "        errorEl.textContent = '';\n"
        "        pauseBtn.disabled = true;\n"
        "        unpauseBtn.disabled = true;\n"
        "        return;\n"
        "      }\n"
        "      stateEl.textContent = snap.state;\n"
        "      stateEl.className = 'pill ' + snap.state;\n"
        "      cmdEl.textContent = snap.command + ' \u00b7 ' + snap.run_id;\n"
        "      currentEl.textContent = snap.current_item || '(idle between items)';\n"
        "      countersEl.innerHTML = Object.entries(snap.counters || {})\n"
        '        .map(([k, v]) => `<div class="card"><div class="k">${k}</div>`\n'
        '                       + `<div class="v">${v}</div></div>`)\n'
        "        .join('');\n"
        "      recentEl.innerHTML = (snap.recent || []).slice().reverse()\n"
        '        .map(e => `<li><span class="s ${e.status}">${e.status}</span>`\n'
        '                + `<span class="p">${e.path}</span></li>`)\n'
        "        .join('');\n"
        "      errorEl.textContent = snap.last_error ? 'last error: ' + snap.last_error : '';\n"
        "      pauseBtn.disabled = !(snap.state === 'running' || snap.state === 'starting');\n"
        "      unpauseBtn.disabled = !(snap.state === 'paused' || snap.state === 'pausing');\n"
        "    }\n"
        "\n"
        "    async function post(path) {\n"
        "      await fetch(path, { method: 'POST' });\n"
        "      fetchSnapshot();\n"
        "    }\n"
        "    pauseBtn.addEventListener('click', () => post('/api/run/current/pause'));\n"
        "    unpauseBtn.addEventListener('click', () => post('/api/run/current/unpause'));\n"
        "\n"
        "    fetchSnapshot();\n"
        "    setInterval(fetchSnapshot, 1500);\n"
        "  </script>\n"
        "  </div>\n"
        "</body>\n"
        "</html>\n"
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
