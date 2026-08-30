"""Map page routes: browse the library geographically.

The page is a Leaflet map served entirely from the package (see
:mod:`pyimgtag.webapp.routes_static`) — no CDN, no bundler. The only
external request the page makes is for raster map tiles, and that URL is
configurable:

* ``PYIMGTAG_MAP_TILES`` sets the tile template (default OpenStreetMap).
* ``PYIMGTAG_MAP_TILES_ATTRIBUTION`` sets the attribution line shown on
  the map, which the tile provider's terms generally require.

Point ``PYIMGTAG_MAP_TILES`` at a LAN tile server (or leave the map
zoomed out) if you do not want the tile host to learn which regions you
browse. Clustering itself is entirely local: ``/map/api/clusters`` bins
the stored EXIF coordinates SQL-side via
:meth:`pyimgtag.db.map_db.MapDB.clusters`.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from pyimgtag.db.map_db import LEAF_ZOOM, MAX_CELLS, MAX_ZOOM, MIN_ZOOM

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

#: Tile template used when ``PYIMGTAG_MAP_TILES`` is unset.
DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

#: Attribution used when ``PYIMGTAG_MAP_TILES_ATTRIBUTION`` is unset.
DEFAULT_TILE_ATTRIBUTION = "&copy; OpenStreetMap contributors"

#: Photos listed in a leaf popover before it switches to "show as grid".
POPOVER_LIMIT = 12


def tile_config() -> dict[str, str]:
    """Return the ``{"url", "attribution"}`` tile settings from the environment."""
    url = os.environ.get("PYIMGTAG_MAP_TILES", "").strip() or DEFAULT_TILE_URL
    attribution = (
        os.environ.get("PYIMGTAG_MAP_TILES_ATTRIBUTION", "").strip() or DEFAULT_TILE_ATTRIBUTION
    )
    return {"url": url, "attribution": attribution}


def render_map_html(api_base: str = "") -> str:
    """Return the map page HTML with the given API base prefix inserted."""
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav
    from pyimgtag.webapp.templating import Markup, render

    tiles = tile_config()
    return render(
        "map.html",
        api_base=Markup(api_base),
        nav=Markup(render_nav("map")),
        nav_styles=Markup(NAV_STYLES),
        # Autoescaped: the tile URL and attribution come from the
        # environment, so they are the one untrusted input on this page.
        tile_url=tiles["url"],
        tile_attribution=tiles["attribution"],
        leaf_zoom=LEAF_ZOOM,
        popover_limit=POPOVER_LIMIT,
    )


def build_map_router(db: ProgressDB, api_base: str = "") -> Any:
    """Build and return a FastAPI APIRouter for the map page.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into HTML (e.g. ``"/map"`` or ``""``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, HTTPException, Query
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for the map UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    import asyncio

    from pyimgtag.filters import parse_bbox

    router = APIRouter()

    def _bbox(raw: str | None) -> tuple[float, float, float, float] | None:
        if not raw:
            return None
        try:
            return parse_bbox(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_map_html(api_base)

    @router.get("/api/coverage")
    async def coverage() -> dict:
        return await asyncio.to_thread(db.gps_coverage)

    @router.get("/api/clusters")
    async def clusters(
        bbox: str | None = None,
        zoom: int = Query(default=2, ge=MIN_ZOOM, le=MAX_ZOOM),
        limit: int = Query(default=MAX_CELLS, ge=1, le=MAX_CELLS),
    ) -> dict:
        box = _bbox(bbox)
        cells = await asyncio.to_thread(db.map_clusters, box, zoom, limit)
        return {"zoom": zoom, "leaf_zoom": LEAF_ZOOM, "clusters": cells}

    @router.get("/api/photos")
    async def photos(
        bbox: str | None = None,
        limit: int = Query(default=POPOVER_LIMIT, ge=1, le=200),
    ) -> list[dict]:
        """List individual photos inside *bbox* for the leaf-level popover."""
        box = _bbox(bbox)
        return await asyncio.to_thread(
            lambda: db.query_images(bbox=box, limit=limit, sort="shot_desc")
        )

    return router
