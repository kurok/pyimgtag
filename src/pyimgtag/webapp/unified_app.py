"""Unified pyimgtag webapp: dashboard + review + faces + tags + query + judge."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def create_unified_app(db_path: str | Path | None = None) -> Any:
    """Compose all routers onto one FastAPI app.

    Mounts:
    - Dashboard at ``/``
    - Review at ``/review``
    - Faces at ``/faces``
    - Tags at ``/tags``
    - Query at ``/query``
    - Judge at ``/judge``

    Raises:
        ImportError: If ``fastapi`` is not installed.
    """
    try:
        from fastapi import FastAPI
    except ImportError as exc:
        raise ImportError(
            "fastapi and uvicorn are required for the unified webapp. "
            "Install with: pip install 'pyimgtag[review]'"
        ) from exc

    from pyimgtag.progress_db import ProgressDB
    from pyimgtag.webapp.dashboard_server import build_dashboard_router
    from pyimgtag.webapp.routes_faces import build_faces_router
    from pyimgtag.webapp.routes_judge import build_judge_router
    from pyimgtag.webapp.routes_query import build_query_router
    from pyimgtag.webapp.routes_review import build_review_router
    from pyimgtag.webapp.routes_tags import build_tags_router

    db = ProgressDB(db_path=db_path)
    app = FastAPI(title="pyimgtag", docs_url=None, redoc_url=None, openapi_url=None)

    app.include_router(build_dashboard_router())
    app.include_router(build_review_router(db, api_base="/review"), prefix="/review")
    app.include_router(build_faces_router(db, api_base="/faces"), prefix="/faces")
    app.include_router(build_tags_router(db, api_base="/tags"), prefix="/tags")
    app.include_router(build_query_router(db, api_base="/query"), prefix="/query")
    app.include_router(build_judge_router(db, api_base="/judge"), prefix="/judge")

    return app
