"""Judge ranking UI routes as a reusable APIRouter factory.

The Judge page mirrors the Review page's layout (sticky toolbar, card
grid, lightbox, pagination) but is focused on the model's verdict: each
card surfaces the prominent rating badge and the full natural-language
reason text instead of the editable tag chips.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB


def render_judge_html(api_base: str = "") -> str:
    """Return the judge UI HTML with the given API base prefix inserted."""
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav
    from pyimgtag.webapp.templating import Markup, render

    return render(
        "judge.html",
        api_base=Markup(api_base),
        nav=Markup(render_nav("judge")),
        nav_styles=Markup(NAV_STYLES),
    )


def build_judge_router(db: ProgressDB, api_base: str = "") -> Any:
    """Build and return a FastAPI APIRouter for the judge ranking UI.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into HTML (e.g. ``"/judge"`` or ``""``).

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
            "fastapi is required for the judge UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_judge_html(api_base)

    @router.get("/api/scores")
    async def list_scores(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
        sort: str = Query(default="rating_desc"),
        min_rating: int | None = Query(default=None),
        max_rating: int | None = Query(default=None),
    ) -> dict:
        return db.query_judge_results(
            offset=offset,
            limit=limit,
            sort=sort,
            min_rating=min_rating,
            max_rating=max_rating,
        )

    return router
