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
    <div class="field"><label>Limit</label>
      <input id="f_limit" type="number" value="100" min="1" max="5000"></div>
    <button class="btn btn-primary" onclick="search()">Search</button>
  </div>
</div>
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
  for (const h of ['File','Status','Tags','Category','Cleanup','Location']) {
    const th = document.createElement('th');
    th.textContent = h;
    hdr.appendChild(th);
  }
  tbl.appendChild(hdr);

  for (const row of rows) {
    const tr = document.createElement('tr');
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

    for (const td of [tdFile, tdStatus, tdTags, tdCat, tdClean, tdLoc]) tr.appendChild(td);
    tbl.appendChild(tr);
  }
  wrap.appendChild(tbl);
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
    ) -> list[dict]:
        has_text_bool: bool | None = None
        if has_text == "true":
            has_text_bool = True
        elif has_text == "false":
            has_text_bool = False
        return db.query_images(
            tag=tag or None,
            has_text=has_text_bool,
            cleanup_class=cleanup or None,
            scene_category=scene_category or None,
            city=city or None,
            country=country or None,
            status=status or None,
            limit=limit,
        )

    return router
