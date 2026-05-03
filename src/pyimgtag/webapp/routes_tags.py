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
    __NAV_STYLES__
    .tags-toolbar{padding:16px 32px;display:flex;align-items:center;gap:12px}
    .search-inp{padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);
                font-size:13px;font-family:inherit;color:var(--text);background:var(--surface);
                outline:none;box-shadow:var(--shadow-sm);width:260px;
                transition:border-color .15s,box-shadow .15s}
    .search-inp:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(0,113,227,.15)}
    #list{padding:0 32px 32px;display:flex;flex-direction:column;gap:4px}
    .row{display:flex;align-items:center;gap:8px;padding:10px 14px;
         background:var(--surface);border-radius:var(--radius-sm);
         box-shadow:var(--shadow-sm);transition:box-shadow .15s}
    .row:hover{box-shadow:var(--shadow-md)}
    .tag-name{flex:1;font-size:13px;font-weight:500}
    .tag-name a{color:var(--text);text-decoration:none;
                 border-bottom:1px dotted transparent;cursor:pointer}
    .tag-name a:hover{color:var(--accent);border-bottom-color:var(--accent)}
    .cnt{font-size:12px;color:var(--muted);min-width:56px;text-align:right}
    .row-btn{padding:5px 10px;font-size:12px;font-weight:500;border-radius:6px;
             border:1px solid var(--border);background:transparent;color:var(--muted);
             cursor:pointer;transition:all .15s}
    .row-btn:hover{border-color:var(--accent);color:var(--accent)}
    .row-btn.danger{color:var(--danger);border-color:rgba(255,59,48,.3)}
    .row-btn.danger:hover{background:rgba(255,59,48,.05)}
  </style>
</head>
<body>
__NAV__
__MODAL_HTML__
__MODAL_JS__
<div class="page-hdr">
  <h1 class="page-title">Tags</h1>
  <span id="status" class="page-meta">Loading\u2026</span>
</div>
<div class="tags-toolbar">
  <input class="search-inp" id="search" placeholder="Filter tags\u2026"
         oninput="filterTags(this.value)">
</div>
<div id="list"></div>
<script>
let allTags = [];

async function load() {
  const r = await fetch('__API_BASE__/api/tags');
  allTags = await r.json();
  document.getElementById('status').textContent = allTags.length + ' tags';
  render(allTags);
}

function render(tags) {
  const el = document.getElementById('list');
  el.innerHTML = '';
  for (const t of tags) {
    const row = document.createElement('div');
    row.className = 'row';

    const nameEl = document.createElement('span');
    nameEl.className = 'tag-name';
    // Click the tag name to open the Query page filtered to images with
    // this tag. textContent on the anchor keeps unescaped tag values
    // safe (no innerHTML interpolation).
    const nameLink = document.createElement('a');
    nameLink.href = '/query?tag=' + encodeURIComponent(t.tag);
    nameLink.title = 'Search images with this tag';
    nameLink.textContent = t.tag;
    nameEl.appendChild(nameLink);
    const cntEl = document.createElement('span');
    cntEl.className = 'cnt';
    cntEl.textContent = t.count + '\u00a0img';

    const renBtn = document.createElement('button');
    renBtn.className = 'row-btn';
    renBtn.textContent = 'Rename';
    renBtn.addEventListener('click', () => {
      openModal(
        'Rename tag',
        'Renaming "' + t.tag + '" will update all ' + t.count + ' images.',
        '<input class="inp" id="m-inp" />',
        'Rename', 'btn-primary',
        async () => {
          const val = document.getElementById('m-inp').value.trim();
          if (!val || val === t.tag) return;
          await fetch('__API_BASE__/api/tags/rename', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({old_tag: t.tag, new_tag: val}),
          });
          closeModal(); load();
        }
      );
      document.getElementById('m-inp').value = t.tag;
      setTimeout(() => document.getElementById('m-inp').focus(), 50);
    });

    const merBtn = document.createElement('button');
    merBtn.className = 'row-btn';
    merBtn.textContent = 'Merge into';
    merBtn.addEventListener('click', () => {
      openModal(
        'Merge tag',
        'Merge "' + t.tag + '" into which tag? The source tag is removed.',
        '<input class="inp" id="m-inp" placeholder="Target tag" />',
        'Merge', 'btn-primary',
        async () => {
          const val = document.getElementById('m-inp').value.trim();
          if (!val || val === t.tag) return;
          await fetch('__API_BASE__/api/tags/merge', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({source_tag: t.tag, target_tag: val}),
          });
          closeModal(); load();
        }
      );
      setTimeout(() => document.getElementById('m-inp').focus(), 50);
    });

    const delBtn = document.createElement('button');
    delBtn.className = 'row-btn danger';
    delBtn.textContent = 'Delete';
    delBtn.addEventListener('click', () => {
      openModal(
        'Delete tag',
        'Remove "' + t.tag + '" from all ' + t.count + ' images? Cannot be undone.',
        '',
        'Delete', 'btn-danger-text',
        async () => {
          await fetch('__API_BASE__/api/tags/delete', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({tag: t.tag}),
          });
          closeModal(); load();
        }
      );
    });

    row.appendChild(nameEl);
    row.appendChild(cntEl);
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

load();
</script>
</body>
</html>"""


def render_tags_html(api_base: str = "") -> str:
    """Return the tags UI HTML with the given API base prefix inserted."""
    from pyimgtag.webapp.nav import MODAL_HTML, MODAL_JS, NAV_STYLES, render_nav

    return (
        _HTML_TEMPLATE.replace("__API_BASE__", api_base)
        .replace("__NAV__", render_nav("tags"))
        .replace("__NAV_STYLES__", NAV_STYLES)
        .replace("__MODAL_HTML__", MODAL_HTML)
        .replace("__MODAL_JS__", MODAL_JS)
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
