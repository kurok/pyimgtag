"""Review UI routes as a reusable APIRouter factory."""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)

_THUMB_DIR = Path.home() / ".cache" / "pyimgtag" / "thumbs"

_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

try:
    from pydantic import BaseModel as _BaseModel

    class _TagsBody(_BaseModel):
        file_path: str
        tags: list[str]

    class _CleanupBody(_BaseModel):
        file_path: str
        cleanup_class: str | None

except ImportError:  # pragma: no cover - pydantic ships with fastapi (review extra)
    _TagsBody = None  # type: ignore[assignment,misc]
    _CleanupBody = None  # type: ignore[assignment,misc]


def render_review_html(api_base: str = "") -> str:
    """Return the review UI HTML with the given API base prefix."""
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav
    from pyimgtag.webapp.templating import Markup, render

    return render(
        "review.html",
        api_base=Markup(api_base),
        nav=Markup(render_nav("review")),
        nav_styles=Markup(NAV_STYLES),
    )


def _thumb_via_sips(image_path: str, size: int) -> bytes | None:
    """Render a HEIC/HEIF thumbnail via macOS ``sips`` when PIL can't decode it."""
    import subprocess
    import tempfile
    from pathlib import Path as _P

    if not _P(image_path).is_file():
        return None
    try:
        import shutil

        if not shutil.which("sips"):
            return None
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            proc = subprocess.run(  # noqa: S603
                ["sips", "-s", "format", "jpeg", "-Z", str(size), image_path, "--out", tmp_path],
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0 or not _P(tmp_path).is_file():
                return None
            data = _P(tmp_path).read_bytes()
            return data if data else None
        finally:
            # Remove the temp file on every exit path — a failing sips run
            # (nonzero exit, timeout, OSError) must not leak orphaned .jpg
            # files in the temp directory.
            _P(tmp_path).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        return None


def _make_thumbnail(image_path: str, size: int) -> bytes | None:
    """Return cached JPEG thumbnail bytes. Returns None on any failure."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return None

    with contextlib.suppress(ImportError):
        from pillow_heif import register_heif_opener  # type: ignore[import-untyped]

        register_heif_opener()  # pragma: no cover - only runs with the [heic] extra

    cache_key = hashlib.sha256(f"{image_path}:{size}".encode()).hexdigest()
    cache_path = _THUMB_DIR / f"{cache_key}.jpg"

    if cache_path.exists():
        return cache_path.read_bytes()

    data: bytes | None = None
    try:
        with Image.open(image_path) as img:
            img.thumbnail((size, size), Image.Resampling.LANCZOS)
            img_rgb = img.convert("RGB")
            buf = io.BytesIO()
            img_rgb.save(buf, format="JPEG", quality=75)
            data = buf.getvalue()
    except (OSError, UnidentifiedImageError):
        pass  # nosec B110 — fall through to sips fallback below
    except Exception as exc:  # noqa: BLE001 — catch-all for PIL/HEIC decode failures
        # Best-effort: fall through to the sips path / placeholder, but leave a
        # breadcrumb so a systemic decode regression is discoverable in debug logs.
        logger.debug("thumbnail decode failed for %s: %s", image_path, exc)

    # PIL failed (likely HEIC without pillow-heif installed) — try macOS sips.
    if data is None and image_path.lower().endswith((".heic", ".heif")):
        data = _thumb_via_sips(image_path, size)

    if data is None:
        return None

    _THUMB_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    return data


def _serve_original(safe_path: str) -> tuple[bytes, str] | None:
    """Return (bytes, media_type) for the original file, or None on failure.

    Runs synchronously — always call via asyncio.to_thread from async handlers.
    """
    from pathlib import Path as _P

    try:
        p = _P(safe_path)
        if not p.is_file():
            return None
        suffix = p.suffix.lower()
        if suffix in _MIME_BY_SUFFIX:
            return p.read_bytes(), _MIME_BY_SUFFIX[suffix]
    except OSError:
        return None
    # HEIC / RAW — decode to a high-quality JPEG the browser can render.
    data = _make_thumbnail(safe_path, 4000)
    if data is None:
        return None
    return data, "image/jpeg"


def build_review_router(db: ProgressDB, api_base: str = "") -> Any:
    """Build and return a FastAPI APIRouter with all review UI routes.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into the HTML (e.g. ``"/review"`` or ``""``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, Body, Query, Response
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi and uvicorn are required for the review UI. "
            "Install with: pip install 'pyimgtag[review]'"
        ) from exc

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_review_html(api_base)

    @router.get("/api/stats")
    async def get_stats() -> dict:
        return db.get_stats()

    @router.get("/api/images")
    async def list_images(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        cleanup: str | None = Query(default=None),
        status: str | None = Query(default=None),
        sort: str = Query(default="path_asc"),
        file: str | None = Query(default=None, description="Single absolute path"),
    ) -> dict:
        # ``?file=<path>`` is used by the dashboard click-through to deep-link
        # into a single record; bypass pagination + cleanup filters in that
        # case and return either one item or an empty list.
        if file is not None:
            row = db.get_image(file)
            items = [row] if row is not None else []
            return {"items": items, "total": len(items), "limit": 1, "offset": 0}
        items = db.get_images(
            limit=limit, offset=offset, status=status, cleanup_class=cleanup, sort=sort
        )
        total = db.count_images(status=status, cleanup_class=cleanup)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @router.get("/thumbnail")
    async def get_thumbnail(
        path: str = Query(..., description="Absolute path to the image file"),
        size: int = Query(default=200, ge=50, le=4000),
    ):
        """Return a cached JPEG thumbnail for an image in the progress DB.

        ``path`` is used purely as a DB lookup key — the request value never
        reaches ``Image.open``; the read uses the DB-stored path. Returns 404
        when the row is missing or the image cannot be decoded.
        """
        import asyncio

        # Use the request value purely as a DB lookup key; the actual
        # filesystem read uses the path the DB stored when pyimgtag
        # processed the image. This keeps user input out of Image.open().
        # Resolve via tagging OR judging tables so judged-but-untagged images
        # (the whole Judge grid, typically) still get a preview.
        safe_path = db.get_known_file_path(path)
        if safe_path is None:
            return Response(status_code=404)
        # PIL decode + JPEG encode are CPU/IO-bound; run off the event loop
        # so concurrent requests (stats, pagination) are never blocked.
        data = await asyncio.to_thread(_make_thumbnail, safe_path, size)
        if data is None:
            return Response(status_code=404)
        return Response(content=data, media_type="image/jpeg")

    @router.get("/original")
    async def get_original(
        path: str = Query(..., description="Absolute path to the image file"),
    ):
        """Stream the original image bytes for the lightbox / "view source" link.

        Path must already be present in the progress DB; arbitrary filesystem
        reads are refused. HEIC and RAW originals are decoded to JPEG on the
        fly because most browsers can't render them natively.

        The query parameter is used purely as a lookup key against the DB.
        All filesystem operations downstream use the path string returned
        by ``db.get_image`` (i.e. the value pyimgtag itself stored when it
        scanned the file), so the request-controlled value never reaches
        ``open()`` / ``Path.is_file()``.
        """
        import asyncio

        # ``safe_path`` is the DB-stored path (tagging or judging table); not
        # derived from the HTTP request.
        safe_path = db.get_known_file_path(path)
        if safe_path is None:
            return Response(status_code=404)
        # File I/O and PIL decode are blocking — run off the event loop so
        # concurrent requests (stats, pagination) are never stalled.
        result = await asyncio.to_thread(_serve_original, safe_path)
        if result is None:
            return Response(status_code=404)
        data, media_type = result
        return Response(content=data, media_type=media_type)

    @router.post("/api/open-in-photos")
    async def open_in_photos(
        path: str = Query(..., description="Absolute path to the image file"),
    ) -> dict:
        """Activate Apple Photos and reveal the matching item.

        Looks the path up in the progress DB so the request value never
        reaches the AppleScript layer; only the DB-stored path is passed
        to ``reveal_in_photos``. Returns ``{"ok": true}`` on success or
        ``{"ok": false, "error": "..."}`` with HTTP 200 so the JS can
        gracefully fall back to opening the original bytes.

        The detailed error from ``reveal_in_photos`` (which can include
        an osascript stderr line/column reference) is logged server-side
        and one of these stable category strings is returned to the
        client: ``image_not_found`` (DB lookup failed),
        ``platform_unsupported`` (non-macOS host), ``photos_timeout``
        (osascript exceeded its window), ``photos_unavailable``
        (osascript / AppleScript missing), or ``photos_error`` (any
        other AppleScript failure). Downstream JS branches on the
        sentinel; the verbose stderr never reaches the browser.
        """
        safe_path = db.get_known_file_path(path)
        if safe_path is None:
            return {"ok": False, "error": "image_not_found"}
        from pyimgtag.applescript_writer import reveal_in_photos

        err = reveal_in_photos(safe_path)
        if err is None:
            return {"ok": True}
        logger.warning("open-in-photos failed for %s: %s", safe_path, err)
        # Map verbose AppleScript errors onto a small set of stable
        # client-facing categories so a script-level trace never reaches
        # the browser.
        low = err.lower()
        if "macos" in low:
            category = "platform_unsupported"
        elif "timed out" in low or "timeout" in low:
            category = "photos_timeout"
        elif "osascript" in low or "applescript" in low:
            category = "photos_unavailable"
        else:
            category = "photos_error"
        return {"ok": False, "error": category}

    @router.patch("/api/images/tags")
    async def update_tags(body: _TagsBody = Body(...)) -> dict:
        from pyimgtag.models import normalize_tags

        cleaned = normalize_tags(body.tags, max_tags=20)
        db.update_image_tags(body.file_path, cleaned)
        row = db.get_image(body.file_path)
        if row is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Image not found in DB")
        return row

    @router.patch("/api/images/cleanup")
    async def update_cleanup(body: _CleanupBody = Body(...)) -> dict:
        db.update_image_cleanup(body.file_path, body.cleanup_class)
        row = db.get_image(body.file_path)
        if row is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Image not found in DB")
        return row

    return router
