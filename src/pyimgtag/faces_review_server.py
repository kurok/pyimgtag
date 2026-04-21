"""FastAPI face management UI for pyimgtag."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB


def build_app(db: ProgressDB) -> Any:
    """Build and return the FastAPI application.

    Args:
        db: ProgressDB instance to use for all requests.

    Returns:
        FastAPI application instance.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import FastAPI
    except ImportError:
        raise ImportError(
            "fastapi is required for the faces review UI. "
            "Install with: pip install 'pyimgtag[review]'"
        ) from None

    from pyimgtag.webapp.routes_faces import build_faces_router

    app = FastAPI(title="pyimgtag Faces", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_faces_router(db, api_base=""))
    return app


def run_server(db: ProgressDB, host: str = "127.0.0.1", port: int = 8766) -> None:
    """Start the face review server (blocking).

    Args:
        db: ProgressDB instance.
        host: Bind address.
        port: TCP port.

    Raises:
        ImportError: If uvicorn is not installed.
    """
    try:
        import uvicorn
    except ImportError:
        raise ImportError(
            "uvicorn is required for the faces review UI. "
            "Install with: pip install 'pyimgtag[review]'"
        ) from None

    app = build_app(db)
    print(f"Face review UI: http://{host}:{port}/", flush=True)
    uvicorn.run(app, host=host, port=port)
