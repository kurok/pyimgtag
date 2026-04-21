"""Tags management UI routes as a reusable APIRouter factory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag Tags</title>
  <style>
    :root{--bg:#121212;--surface:#1e1e1e;--card:#252525;--accent:#bb86fc;
          --danger:#cf6679;--text:#e0e0e0;--muted:#888;--border:#333}
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text)}
    __NAV_STYLES__
    header{background:var(--surface);border-bottom:1px solid var(--border);
           padding:.75rem 1.5rem;display:flex;align-items:center;gap:1rem}
    h1{font-size:1rem;font-weight:600;color:var(--accent)}
    #search{margin:.75rem 1.5rem;padding:.4rem .8rem;background:var(--surface);
            border:1px solid var(--border);border-radius:4px;color:var(--text);
            font-size:.85rem;width:280px}
    #list{padding:0 1.5rem 2rem}
    .row{display:flex;align-items:center;gap:.5rem;padding:.4rem 0;
         border-bottom:1px solid var(--border)}
    .tag-name{flex:1;font-size:.85rem}
    .cnt{font-size:.75rem;color:var(--muted);min-width:3.5rem;text-align:right}
    button{padding:.2rem .55rem;font-size:.72rem;border:1px solid var(--border);
           background:transparent;color:var(--text);cursor:pointer;border-radius:4px}
    button:hover{background:var(--surface)}
    button.danger{color:var(--danger);border-color:var(--danger)}
    #status{margin-left:auto;font-size:.8rem;color:var(--muted)}
  </style>
</head>
<body>
__NAV__
<header>
  <h1>pyimgtag &mdash; Tags</h1>
  <span id="status">Loading&hellip;</span>
</header>
<input id="search" placeholder="Filter tags&hellip;" oninput="filterTags(this.value)">
<div id="list"></div>
<script>
let allTags = [];

async function load() {
  const resp = await fetch('__API_BASE__/api/tags');
  allTags = await resp.json();
  document.getElementById('status').textContent = allTags.length + ' tag(s)';
  render(allTags);
}

function render(tags) {
  const el = document.getElementById('list');
  el.innerHTML = '';
  for (const t of tags) {
    const row = document.createElement('div');
    row.className = 'row';

    const nameSpan = document.createElement('span');
    nameSpan.className = 'tag-name';
    nameSpan.textContent = t.tag;

    const cntSpan = document.createElement('span');
    cntSpan.className = 'cnt';
    cntSpan.textContent = t.count + '\\u00a0img';

    const renBtn = document.createElement('button');
    renBtn.textContent = 'Rename';
    renBtn.addEventListener('click', () => renameTag(t.tag));

    const merBtn = document.createElement('button');
    merBtn.textContent = 'Merge into';
    merBtn.addEventListener('click', () => mergeTag(t.tag));

    const delBtn = document.createElement('button');
    delBtn.className = 'danger';
    delBtn.textContent = 'Delete';
    delBtn.addEventListener('click', () => deleteTag(t.tag));

    row.appendChild(nameSpan);
    row.appendChild(cntSpan);
    row.appendChild(renBtn);
    row.appendChild(merBtn);
    row.appendChild(delBtn);
    el.appendChild(row);
  }
}

function filterTags(q) {
  const lower = q.toLowerCase();
  render(lower ? allTags.filter(t => t.tag.includes(lower)) : allTags);
}

async function renameTag(tag) {
  const newTag = prompt('Rename "' + tag + '" to:');
  if (!newTag || newTag.trim() === '' || newTag.trim() === tag) return;
  await fetch('__API_BASE__/api/tags/rename', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({old_tag: tag, new_tag: newTag.trim()})
  });
  load();
}

async function mergeTag(tag) {
  const target = prompt('Merge "' + tag + '" into which tag?');
  if (!target || target.trim() === '' || target.trim() === tag) return;
  await fetch('__API_BASE__/api/tags/merge', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({source_tag: tag, target_tag: target.trim()})
  });
  load();
}

async function deleteTag(tag) {
  if (!confirm('Delete tag "' + tag + '" from all images?')) return;
  await fetch('__API_BASE__/api/tags/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({tag})
  });
  load();
}

load();
</script>
</body>
</html>"""


def render_tags_html(api_base: str = "") -> str:
    """Return the tags UI HTML with the given API base prefix inserted."""
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav

    return (
        _HTML_TEMPLATE.replace("__API_BASE__", api_base)
        .replace("__NAV__", render_nav("tags"))
        .replace("__NAV_STYLES__", NAV_STYLES)
    )


def build_tags_router(db: "ProgressDB", api_base: str = "") -> Any:
    """Build and return a FastAPI APIRouter for the tags management UI.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into HTML (e.g. ``"/tags"`` or ``""``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, Body
        from fastapi.responses import HTMLResponse
        from pydantic import BaseModel
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for the tags UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    class _RenameBody(BaseModel):
        old_tag: str
        new_tag: str

    class _MergeBody(BaseModel):
        source_tag: str
        target_tag: str

    class _DeleteBody(BaseModel):
        tag: str

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_tags_html(api_base)

    @router.get("/api/tags")
    async def list_tags() -> list[dict]:
        counts = db.get_tag_counts()
        return [{"tag": t, "count": c} for t, c in counts]

    async def rename_tag(body: _RenameBody = Body(...)) -> dict:
        count = db.rename_tag(body.old_tag, body.new_tag)
        return {"ok": True, "count": count}

    rename_tag.__annotations__["body"] = _RenameBody
    router.post("/api/tags/rename")(rename_tag)

    async def merge_tags(body: _MergeBody = Body(...)) -> dict:
        count = db.merge_tags(body.source_tag, body.target_tag)
        return {"ok": True, "count": count}

    merge_tags.__annotations__["body"] = _MergeBody
    router.post("/api/tags/merge")(merge_tags)

    async def delete_tag(body: _DeleteBody = Body(...)) -> dict:
        count = db.delete_tag(body.tag)
        return {"ok": True, "count": count}

    delete_tag.__annotations__["body"] = _DeleteBody
    router.post("/api/tags/delete")(delete_tag)

    return router
