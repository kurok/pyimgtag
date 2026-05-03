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
    .tag-chip-label{color:inherit;text-decoration:none;cursor:pointer}
    .tag-chip-label:hover{color:var(--accent);text-decoration:underline}
    .pagination{display:flex;align-items:center;gap:12px;padding:16px 32px;
                justify-content:center}
    .pagination button{padding:6px 16px;border-radius:var(--radius-sm);font-size:13px;
                       font-weight:500;border:1px solid var(--border);
                       background:var(--surface);color:var(--text);cursor:pointer}
    .pagination button:disabled{opacity:.4;cursor:not-allowed}
    .pagination span{font-size:13px;color:var(--muted)}
    .img-thumb-fallback{display:flex;align-items:center;justify-content:center;
                         padding:14px 10px;text-align:center;
                         font-family:ui-monospace,'SF Mono',monospace;font-size:11px;
                         color:var(--muted);background:#f0f0f5;border-radius:6px;
                         min-height:120px;word-break:break-all;line-height:1.35}
    .ctrl-label{display:inline-flex;align-items:center;gap:6px;font-size:12px;
                color:var(--muted)}
    .ctrl-label select{padding:4px 8px;border-radius:6px;
                        border:1px solid var(--border);background:var(--surface);
                        color:var(--text);font-size:12px}
    .img-thumb{cursor:zoom-in}
    .img-source{display:inline-block;margin-top:4px;font-size:11px;
                 color:var(--muted);text-decoration:none;border-bottom:1px dotted var(--muted)}
    .img-source:hover{color:var(--accent);border-bottom-color:var(--accent)}
    .img-error-msg{font-size:11px;color:var(--danger);margin-top:4px;line-height:1.3}
    .lightbox{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);
               z-index:1000;align-items:center;justify-content:center;cursor:zoom-out}
    .lightbox.open{display:flex}
    .lightbox img{max-width:95vw;max-height:95vh;object-fit:contain;cursor:default}
    .lightbox-close{position:fixed;top:16px;right:24px;color:#fff;font-size:32px;
                     cursor:pointer;line-height:1;user-select:none}
    .img-judge{position:absolute;top:8px;right:8px;font-size:11px;font-weight:700;
               padding:2px 7px;border-radius:10px;background:rgba(0,0,0,.55);
               color:#fff;letter-spacing:.4px;pointer-events:none}
    .img-judge.judge-high{background:rgba(34,139,34,.85)}
    .img-judge.judge-mid{background:rgba(255,159,10,.85)}
    .img-judge.judge-low{background:rgba(255,59,48,.85)}
  </style>
</head>
<body>
__NAV__
<div class="toolbar">
  <div class="pills" style="padding:0">
    <button class="pill on" onclick="setFilter(null,this)">All</button>
    <button class="pill" onclick="setFilter('delete',this)">Delete</button>
    <button class="pill" onclick="setFilter('review',this)">Review</button>
    <button class="pill" onclick="setStatusFilter('error',this)">Errors</button>
  </div>
  <label class="ctrl-label">Sort
    <select id="sortSel" onchange="setSort(this.value)">
      <option value="path_asc">Path (A→Z)</option>
      <option value="path_desc">Path (Z→A)</option>
      <option value="newest">Newest first</option>
      <option value="oldest">Oldest first</option>
      <option value="name_asc">Name (A→Z)</option>
      <option value="name_desc">Name (Z→A)</option>
    </select>
  </label>
  <label class="ctrl-label">Per page
    <select id="pageSel" onchange="setPageSize(parseInt(this.value,10))">
      <option value="25">25</option>
      <option value="50" selected>50</option>
      <option value="100">100</option>
      <option value="200">200</option>
    </select>
  </label>
  <span id="stats"></span>
  <span id="count"></span>
</div>
<div class="pagination top">
  <button id="prevBtnTop" onclick="prevPage()" disabled>&#8592; Prev</button>
  <span id="pageInfoTop"></span>
  <button id="nextBtnTop" onclick="nextPage()">Next &#8594;</button>
</div>
<div id="grid"></div>
<div class="pagination">
  <button id="prevBtn" onclick="prevPage()" disabled>&#8592; Prev</button>
  <span id="pageInfo"></span>
  <button id="nextBtn" onclick="nextPage()">Next &#8594;</button>
</div>

<div id="lightbox" class="lightbox" onclick="closeLightbox(event)">
  <img id="lightboxImg" alt="" />
  <span class="lightbox-close" onclick="closeLightbox(event)">&times;</span>
</div>
<script>
let _offset = 0, _total = 0, _cleanup = null, _status = null;
let _sort = 'path_asc';
let PAGE = 50;

async function loadStats() {
  const r = await fetch('__API_BASE__/api/stats');
  const d = await r.json();
  document.getElementById('stats').textContent =
    d.total + ' images \u00b7 ' + d.error + ' errors';
}

// If the page was opened with ?file=<path> (typically from the dashboard
// click-through) zoom in on that single image: hide pagination, ignore the
// cleanup pills, and only fetch that one record.
const _singleFile = (() => {
  const p = new URLSearchParams(window.location.search).get('file');
  return p && p.length ? p : null;
})();

async function load() {
  let qs;
  if (_singleFile) {
    qs = '?file=' + encodeURIComponent(_singleFile);
  } else {
    qs = '?limit=' + PAGE + '&offset=' + _offset + '&sort=' + encodeURIComponent(_sort) +
         (_cleanup ? '&cleanup=' + _cleanup : '') +
         (_status ? '&status=' + _status : '');
  }
  const r = await fetch('__API_BASE__/api/images' + qs);
  const d = await r.json();
  _total = d.total;
  document.getElementById('count').textContent = _total + ' shown';
  const pageText = _singleFile
    ? 'Showing 1 image'
    : 'Page ' + (Math.floor(_offset / PAGE) + 1) + ' of ' +
      Math.max(1, Math.ceil(_total / PAGE));
  document.getElementById('pageInfo').textContent = pageText;
  document.getElementById('pageInfoTop').textContent = pageText;
  const atFirst = _singleFile || _offset === 0;
  const atLast = _singleFile || _offset + PAGE >= _total;
  document.getElementById('prevBtn').disabled = atFirst;
  document.getElementById('prevBtnTop').disabled = atFirst;
  document.getElementById('nextBtn').disabled = atLast;
  document.getElementById('nextBtnTop').disabled = atLast;
  renderGrid(d.items || []);
}

function setFilter(val, btn) {
  _cleanup = val; _status = null; _offset = 0;
  document.querySelectorAll('.pill').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  load();
}

function setStatusFilter(val, btn) {
  _status = val; _cleanup = null; _offset = 0;
  document.querySelectorAll('.pill').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  load();
}

function setSort(val) { _sort = val; _offset = 0; load(); }
function setPageSize(n) { PAGE = n; _offset = 0; load(); }

function judgeClass(score) {
  if (score >= 8) return 'judge-high';
  if (score >= 6) return 'judge-mid';
  return 'judge-low';
}

function prevPage() { _offset = Math.max(0, _offset - PAGE); load(); }
function nextPage() { _offset += PAGE; load(); }

function openLightbox(path) {
  const lb = document.getElementById('lightbox');
  const img = document.getElementById('lightboxImg');
  img.src = '__API_BASE__/original?path=' + encodeURIComponent(path);
  lb.classList.add('open');
}
function closeLightbox(e) {
  // Only close on clicks on the backdrop or the close glyph, not on the image.
  if (e && e.target && e.target.tagName === 'IMG') return;
  document.getElementById('lightbox').classList.remove('open');
  document.getElementById('lightboxImg').removeAttribute('src');
}
window.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeLightbox();
});

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
    thumbEl.alt = img.file_name || '';
    thumbEl.src = '__API_BASE__/thumbnail?path=' +
                  encodeURIComponent(img.file_path) + '&size=400';
    thumbEl.addEventListener('click', () => openLightbox(img.file_path));
    // When the thumbnail endpoint can't read the file (path moved, decode
    // error, etc.) replace the broken-image icon with a labelled placeholder
    // so the grid stays readable instead of showing a wall of broken icons.
    thumbEl.onerror = () => {
      const ph = document.createElement('div');
      ph.className = 'img-thumb-fallback';
      ph.title = img.file_path || '';
      ph.textContent = img.file_name || 'thumbnail unavailable';
      ph.addEventListener('click', () => openLightbox(img.file_path));
      if (thumbEl.parentNode) thumbEl.parentNode.replaceChild(ph, thumbEl);
    };
    thumbWrap.appendChild(thumbEl);
    if (img.cleanup_class === 'delete' || img.cleanup_class === 'review') {
      const badge = document.createElement('span');
      badge.className = 'img-badge ' +
        (img.cleanup_class === 'delete' ? 'badge-del' : 'badge-rev');
      badge.textContent = img.cleanup_class.toUpperCase();
      thumbWrap.appendChild(badge);
    }
    if (typeof img.judge_score === 'number') {
      // Show the judge weighted score (1–10 integer) as a corner badge.
      // Tooltip prefers the simple-prompt ``reason`` and falls back to
      // the legacy ``verdict`` so older judged rows still show context.
      const judge = document.createElement('span');
      judge.className = 'img-judge ' + judgeClass(img.judge_score);
      judge.textContent = img.judge_score + '/10';
      const tip = img.judge_reason || img.judge_verdict;
      if (tip) judge.title = tip;
      thumbWrap.appendChild(judge);
    }
    wrap.appendChild(thumbWrap);

    const body = document.createElement('div');
    body.className = 'img-body';
    const nameEl = document.createElement('div');
    nameEl.className = 'img-name';
    nameEl.title = img.file_path;
    nameEl.textContent = img.file_name;
    body.appendChild(nameEl);
    // "Open original" — when the host is macOS we ask the backend to
    // activate Photos.app and spotlight this media item; otherwise (or
    // when the photo isn't in the library) we fall back to streaming the
    // original bytes via /original. Built as a real link so right-click
    // / cmd-click still opens the bytes in a new tab.
    const srcLink = document.createElement('a');
    srcLink.className = 'img-source';
    srcLink.href = '__API_BASE__/original?path=' + encodeURIComponent(img.file_path);
    srcLink.target = '_blank';
    srcLink.rel = 'noopener';
    srcLink.textContent = 'Open original';
    srcLink.title = 'Reveal in Photos.app — falls back to opening the file';
    srcLink.addEventListener('click', async (e) => {
      // Modifier-clicks / right-clicks bypass the Photos hop so power
      // users can still grab the raw bytes when they want them.
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      try {
        const r = await fetch(
          '__API_BASE__/api/open-in-photos?path=' + encodeURIComponent(img.file_path),
          { method: 'POST' });
        const d = await r.json();
        if (d && d.ok) return;
      } catch (_) { /* swallow — fall back below */ }
      window.open(srcLink.href, '_blank', 'noopener');
    });
    body.appendChild(srcLink);
    const sceneEl = document.createElement('div');
    sceneEl.className = 'img-scene';
    sceneEl.textContent = img.scene_summary || '';
    body.appendChild(sceneEl);
    if (img.status === 'error') {
      const errEl = document.createElement('div');
      errEl.className = 'img-error-msg';
      errEl.textContent = 'Error: ' + (img.error_message || 'unknown');
      body.appendChild(errEl);
    }
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
    // Clicking the label opens a Query search filtered to this tag.
    const label = document.createElement('a');
    label.className = 'tag-chip-label';
    label.href = '/query?tag=' + encodeURIComponent(t);
    label.title = 'Search images with this tag';
    label.textContent = t;
    chip.appendChild(label);
    const rm = document.createElement('button');
    rm.className = 'tag-rm';
    rm.textContent = '\u00d7';
    rm.addEventListener('click', e => {
      e.preventDefault();
      removeTag(img, t, container);
    });
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
    ) -> Response:
        # Use the request value purely as a DB lookup key; the actual
        # filesystem read uses the path the DB stored when pyimgtag
        # processed the image. This keeps user input out of Image.open().
        row = db.get_image(path)
        if row is None:
            return Response(status_code=404)
        data = _make_thumbnail(row["file_path"], size)
        if data is None:
            return Response(status_code=404)
        return Response(content=data, media_type="image/jpeg")

    @router.get("/original")
    async def get_original(
        path: str = Query(..., description="Absolute path to the image file"),
    ) -> Response:
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
        row = db.get_image(path)
        if row is None:
            return Response(status_code=404)
        # ``safe_path`` is the DB-stored path. CodeQL treats this as
        # untainted because it flows from a SQL row, not the HTTP request.
        safe_path: str = row["file_path"]
        try:
            from pathlib import Path as _P

            p = _P(safe_path)
            if not p.is_file():
                return Response(status_code=404)
            suffix = p.suffix.lower()
            if suffix in _MIME_BY_SUFFIX:
                return Response(content=p.read_bytes(), media_type=_MIME_BY_SUFFIX[suffix])
        except OSError:
            return Response(status_code=404)
        # Fall through to a high-quality JPEG render for HEIC / RAW / etc.
        data = _make_thumbnail(safe_path, 4000)
        if data is None:
            return Response(status_code=404)
        return Response(content=data, media_type="image/jpeg")

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
        """
        row = db.get_image(path)
        if row is None:
            return {"ok": False, "error": "Image not found in DB"}
        from pyimgtag.applescript_writer import reveal_in_photos

        err = reveal_in_photos(row["file_path"])
        if err is None:
            return {"ok": True}
        return {"ok": False, "error": err}

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
