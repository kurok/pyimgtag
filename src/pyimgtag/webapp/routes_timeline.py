"""Timeline page routes: browse the library by capture date.

``/timeline`` draws a month histogram from ``/timeline/api/months``;
clicking a bar loads that month's day strip from
``/timeline/api/days?month=YYYY-MM`` and clicking a day hands the date to
the query grid (``/query/api/images?day=YYYY-MM-DD``). Every number is a
SQL aggregate over ``processed_images.image_date`` — see
:class:`pyimgtag.db.map_db.MapDB`.

The bars can be coloured by average judge score or by cleanup-class
share; both metrics come back with the counts in the same response, so
switching the toggle is pure CSS and costs no extra request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB


def render_timeline_html(api_base: str = "") -> str:
    """Return the timeline page HTML with the given API base prefix inserted."""
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav
    from pyimgtag.webapp.templating import Markup, render

    return render(
        "timeline.html",
        api_base=Markup(api_base),
        nav=Markup(render_nav("timeline")),
        nav_styles=Markup(NAV_STYLES),
    )


def build_timeline_router(db: ProgressDB, api_base: str = "") -> Any:
    """Build and return a FastAPI APIRouter for the timeline page.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into HTML (e.g. ``"/timeline"`` or ``""``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, HTTPException
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for the timeline UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    import asyncio

    from pyimgtag.filters import parse_month

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_timeline_html(api_base)

    @router.get("/api/months")
    async def months() -> dict:
        rows = await asyncio.to_thread(db.timeline_months)
        return {"months": rows, "total": sum(r["count"] for r in rows)}

    @router.get("/api/days")
    async def days(month: str) -> dict:
        try:
            valid = parse_month(month)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        rows = await asyncio.to_thread(db.timeline_days, valid)
        return {"month": valid, "days": rows, "total": sum(r["count"] for r in rows)}

    return router
