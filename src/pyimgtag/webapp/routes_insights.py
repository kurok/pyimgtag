"""Insights dashboard routes as a reusable APIRouter factory.

``/`` renders the dashboard page (client-side rendering of ``/api/insights``),
``/api/insights`` returns the same JSON document as
``pyimgtag insights --format json``, and ``/export`` streams the standalone
HTML report so the "Export HTML" button delivers exactly the CLI artifact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB


def render_insights_html(api_base: str = "") -> str:
    """Return the insights page HTML with the given API base prefix inserted."""
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav
    from pyimgtag.webapp.templating import Markup, render

    return render(
        "insights.html",
        api_base=Markup(api_base),
        nav=Markup(render_nav("insights")),
        nav_styles=Markup(NAV_STYLES),
    )


def build_insights_router(db: ProgressDB, api_base: str = "") -> Any:
    """Build and return a FastAPI APIRouter for the insights page.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into HTML (e.g. ``"/insights"`` or ``""``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, Query
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for the insights UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    import asyncio

    from pyimgtag.insights_report import DEFAULT_MAX_THUMBS, render_html
    from pyimgtag.webapp.routes_review import _make_thumbnail

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_insights_html(api_base)

    @router.get("/api/insights")
    async def get_insights(top: int = Query(default=10, ge=1, le=50)) -> dict:
        return await asyncio.to_thread(db.get_insights, top)

    @router.get("/export", response_class=HTMLResponse)
    async def export(
        top: int = Query(default=10, ge=1, le=50),
        thumbnails: int = Query(default=1, ge=0, le=1),
    ) -> Any:
        def _build() -> str:
            doc = db.get_insights(top_n=top)
            return render_html(
                doc,
                thumb_loader=_make_thumbnail if thumbnails else None,
                max_thumbs=DEFAULT_MAX_THUMBS,
            )

        html = await asyncio.to_thread(_build)
        return HTMLResponse(
            html,
            headers={"Content-Disposition": 'attachment; filename="pyimgtag-insights.html"'},
        )

    return router
