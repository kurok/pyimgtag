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

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag Judge</title>
  <style>
    __NAV_STYLES__
    .toolbar{position:sticky;top:52px;z-index:90;background:rgba(255,255,255,.85);
             backdrop-filter:blur(12px);border-bottom:1px solid var(--border);
             padding:10px 32px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
    #count{font-size:13px;color:var(--muted);margin-left:auto}
    #grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
          gap:14px;padding:20px 32px}
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
    .ctrl-label select,.ctrl-label input{padding:4px 8px;border-radius:6px;
                        border:1px solid var(--border);background:var(--surface);
                        color:var(--text);font-size:12px;font-family:inherit}
    .ctrl-label input[type=number]{width:64px}
    .img-thumb{cursor:zoom-in}
    .img-source{display:inline-block;margin-top:4px;font-size:11px;
                 color:var(--muted);text-decoration:none;border-bottom:1px dotted var(--muted)}
    .img-source:hover{color:var(--accent);border-bottom-color:var(--accent)}
    .lightbox{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);
               z-index:1000;align-items:center;justify-content:center;cursor:zoom-out}
    .lightbox.open{display:flex}
    .lightbox img{max-width:95vw;max-height:95vh;object-fit:contain;cursor:default}
    .lightbox-close{position:fixed;top:16px;right:24px;color:#fff;font-size:32px;
                     cursor:pointer;line-height:1;user-select:none}
    .img-judge{position:absolute;top:8px;right:8px;font-size:13px;font-weight:700;
               padding:4px 10px;border-radius:12px;background:rgba(0,0,0,.55);
               color:#fff;letter-spacing:.4px;pointer-events:none;
               box-shadow:0 2px 6px rgba(0,0,0,.25)}
    .img-judge.judge-high{background:rgba(34,139,34,.9)}
    .img-judge.judge-mid{background:rgba(255,159,10,.9)}
    .img-judge.judge-low{background:rgba(255,59,48,.9)}
    .img-judge.judge-none{background:rgba(99,99,102,.85)}
    .img-verdict{font-size:11px;font-weight:600;color:var(--muted);
                 text-transform:uppercase;letter-spacing:.4px;margin-top:2px}
    .img-reason{font-size:12px;color:var(--text);line-height:1.45;
                margin-top:6px;white-space:normal;word-wrap:break-word}
    .img-date{font-size:11px;color:var(--muted);margin-top:4px}
  </style>
</head>
<body>
__NAV__
<div class="toolbar">
  <label class="ctrl-label">Min rating
    <input id="minRating" type="number" min="1" max="10" step="1"
           onchange="setRating()" />
  </label>
  <label class="ctrl-label">Max rating
    <input id="maxRating" type="number" min="1" max="10" step="1"
           onchange="setRating()" />
  </label>
  <label class="ctrl-label">Sort
    <select id="sortSel" onchange="setSort(this.value)">
      <option value="rating_desc">Rating (high → low)</option>
      <option value="rating_asc">Rating (low → high)</option>
      <option value="path_asc">Path (A→Z)</option>
      <option value="path_desc">Path (Z→A)</option>
      <option value="shot_desc">Newest taken</option>
      <option value="shot_asc">Oldest taken</option>
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
let _offset = 0, _total = 0;
let _sort = 'rating_desc';
let _minRating = null, _maxRating = null;
let PAGE = 50;

function judgeClass(score) {
  if (score == null) return 'judge-none';
  if (score >= 8) return 'judge-high';
  if (score >= 6) return 'judge-mid';
  return 'judge-low';
}

async function load() {
  const params = new URLSearchParams();
  params.set('limit', String(PAGE));
  params.set('offset', String(_offset));
  params.set('sort', _sort);
  if (_minRating != null) params.set('min_rating', String(_minRating));
  if (_maxRating != null) params.set('max_rating', String(_maxRating));
  const r = await fetch('__API_BASE__/api/scores?' + params.toString());
  const d = await r.json();
  _total = d.total || 0;
  document.getElementById('count').textContent = _total + ' scored';
  const pages = Math.max(1, Math.ceil(_total / PAGE));
  const pageText = 'Page ' + (Math.floor(_offset / PAGE) + 1) + ' of ' + pages;
  document.getElementById('pageInfo').textContent = pageText;
  document.getElementById('pageInfoTop').textContent = pageText;
  const atFirst = _offset === 0;
  const atLast = _offset + PAGE >= _total;
  document.getElementById('prevBtn').disabled = atFirst;
  document.getElementById('prevBtnTop').disabled = atFirst;
  document.getElementById('nextBtn').disabled = atLast;
  document.getElementById('nextBtnTop').disabled = atLast;
  renderGrid(d.items || []);
}

function setSort(val) { _sort = val; _offset = 0; load(); }
function setPageSize(n) { PAGE = n; _offset = 0; load(); }
function setRating() {
  const minEl = document.getElementById('minRating');
  const maxEl = document.getElementById('maxRating');
  const minVal = minEl.value === '' ? null : parseInt(minEl.value, 10);
  const maxVal = maxEl.value === '' ? null : parseInt(maxEl.value, 10);
  _minRating = (Number.isFinite(minVal) ? minVal : null);
  _maxRating = (Number.isFinite(maxVal) ? maxVal : null);
  _offset = 0;
  load();
}

function prevPage() { _offset = Math.max(0, _offset - PAGE); load(); }
function nextPage() { _offset += PAGE; load(); }

function openLightbox(path) {
  const lb = document.getElementById('lightbox');
  const img = document.getElementById('lightboxImg');
  // Re-use the review router's /original endpoint — both routers
  // mount their own copy bound to the same DB, so the path lookup
  // succeeds for any judged image regardless of which page opened it.
  img.src = '/review/original?path=' + encodeURIComponent(path);
  lb.classList.add('open');
}
function closeLightbox(e) {
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
    // Re-use /review/thumbnail; it's path-injection-safe (looks up the
    // request value in the DB and reads the DB-stored path, not the
    // request value).
    thumbEl.src = '/review/thumbnail?path=' +
                  encodeURIComponent(img.file_path) + '&size=400';
    thumbEl.addEventListener('click', () => openLightbox(img.file_path));
    thumbEl.onerror = () => {
      const ph = document.createElement('div');
      ph.className = 'img-thumb-fallback';
      ph.title = img.file_path || '';
      ph.textContent = img.file_name || 'thumbnail unavailable';
      ph.addEventListener('click', () => openLightbox(img.file_path));
      if (thumbEl.parentNode) thumbEl.parentNode.replaceChild(ph, thumbEl);
    };
    thumbWrap.appendChild(thumbEl);

    const judge = document.createElement('span');
    judge.className = 'img-judge ' + judgeClass(img.weighted_score);
    judge.textContent = (img.weighted_score == null
        ? '–'
        : img.weighted_score + '/10');
    thumbWrap.appendChild(judge);
    wrap.appendChild(thumbWrap);

    const body = document.createElement('div');
    body.className = 'img-body';
    const nameEl = document.createElement('div');
    nameEl.className = 'img-name';
    nameEl.title = img.file_path;
    nameEl.textContent = img.file_name;
    body.appendChild(nameEl);

    // Photos.app reveal-on-click — same handler as Review so the link
    // behaves identically across the two pages.
    const srcLink = document.createElement('a');
    srcLink.className = 'img-source';
    srcLink.href = '/review/original?path=' + encodeURIComponent(img.file_path);
    srcLink.target = '_blank';
    srcLink.rel = 'noopener';
    srcLink.textContent = 'Open original';
    srcLink.title = 'Reveal in Photos.app — falls back to opening the file';
    srcLink.addEventListener('click', async (e) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      try {
        const r = await fetch(
          '/review/api/open-in-photos?path=' + encodeURIComponent(img.file_path),
          { method: 'POST' });
        const d = await r.json();
        if (d && d.ok) return;
      } catch (_) { /* swallow — fall back below */ }
      window.open(srcLink.href, '_blank', 'noopener');
    });
    body.appendChild(srcLink);

    if (img.scene_summary) {
      const sceneEl = document.createElement('div');
      sceneEl.className = 'img-scene';
      sceneEl.textContent = img.scene_summary;
      body.appendChild(sceneEl);
    }
    if (img.image_date) {
      const dateEl = document.createElement('div');
      dateEl.className = 'img-date';
      // Show only the calendar portion — the time component would
      // crowd the small card body.
      dateEl.textContent = String(img.image_date).slice(0, 10);
      body.appendChild(dateEl);
    }
    if (img.verdict) {
      const verdictEl = document.createElement('div');
      verdictEl.className = 'img-verdict';
      verdictEl.textContent = img.verdict;
      body.appendChild(verdictEl);
    }
    if (img.reason) {
      const reasonEl = document.createElement('div');
      reasonEl.className = 'img-reason';
      reasonEl.textContent = img.reason;
      body.appendChild(reasonEl);
    }
    wrap.appendChild(body);
    grid.appendChild(wrap);
  }
}

load();
</script>
</body>
</html>"""


def render_judge_html(api_base: str = "") -> str:
    """Return the judge UI HTML with the given API base prefix inserted."""
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav

    return (
        _HTML_TEMPLATE.replace("__API_BASE__", api_base)
        .replace("__NAV__", render_nav("judge"))
        .replace("__NAV_STYLES__", NAV_STYLES)
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
