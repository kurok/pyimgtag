"""Live run dashboard FastAPI app."""

from __future__ import annotations

from typing import Any

from pyimgtag.run_registry import get_current


def _render_html() -> str:
    """Assemble and return the dashboard HTML page."""
    from pyimgtag.webapp.nav import DESIGN_CSS, render_nav
    from pyimgtag.webapp.templating import Markup, render

    return render(
        "dashboard.html",
        design_css=Markup(DESIGN_CSS),
        nav=Markup(render_nav("dashboard")),
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

    @router.post("/api/run/current/stop")
    async def stop_current() -> dict:
        session = get_current()
        if session is None:
            raise HTTPException(status_code=404, detail="no active run")
        session.request_stop()
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
