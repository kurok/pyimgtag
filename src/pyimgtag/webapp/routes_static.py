"""Serve the vendored front-end assets that ship inside the package.

pyimgtag makes no requests to a CDN: the map page needs Leaflet, so
Leaflet is vendored under ``pyimgtag/webapp/static/`` and served from
here. Only the files listed in :data:`STATIC_FILES` are reachable — the
route is a lookup in that table rather than a path join, so there is no
traversal surface and no dependency on the package being unpacked on a
real filesystem (assets are read through :mod:`importlib.resources`).
"""

from __future__ import annotations

from typing import Any

#: The vendored Leaflet release. Bumping this means re-vendoring
#: ``leaflet.js`` / ``leaflet.css`` / ``images/*`` from the upstream
#: ``leaflet.zip`` for the same tag (source maps deliberately excluded).
LEAFLET_VERSION = "1.9.4"

#: ``request path -> content type`` for every asset the webapp serves.
STATIC_FILES: dict[str, str] = {
    "leaflet/leaflet.js": "text/javascript; charset=utf-8",
    "leaflet/leaflet.css": "text/css; charset=utf-8",
    "leaflet/LICENSE.txt": "text/plain; charset=utf-8",
    "leaflet/images/layers.png": "image/png",
    "leaflet/images/layers-2x.png": "image/png",
    "leaflet/images/marker-icon.png": "image/png",
    "leaflet/images/marker-icon-2x.png": "image/png",
    "leaflet/images/marker-shadow.png": "image/png",
}


def read_static(name: str) -> bytes:
    """Return the bytes of the packaged static asset *name*.

    Args:
        name: A key of :data:`STATIC_FILES`, e.g. ``"leaflet/leaflet.js"``.

    Raises:
        KeyError: If *name* is not a known asset.
        FileNotFoundError: If the package data is missing from the install.
    """
    if name not in STATIC_FILES:
        raise KeyError(name)
    from importlib.resources import files

    resource = files("pyimgtag.webapp") / "static"
    for part in name.split("/"):
        resource = resource / part
    return resource.read_bytes()


def build_static_router() -> Any:
    """Build an APIRouter serving :data:`STATIC_FILES` (mount at ``/static``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, HTTPException
        from fastapi.responses import Response
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for the web UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    router = APIRouter()

    @router.get("/{asset_path:path}")
    async def get_static(asset_path: str) -> Any:
        content_type = STATIC_FILES.get(asset_path)
        if content_type is None:
            raise HTTPException(status_code=404, detail="unknown asset")
        try:
            payload = read_static(asset_path)
        except FileNotFoundError as exc:  # pragma: no cover — broken install
            raise HTTPException(status_code=404, detail="asset missing") from exc
        return Response(
            content=payload,
            media_type=content_type,
            # Vendored assets are immutable for the life of a release.
            headers={"Cache-Control": "public, max-age=86400"},
        )

    return router
