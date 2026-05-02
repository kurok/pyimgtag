"""Review UI routes as a reusable APIRouter factory."""

from __future__ import annotations

import contextlib
import hashlib
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

_THUMB_DIR = Path.home() / ".cache" / "pyimgtag" / "thumbs"

try:
    from pydantic import BaseModel as _BaseModel

    class _TagsBody(_BaseModel):
        file_path: str
        tags: list[str]

    class _CleanupBody(_BaseModel):
        file_path: str
        cleanup_class: str | None

except ImportError:
    _TagsBody = None  # type: ignore[assignment,misc]
    _CleanupBody = None  # type: ignore[assignment,misc]

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag Review</title>
  <style>
    __NAV_STYLES__
    .toolbar{position:sticky;top:52px;z-index:90;background:rgba(255,255,255,.85);
             backdrop-filter:blur(12px);border-bottom:1px solid var(--border);
             padding:10px 32px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
    #stats{font-size:13px;color:var(--muted)}
    #count{font-size:13px;color:var(--muted);margin-left:auto}
    #grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
          gap:14px;padding:20px 32px}
    .tag-input{font-size:11px;border:1px dashed var(--border);border-radius:4px;
               padding:2px 6px;background:transparent;color:var(--text);
               font-family:inherit;outline:none;width:80px}
    .tag-input:focus{border-color:var(--accent)}
    .tag-rm{background:none;border:none;cursor:pointer;color:var(--muted);
            font-size:10px;padding:0 2px;line-height:1}
    .tag-rm:hover{color:var(--danger)}
    .pagination{display:flex;align-items:center;gap:12px;padding:16px 32px;
                justify-content:center}
    .pagination button{padding:6px 16px;border-radius:var(--radius-sm);font-size:13px;
                       font-weight:500;border:1px solid var(--border);
                       background:var(--surface);color:var(--text);cursor:pointer}
    .pagination button:disabled{opacity:.4;cursor:not-allowed}
    .pagination span{font-size:13px;color:var(--muted)}
  </style>
</head>
<body>
__NAV__
<div class="toolbar">
  <div class="pills" style="padding:0">
    <button class="pill on" onclick="setFilter(null,this)">All</button>
    <button class="pill" onclick="setFilter('delete',this)">Delete</button>
    <button class="pill" onclick="setFilter('review',this)">Review</button>
  </div>
  <span id="stats"></span>
  <span id="count"></span>
</div>
<div id="grid"></div>
<div class="pagination">
  <button id="prevBtn" onclick="prevPage()" disabled>&#8592; Prev</button>
  <span id="pageInfo"></span>
  <button id="nextBtn" onclick="nextPage()">Next &#8594;</button>
</div>
<script>
let _offset = 0, _total = 0, _cleanup = null;
const PAGE = 50;

async function loadStats() {
  const r = await fetch('__API_BASE__/api/stats');
  const d = await r.json();
  document.getElementById('stats').textContent =
    d.total + ' images \u00b7 ' + d.error + ' errors';
}

async function load() {
  const qs = '?limit=' + PAGE + '&offset=' + _offset +
             (_cleanup ? '&cleanup=' + _cleanup : '');
  const r = await fetch('__API_BASE__/api/images' + qs);
  const d = await r.json();
  _total = d.total;
  document.getElementById('count').textContent = _total + ' shown';
  document.getElementById('pageInfo').textContent =
    'Page ' + (Math.floor(_offset / PAGE) + 1) + ' of ' +
    Math.max(1, Math.ceil(_total / PAGE));
  document.getElementById('prevBtn').disabled = _offset === 0;
  document.getElementById('nextBtn').disabled = _offset + PAGE >= _total;
  renderGrid(d.items || []);
}

function setFilter(val, btn) {
  _cleanup = val; _offset = 0;
  document.querySelectorAll('.pill').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  load();
}

function prevPage() { _offset = Math.max(0, _offset - PAGE); load(); }
function nextPage() { _offset += PAGE; load(); }

function renderGrid(items) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  for (const img of items) {
    const wrap = document.createElement('div');
    wrap.className = 'img-card';

    const thumbWrap = document.createElement('div');
    thumbWrap.className = 'img-thumb-wrap';
    const thumbEl = document.createElement('img');
    thumbEl.className = 'img-thumb';
    thumbEl.loading = 'lazy';
    thumbEl.src = '__API_BASE__/thumbnail?path=' +
                  encodeURIComponent(img.file_path) + '&size=400';
    thumbEl.onerror = () => { thumbEl.style.background = '#e5e5ea';
                              thumbEl.removeAttribute('src'); };
    thumbWrap.appendChild(thumbEl);
    if (img.cleanup_class === 'delete' || img.cleanup_class === 'review') {
      const badge = document.createElement('span');
      badge.className = 'img-badge ' +
        (img.cleanup_class === 'delete' ? 'badge-del' : 'badge-rev');
      badge.textContent = img.cleanup_class.toUpperCase();
      thumbWrap.appendChild(badge);
    }
    wrap.appendChild(thumbWrap);

    const body = document.createElement('div');
    body.className = 'img-body';
    const nameEl = document.createElement('div');
    nameEl.className = 'img-name';
    nameEl.title = img.file_path;
    nameEl.textContent = img.file_name;
    body.appendChild(nameEl);
    const sceneEl = document.createElement('div');
    sceneEl.className = 'img-scene';
    sceneEl.textContent = img.scene_summary || '';
    body.appendChild(sceneEl);
    const tagsEl = document.createElement('div');
    tagsEl.className = 'img-tags';
    renderTags(tagsEl, img);
    body.appendChild(tagsEl);

    const acts = document.createElement('div');
    acts.className = 'img-actions';
    for (const [label, cls, val] of [
      ['Keep','btn-keep',null],['Review','btn-rev','review'],['Delete','btn-del','delete'],
    ]) {
      const b = document.createElement('button');
      b.className = 'img-btn ' + cls;
      b.textContent = label;
      b.addEventListener('click', () => setCleanup(img, val));
      acts.appendChild(b);
    }
    body.appendChild(acts);
    wrap.appendChild(body);
    grid.appendChild(wrap);
  }
}

function renderTags(container, img) {
  container.innerHTML = '';
  for (const t of (img.tags || [])) {
    const chip = document.createElement('span');
    chip.className = 'tag-chip';
    chip.textContent = t;
    const rm = document.createElement('button');
    rm.className = 'tag-rm';
    rm.textContent = '\u00d7';
    rm.addEventListener('click', () => removeTag(img, t, container));
    chip.appendChild(rm);
    container.appendChild(chip);
  }
  const inp = document.createElement('input');
  inp.className = 'tag-input';
  inp.placeholder = '+ tag';
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter' && inp.value.trim()) addTag(img, inp.value.trim(), container);
  });
  container.appendChild(inp);
}

async function removeTag(img, tag, container) {
  const prev = (img.tags || []).slice();
  img.tags = prev.filter(t => t !== tag);
  const r = await fetch('__API_BASE__/api/images/tags', {
    method: 'PATCH', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({file_path: img.file_path, tags: img.tags}),
  });
  if (!r.ok) { img.tags = prev; alert('Failed to remove tag'); }
  renderTags(container, img);
}

async function addTag(img, tag, container) {
  const prev = (img.tags || []).slice();
  img.tags = [...new Set([...prev, tag])];
  const r = await fetch('__API_BASE__/api/images/tags', {
    method: 'PATCH', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({file_path: img.file_path, tags: img.tags}),
  });
  if (!r.ok) { img.tags = prev; alert('Failed to add tag'); }
  renderTags(container, img);
}

async function setCleanup(img, val) {
  const r = await fetch('__API_BASE__/api/images/cleanup', {
    method: 'PATCH', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({file_path: img.file_path, cleanup_class: val}),
  });
  if (!r.ok) { alert('Failed to update'); return; }
  load();
}

loadStats();
load();
</script>
</body>
</html>"""


def render_review_html(api_base: str = "") -> str:
    """Return the review UI HTML with the given API base prefix."""
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav

    return (
        _HTML_TEMPLATE.replace("__API_BASE__", api_base)
        .replace("__NAV__", render_nav("review"))
        .replace("__NAV_STYLES__", NAV_STYLES)
    )


def _make_thumbnail(image_path: str, size: int) -> bytes | None:
    """Return cached JPEG thumbnail bytes. Returns None on any failure."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return None

    with contextlib.suppress(ImportError):
        from pillow_heif import register_heif_opener  # type: ignore[import-untyped]

        register_heif_opener()

    cache_key = hashlib.sha256(f"{image_path}:{size}".encode()).hexdigest()
    cache_path = _THUMB_DIR / f"{cache_key}.jpg"

    if cache_path.exists():
        return cache_path.read_bytes()

    try:
        with Image.open(image_path) as img:
            img.thumbnail((size, size), Image.Resampling.LANCZOS)
            img_rgb = img.convert("RGB")
            buf = io.BytesIO()
            img_rgb.save(buf, format="JPEG", quality=75)
            data = buf.getvalue()
        _THUMB_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
        return data
    except (OSError, UnidentifiedImageError):
        return None
    except Exception:  # noqa: BLE001 — catch-all for PIL/HEIC decode failures
        return None


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
    ) -> dict:
        items = db.get_images(limit=limit, offset=offset, status=status, cleanup_class=cleanup)
        total = db.count_images(status=status, cleanup_class=cleanup)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @router.get("/thumbnail")
    async def get_thumbnail(
        path: str = Query(..., description="Absolute path to the image file"),
        size: int = Query(default=200, ge=50, le=800),
    ) -> Response:
        if db.get_image(path) is None:
            return Response(status_code=404)
        data = _make_thumbnail(path, size)
        if data is None:
            return Response(status_code=404)
        return Response(content=data, media_type="image/jpeg")

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
