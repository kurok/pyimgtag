"""Events and trips UI routes as a reusable APIRouter factory.

Read-and-rename only: detection and album materialization stay in the CLI,
where a run that touches Apple Photos or the filesystem is an explicit act
rather than a side effect of loading a page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyimgtag.webapp.nav import DESIGN_CSS as NAV_STYLES
from pyimgtag.webapp.nav import render_nav

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag Events</title>
  <style>
    __NAV_STYLES__
    #summary{padding:14px 32px 0;font-size:13px;color:var(--muted)}
    #cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));
           gap:18px;padding:18px 32px 40px}
    .ev{background:var(--surface);border-radius:var(--radius-md);
        box-shadow:var(--shadow-sm);overflow:hidden;transition:transform .15s,box-shadow .15s}
    .ev:hover{transform:translateY(-2px);box-shadow:var(--shadow-md)}
    .ev-cover{width:100%;height:150px;object-fit:cover;display:block;
              background:var(--bg);cursor:pointer}
    .ev-body{padding:10px 12px 12px}
    .ev-name{font-size:14px;font-weight:600;color:var(--text);cursor:text}
    .ev-name[contenteditable="true"]:focus{outline:2px solid var(--accent);
                                           border-radius:4px;padding:1px 3px}
    .ev-meta{font-size:11px;color:var(--muted);margin-top:4px}
    .ev-trip{display:inline-block;background:#e8f0fe;color:#1a73e8;border-radius:4px;
             padding:1px 6px;font-size:10px;font-weight:600;margin-left:6px}
    .empty{padding:40px 32px;color:var(--muted);font-size:14px}
    .empty code{background:var(--surface);padding:2px 6px;border-radius:4px;
                font-family:ui-monospace,'SF Mono',monospace;font-size:12px}
    #detail{position:fixed;inset:0;background:rgba(0,0,0,.88);display:none;
            flex-direction:column;align-items:center;justify-content:center;gap:12px;z-index:200}
    #detail.open{display:flex}
    #detail-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));
                 gap:10px;max-width:90vw;max-height:74vh;overflow:auto;padding:4px}
    #detail-grid img{width:100%;height:110px;object-fit:cover;border-radius:6px;display:block}
    #detail-title{color:#fff;font-size:17px;font-weight:600}
  </style>
</head>
<body>
__NAV__
<div class="page-hdr">
  <h1 class="page-title">Events</h1>
  <span class="page-meta">Occasions and trips, clustered from capture time and place</span>
</div>
<div id="summary"></div>
<div id="cards"></div>

<div id="detail" onclick="if (event.target === this) this.classList.remove('open')">
  <div id="detail-title"></div>
  <div id="detail-grid"></div>
</div>

<script>
const API = '__API_BASE__';
const cards = document.getElementById('cards');
const summary = document.getElementById('summary');

function thumbFor(path, size) {
  return '/review/thumbnail?path=' + encodeURIComponent(path) + '&size=' + size;
}

async function load() {
  const res = await fetch(API + '/api/events');
  const payload = await res.json();
  const events = payload.events || [];
  const s = payload.stats || {};
  summary.textContent = events.length
    ? `${s.events} event(s), ${s.trips} trip(s), ${s.photos} photo(s) assigned`
    : '';

  cards.replaceChildren();
  if (!events.length) {
    const box = document.createElement('div');
    box.className = 'empty';
    box.textContent = 'No events yet. Detect them with:';
    const code = document.createElement('code');
    code.textContent = 'pyimgtag events detect';
    box.appendChild(document.createElement('br'));
    box.appendChild(code);
    cards.appendChild(box);
    return;
  }

  for (const ev of events) {
    const card = document.createElement('div');
    card.className = 'ev';

    const cover = document.createElement('img');
    cover.className = 'ev-cover';
    cover.loading = 'lazy';
    cover.alt = '';
    if (ev.cover) cover.src = thumbFor(ev.cover, 400);
    cover.onclick = () => openDetail(ev);

    const body = document.createElement('div');
    body.className = 'ev-body';

    const name = document.createElement('div');
    name.className = 'ev-name';
    name.contentEditable = 'true';
    name.spellcheck = false;
    name.textContent = ev.name;
    name.dataset.original = ev.name;
    name.onblur = () => rename(ev.id, name);
    name.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); name.blur(); } };

    const meta = document.createElement('div');
    meta.className = 'ev-meta';
    const dates = (ev.started_at || '').slice(0, 10) +
                  (((ev.ended_at || '').slice(0, 10) !== (ev.started_at || '').slice(0, 10))
                    ? ' – ' + (ev.ended_at || '').slice(0, 10) : '');
    meta.textContent = [dates, ev.place, ev.photo_count + ' photos']
      .filter(Boolean).join(' · ');
    if (ev.trip_id !== null && ev.trip_id !== undefined) {
      const trip = document.createElement('span');
      trip.className = 'ev-trip';
      trip.textContent = 'trip';
      meta.appendChild(trip);
    }

    body.appendChild(name);
    body.appendChild(meta);
    card.appendChild(cover);
    card.appendChild(body);
    cards.appendChild(card);
  }
}

async function rename(id, el) {
  const value = el.textContent.trim();
  if (!value || value === el.dataset.original) { el.textContent = el.dataset.original; return; }
  const res = await fetch(API + '/api/events/' + id + '/name', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: value}),
  });
  if (res.ok) { el.dataset.original = value; }
  else { el.textContent = el.dataset.original; }
}

async function openDetail(ev) {
  const res = await fetch(API + '/api/events/' + ev.id);
  const detail = await res.json();
  document.getElementById('detail-title').textContent = detail.name;
  const grid = document.getElementById('detail-grid');
  grid.replaceChildren();
  for (const path of detail.members || []) {
    const img = document.createElement('img');
    img.loading = 'lazy';
    img.alt = '';
    img.src = thumbFor(path, 300);
    grid.appendChild(img);
  }
  document.getElementById('detail').classList.add('open');
}

load();
</script>
</body>
</html>"""


def render_events_html(api_base: str = "") -> str:
    """Render the events page with the nav shell and API base injected."""
    return (
        _HTML_TEMPLATE.replace("__API_BASE__", api_base)
        .replace("__NAV__", render_nav("events"))
        .replace("__NAV_STYLES__", NAV_STYLES)
    )


def build_events_router(db: ProgressDB, api_base: str = "") -> Any:
    """Build a FastAPI APIRouter for the events UI.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into the HTML (e.g. ``"/events"``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, Body, HTTPException
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for the events UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_events_html(api_base)

    @router.get("/api/events")
    async def list_events() -> dict:
        """Every event with a cover photo for the card grid."""
        events = db.list_events()
        for event in events:
            members = sorted(db.event_paths(event["id"]))
            # First photo by path: stable between loads, which matters more
            # for a cover than picking the "best" one would.
            event["cover"] = members[0] if members else None
        return {"events": events, "stats": db.event_stats()}

    @router.get("/api/events/{event_id}")
    async def get_event(event_id: int) -> dict:
        event = db.get_event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no event with id {event_id}")
        return event

    @router.post("/api/events/{event_id}/name")
    async def rename_event(event_id: int, payload: dict = Body(...)) -> dict:
        name = str(payload.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name must not be empty")
        if not db.rename_event(event_id, name[:120]):
            raise HTTPException(status_code=404, detail=f"no event with id {event_id}")
        return {"ok": True, "id": event_id, "name": name[:120]}

    return router
