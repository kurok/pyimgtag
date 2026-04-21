"""Live run dashboard FastAPI app."""

from __future__ import annotations

from typing import Any

from pyimgtag.run_registry import get_current

_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>pyimgtag dashboard</title></head>
<body><h1>pyimgtag</h1><p>Loading…</p></body></html>
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

    return app
