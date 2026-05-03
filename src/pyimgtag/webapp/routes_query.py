"""Image query builder UI routes as a reusable APIRouter factory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag Query</title>
  <style>
    __NAV_STYLES__
    .filter-card{margin:20px 32px 0;background:var(--surface);
                 border-radius:var(--radius-md);box-shadow:var(--shadow-sm);
                 padding:18px 20px}
    .filter-row{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}
    .field{display:flex;flex-direction:column;gap:5px}
    .field label{font-size:11px;font-weight:600;color:var(--muted);
                 text-transform:uppercase;letter-spacing:.4px}
    .field input,.field select{padding:8px 10px;border:1px solid var(--border);
                               border-radius:var(--radius-sm);font-size:13px;
                               font-family:inherit;color:var(--text);background:var(--bg);
                               outline:none;transition:border-color .15s,box-shadow .15s}
    .field input:focus,.field select:focus{border-color:var(--accent);
                                           box-shadow:0 0 0 3px rgba(0,113,227,.15)}
    .field input[type=number]{width:80px}
    #count{padding:14px 32px 0;font-size:13px;color:var(--muted)}
    #results{padding:12px 32px 32px;overflow-x:auto}
    .cleanup-del{background:rgba(255,59,48,.1);color:var(--danger);padding:2px 7px;
                 border-radius:5px;font-size:11px;font-weight:600}
    .cleanup-rev{background:rgba(255,159,10,.1);color:var(--warn);padding:2px 7px;
                 border-radius:5px;font-size:11px;font-weight:600}
    .status-ok{color:var(--ok,#16a34a);font-size:11px;font-weight:600}
    .status-error{color:var(--danger);font-size:11px;font-weight:600}
    .err-msg{font-size:11px;color:var(--danger);margin-top:3px;
             font-family:ui-monospace,'SF Mono',monospace;word-break:break-word}
    .date-cell{font-size:12px;color:var(--muted);white-space:nowrap;
               font-variant-numeric:tabular-nums}
    .judge-cell{font-weight:700;font-size:12px;color:var(--text);
                white-space:nowrap}
    .judge-high{color:#16a34a}
    .judge-mid{color:#d97706}
    .judge-low{color:var(--danger)}
    .judge-none{color:var(--muted);font-weight:400}
    /* Hover thumbnail floats next to the cursor without re-laying out the row. */
    #hover-thumb{position:fixed;display:none;z-index:1000;pointer-events:none;
                 background:#fff;border:1px solid var(--border);
                 box-shadow:0 4px 18px rgba(0,0,0,.18);border-radius:6px;
                 padding:3px}
    #hover-thumb img{display:block;max-width:280px;max-height:280px;
                     object-fit:contain;border-radius:4px}
    #hover-thumb.placeholder{padding:8px 12px;font-size:11px;color:var(--muted);
                              font-family:ui-monospace,'SF Mono',monospace}
    .tbl tr.row{cursor:default}
  </style>
</head>
<body>
__NAV__
<div class="page-hdr">
  <h1 class="page-title">Query</h1>
</div>
<div class="filter-card">
  <div class="filter-row">
    <div class="field"><label>Tag</label>
      <input id="f_tag" type="text" placeholder="e.g. sunset"></div>
    <div class="field"><label>Has text</label>
      <select id="f_text">
        <option value="">any</option>
        <option value="true">yes</option>
        <option value="false">no</option>
      </select></div>
    <div class="field"><label>Cleanup</label>
      <select id="f_cleanup">
        <option value="">any</option>
        <option value="delete">delete</option>
        <option value="review">review</option>
        <option value="keep">keep</option>
      </select></div>
    <div class="field"><label>Category</label>
      <input id="f_cat" type="text" placeholder="e.g. landscape"></div>
    <div class="field"><label>City</label>
      <input id="f_city" type="text" placeholder="e.g. Kyiv"></div>
    <div class="field"><label>Country</label>
      <input id="f_country" type="text" placeholder="e.g. UA"></div>
    <div class="field"><label>Status</label>
      <select id="f_status">
        <option value="">any</option>
        <option value="ok">ok</option>
        <option value="error">error</option>
      </select></div>
    <div class="field"><label>Judge ≥</label>
      <input id="f_min_judge" type="number" min="1" max="10" placeholder="1-10"></div>
    <div class="field"><label>Judge ≤</label>
      <input id="f_max_judge" type="number" min="1" max="10" placeholder="1-10"></div>
    <div class="field"><label>Judged</label>
      <select id="f_judged">
        <option value="">any</option>
        <option value="true">scored</option>
        <option value="false">not scored</option>
      </select></div>
    <div class="field"><label>Sort</label>
      <select id="f_sort">
        <option value="path_asc">Path (A→Z)</option>
        <option value="path_desc">Path (Z→A)</option>
        <option value="newest">Newest processed</option>
        <option value="oldest">Oldest processed</option>
        <option value="shot_desc">Newest taken</option>
        <option value="shot_asc">Oldest taken</option>
        <option value="judge_desc">Judge score (high→low)</option>
        <option value="judge_asc">Judge score (low→high)</option>
      </select></div>
    <div class="field"><label>Limit</label>
      <input id="f_limit" type="number" value="100" min="1" max="5000"></div>
    <button class="btn btn-primary" onclick="search()">Search</button>
  </div>
</div>
<div id="hover-thumb"></div>
<div id="count"></div>
<div id="results"></div>
<script>
async function search() {
  const params = new URLSearchParams();
  const add = (id, key) => {
    const v = document.getElementById(id).value.trim();
    if (v) params.set(key, v);
  };
  add('f_tag', 'tag');
  add('f_text', 'has_text');
  add('f_cleanup', 'cleanup');
  add('f_cat', 'scene_category');
  add('f_city', 'city');
  add('f_country', 'country');
  add('f_status', 'status');
  add('f_min_judge', 'min_judge_score');
  add('f_max_judge', 'max_judge_score');
  add('f_judged', 'judged');
  add('f_sort', 'sort');
  add('f_limit', 'limit');

  const r = await fetch('__API_BASE__/api/images?' + params.toString());
  const rows = await r.json();
  document.getElementById('count').textContent = rows.length + ' results';
  const wrap = document.getElementById('results');
  wrap.innerHTML = '';
  if (!rows.length) return;

  const tbl = document.createElement('table');
  tbl.className = 'tbl';
  const hdr = document.createElement('tr');
  for (const h of ['File','Date','Status','Judge','Tags','Category','Cleanup','Location']) {
    const th = document.createElement('th');
    th.textContent = h;
    hdr.appendChild(th);
  }
  tbl.appendChild(hdr);

  for (const row of rows) {
    const tr = document.createElement('tr');
    tr.className = 'row';
    // Hover the row → show a 280px thumbnail next to the cursor.
    tr.addEventListener('mouseenter', () => showHoverThumb(row));
    tr.addEventListener('mousemove', moveHoverThumb);
    tr.addEventListener('mouseleave', hideHoverThumb);
    const tdFile = document.createElement('td');
    tdFile.className = 'fname';
    tdFile.title = row.file_path;
    // Click-through: open the review page for this single file in a new tab.
    const fileLink = document.createElement('a');
    fileLink.href = '/review?file=' + encodeURIComponent(row.file_path);
    fileLink.target = '_blank';
    fileLink.rel = 'noopener';
    fileLink.textContent = row.file_name;
    fileLink.style.color = 'inherit';
    tdFile.appendChild(fileLink);

    const tdDate = document.createElement('td');
    tdDate.className = 'date-cell';
    tdDate.textContent = formatPhotoDate(row.image_date);
    if (row.image_date) tdDate.title = row.image_date;

    const tdStatus = document.createElement('td');
    const statusEl = document.createElement('span');
    if (row.status === 'error') {
      statusEl.className = 'status-error';
      statusEl.textContent = 'error';
    } else {
      statusEl.className = 'status-ok';
      statusEl.textContent = row.status || 'ok';
    }
    tdStatus.appendChild(statusEl);

    const tdJudge = document.createElement('td');
    const judgeEl = document.createElement('span');
    judgeEl.className = 'judge-cell';
    if (typeof row.judge_score === 'number') {
      judgeEl.classList.add(judgeColour(row.judge_score));
      judgeEl.textContent = row.judge_score + '/10';
      const tip = row.judge_reason || row.judge_verdict;
      if (tip) judgeEl.title = tip;
    } else {
      judgeEl.classList.add('judge-none');
      judgeEl.textContent = '—';
    }
    tdJudge.appendChild(judgeEl);

    const tdTags = document.createElement('td');
    if (row.status === 'error' && row.error_message) {
      const em = document.createElement('div');
      em.className = 'err-msg';
      em.textContent = row.error_message;
      tdTags.appendChild(em);
    } else {
      for (const t of (row.tags_list || [])) {
        const chip = document.createElement('a');
        chip.className = 'tag-chip';
        chip.style.marginRight = '3px';
        chip.style.textDecoration = 'none';
        chip.style.cursor = 'pointer';
        chip.href = '/query?tag=' + encodeURIComponent(t);
        chip.title = 'Search images with this tag';
        chip.textContent = t;
        tdTags.appendChild(chip);
      }
    }

    const tdCat = document.createElement('td');
    tdCat.textContent = row.scene_category || '';

    const tdClean = document.createElement('td');
    if (row.cleanup_class) {
      const badge = document.createElement('span');
      badge.className = row.cleanup_class === 'delete' ? 'cleanup-del' : 'cleanup-rev';
      badge.textContent = row.cleanup_class;
      tdClean.appendChild(badge);
    } else {
      tdClean.textContent = '\u2014';
    }

    const tdLoc = document.createElement('td');
    tdLoc.textContent = [row.nearest_city, row.nearest_country].filter(Boolean).join(', ');

    const cells = [tdFile, tdDate, tdStatus, tdJudge, tdTags, tdCat, tdClean, tdLoc];
    for (const td of cells) tr.appendChild(td);
    tbl.appendChild(tr);
  }
  wrap.appendChild(tbl);
}

function judgeColour(score) {
  if (score >= 8) return 'judge-high';
  if (score >= 6) return 'judge-mid';
  return 'judge-low';
}

// Render the EXIF capture timestamp. The DB stores either an ISO 8601
// string (Pillow path) or the raw "YYYY:MM:DD HH:MM:SS" exiftool form;
// both should render as a short human date. Falls back to em-dash when
// the field is missing (older rows from before migration v8).
function formatPhotoDate(raw) {
  if (!raw) return '—';
  // exiftool keeps colons in the date portion; ISO 8601 uses dashes.
  const iso = String(raw).replace(/^(\\d{4}):(\\d{2}):(\\d{2})/, '$1-$2-$3');
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(raw).slice(0, 10);
  return d.toLocaleDateString(undefined,
    {year:'numeric', month:'short', day:'2-digit'});
}

// --- Hover thumbnail ---------------------------------------------------------
const _hoverThumb = document.getElementById('hover-thumb');

function showHoverThumb(row) {
  if (!row || !row.file_path) return;
  // Reuse the review thumbnail endpoint — it lives at the unified app's
  // /review prefix. The unified webapp serves /review and /query from
  // the same FastAPI app so a relative URL with that absolute prefix
  // resolves cleanly regardless of how the user opened the page.
  _hoverThumb.innerHTML = '';
  _hoverThumb.classList.remove('placeholder');
  const img = document.createElement('img');
  img.src = '/review/thumbnail?path=' + encodeURIComponent(row.file_path) + '&size=400';
  img.alt = row.file_name || '';
  img.addEventListener('error', () => {
    _hoverThumb.innerHTML = '';
    _hoverThumb.classList.add('placeholder');
    _hoverThumb.textContent = row.file_name || 'thumbnail unavailable';
  });
  _hoverThumb.appendChild(img);
  _hoverThumb.style.display = 'block';
}

function moveHoverThumb(e) {
  // Place the preview to the right of the cursor; if it would overflow
  // the viewport, switch to the left side.
  const padding = 16;
  let x = e.clientX + padding;
  const y = Math.min(window.innerHeight - 320, Math.max(0, e.clientY - 140));
  if (x + 320 > window.innerWidth) {
    x = e.clientX - 320 - padding;
  }
  _hoverThumb.style.left = x + 'px';
  _hoverThumb.style.top = y + 'px';
}

function hideHoverThumb() {
  _hoverThumb.style.display = 'none';
  _hoverThumb.innerHTML = '';
}

// On page load, pre-fill any filter from the URL (e.g. /query?tag=sunset
// from the Tags page click-through) and auto-run the search so the user
// lands on the results without an extra click.
(function applyUrlFilters() {
  const params = new URLSearchParams(window.location.search);
  const presets = [
    ['tag', 'f_tag'],
    ['has_text', 'f_text'],
    ['cleanup', 'f_cleanup'],
    ['scene_category', 'f_cat'],
    ['city', 'f_city'],
    ['country', 'f_country'],
    ['status', 'f_status'],
    ['min_judge_score', 'f_min_judge'],
    ['max_judge_score', 'f_max_judge'],
    ['judged', 'f_judged'],
    ['sort', 'f_sort'],
    ['limit', 'f_limit'],
  ];
  let any = false;
  for (const [key, id] of presets) {
    const v = params.get(key);
    if (v != null && v !== '') {
      const el = document.getElementById(id);
      if (el != null) {
        el.value = v;
        any = true;
      }
    }
  }
  if (any) search();
})();
</script>
</body>
</html>"""


def render_query_html(api_base: str = "") -> str:
    """Return the query UI HTML with the given API base prefix inserted."""
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav

    return (
        _HTML_TEMPLATE.replace("__API_BASE__", api_base)
        .replace("__NAV__", render_nav("query"))
        .replace("__NAV_STYLES__", NAV_STYLES)
    )


def build_query_router(db: "ProgressDB", api_base: str = "") -> Any:
    """Build and return a FastAPI APIRouter for the image query builder UI.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into HTML (e.g. ``"/query"`` or ``""``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for the query UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_query_html(api_base)

    @router.get("/api/images")
    async def query_images(
        tag: str | None = None,
        has_text: str | None = None,
        cleanup: str | None = None,
        scene_category: str | None = None,
        city: str | None = None,
        country: str | None = None,
        status: str | None = None,
        limit: int | None = 100,
        min_judge_score: int | None = None,
        max_judge_score: int | None = None,
        judged: str | None = None,
        sort: str = "path_asc",
    ) -> list[dict]:
        has_text_bool: bool | None = None
        if has_text == "true":
            has_text_bool = True
        elif has_text == "false":
            has_text_bool = False
        judged_bool: bool | None = None
        if judged == "true":
            judged_bool = True
        elif judged == "false":
            judged_bool = False
        return db.query_images(
            tag=tag or None,
            has_text=has_text_bool,
            cleanup_class=cleanup or None,
            scene_category=scene_category or None,
            city=city or None,
            country=country or None,
            status=status or None,
            limit=limit,
            min_judge_score=min_judge_score,
            max_judge_score=max_judge_score,
            judged=judged_bool,
            sort=sort,
        )

    return router
