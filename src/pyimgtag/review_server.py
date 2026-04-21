"""Lightweight review UI server for pyimgtag (FastAPI)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def create_app(db_path: str | Path | None = None) -> Any:
    """Create and return the standalone review FastAPI application.

    Args:
        db_path: Path to the SQLite progress DB. Defaults to the standard location.

    Returns:
        A FastAPI application instance.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import FastAPI
    except ImportError as exc:
        raise ImportError(
            "fastapi and uvicorn are required for the review UI. "
            "Install with: pip install 'pyimgtag[review]'"
        ) from exc

    from pyimgtag.progress_db import ProgressDB
    from pyimgtag.webapp.routes_review import build_review_router

    db = ProgressDB(db_path=db_path)
    app = FastAPI(title="pyimgtag Review", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_review_router(db, api_base=""))
    return app


def serve(
    db_path: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Start the review UI server.

    Args:
        db_path: Path to the SQLite progress DB.
        host: Bind host (default: 127.0.0.1).
        port: Bind port (default: 8765).
        open_browser: Open the default browser automatically.
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError(
            "uvicorn is required for the review UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    app = create_app(db_path=db_path)

    if open_browser:
        import threading
        import time
        import webbrowser

        def _open() -> None:
            time.sleep(1.0)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open, daemon=True).start()

    print(f"Review UI: http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port)
