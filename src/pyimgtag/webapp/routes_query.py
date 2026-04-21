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
    :root{--bg:#121212;--surface:#1e1e1e;--card:#252525;--accent:#bb86fc;
          --danger:#cf6679;--warn:#f9a825;--text:#e0e0e0;--muted:#888;--border:#333}
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text)}
    __NAV_STYLES__
    header{background:var(--surface);border-bottom:1px solid var(--border);padding:.75rem 1.5rem}
    h1{font-size:1rem;font-weight:600;color:var(--accent)}
    form{display:flex;flex-wrap:wrap;gap:.5rem;padding:.75rem 1.5rem;
         background:var(--surface);border-bottom:1px solid var(--border);align-items:flex-end}
    label{font-size:.75rem;color:var(--muted);display:flex;flex-direction:column;gap:.25rem}
    input,select{padding:.35rem .6rem;background:var(--card);border:1px solid var(--border);
                 border-radius:4px;color:var(--text);font-size:.8rem}
    button[type=submit]{padding:.35rem .9rem;font-size:.8rem;border:none;
                        background:var(--accent);color:#000;cursor:pointer;
                        border-radius:4px;font-weight:600;align-self:flex-end}
    #count{padding:.5rem 1.5rem;font-size:.8rem;color:var(--muted)}
    #results{padding:0 1.5rem 2rem;overflow-x:auto}
    table{width:100%;border-collapse:collapse;font-size:.8rem}
    th{text-align:left;padding:.4rem .6rem;background:var(--surface);
       border-bottom:1px solid var(--border);color:var(--muted);font-weight:600;white-space:nowrap}
    td{padding:.35rem .6rem;border-bottom:1px solid var(--border);
       max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    tr:hover td{background:var(--card)}
    .tag-pill{display:inline-block;background:#1e3a5a;border-radius:3px;
              padding:.1rem .3rem;font-size:.68rem;margin:.05rem}
    .cleanup-delete{color:var(--danger)}
    .cleanup-review{color:var(--warn)}
  </style>
</head>
<body>
__NAV__
<header><h1>pyimgtag &mdash; Query</h1></header>
<form id="qform" onsubmit="doSearch(event)">
  <label>Tag contains<input id="f_tag" placeholder="e.g. sunset"></label>
  <label>Cleanup
    <select id="f_cleanup">
      <option value="">any</option>
      <option value="delete">delete</option>
      <option value="review">review</option>
      <option value="keep">keep</option>
    </select>
  </label>
  <label>Category<input id="f_cat" placeholder="e.g. landscape"></label>
  <label>City<input id="f_city" placeholder="e.g. Tokyo"></label>
  <label>Country<input id="f_country" placeholder="e.g. Japan"></label>
  <label>Status
    <select id="f_status">
      <option value="">any</option>
      <option value="ok">ok</option>
      <option value="error">error</option>
    </select>
  </label>
  <label>Limit<input id="f_limit" type="number" value="100" min="1" max="5000" style="width:72px"></label>
  <button type="submit">Search</button>
</form>
<div id="count"></div>
<div id="results"></div>
<script>
async function doSearch(e) {
  if (e) e.preventDefault();
  const params = new URLSearchParams();
  const add = (id, key) => { const v = document.getElementById(id).value.trim(); if (v) params.set(key, v); };
  add('f_tag', 'tag');
  add('f_cleanup', 'cleanup');
  add('f_cat', 'scene_category');
  add('f_city', 'city');
  add('f_country', 'country');
  add('f_status', 'status');
  add('f_limit', 'limit');

  const resp = await fetch('__API_BASE__/api/images?' + params.toString());
  const rows = await resp.json();
  document.getElementById('count').textContent = rows.length + ' result(s)';
  const el = document.getElementById('results');
  if (!rows.length) {
    el.innerHTML = '<p style="padding:1rem;color:var(--muted)">No results.</p>';
    return;
  }
  const tbl = document.createElement('table');
  tbl.innerHTML = '<tr><th>File</th><th>Tags</th><th>Category</th><th>Cleanup</th><th>Location</th></tr>';
  for (const r of rows) {
    const tr = document.createElement('tr');
    const tags = (r.tags_list || []).map(t => '<span class="tag-pill">' + t + '</span>').join('');
    const loc = [r.nearest_city, r.nearest_country].filter(Boolean).join(', ');
    const cc = r.cleanup_class === 'delete' ? 'cleanup-delete'
              : r.cleanup_class === 'review' ? 'cleanup-review' : '';
    tr.innerHTML = '<td title="' + r.file_path + '">' + r.file_name + '</td>'
      + '<td>' + tags + '</td>'
      + '<td>' + (r.scene_category || '') + '</td>'
      + '<td class="' + cc + '">' + (r.cleanup_class || '') + '</td>'
      + '<td>' + loc + '</td>';
    tbl.appendChild(tr);
  }
  el.innerHTML = '';
  el.appendChild(tbl);
}
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
            "fastapi is required for the query UI. "
            "Install with: pip install 'pyimgtag[review]'"
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
