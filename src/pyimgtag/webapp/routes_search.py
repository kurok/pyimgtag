"""Semantic-search UI routes as a reusable APIRouter factory.

The page is a thin shell over the same retrieval path the CLI uses: text goes
to the local model, cosine scores come back, thumbnails are served by the
existing ``/review/thumbnail`` endpoint. Nothing about a query leaves the
machine.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pyimgtag.search.model_cache import ModelDownloadError
from pyimgtag.webapp.nav import DESIGN_CSS as NAV_STYLES
from pyimgtag.webapp.nav import render_nav

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag Search</title>
  <style>
    __NAV_STYLES__
    .search-card{margin:20px 32px 0;background:var(--surface);
                 border-radius:var(--radius-md);box-shadow:var(--shadow-sm);padding:18px 20px}
    .search-row{display:flex;gap:12px;align-items:center}
    .search-row .inp{flex:1;font-size:15px}
    .filter-row{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;margin-top:14px}
    .field{display:flex;flex-direction:column;gap:5px}
    .field label{font-size:11px;font-weight:600;color:var(--muted);
                 text-transform:uppercase;letter-spacing:.4px}
    .field input{padding:8px 10px;border:1px solid var(--border);
                 border-radius:var(--radius-sm);font-size:13px;font-family:inherit;
                 color:var(--text);background:var(--bg);outline:none}
    .field input[type=number]{width:90px}
    #status{padding:14px 32px 0;font-size:13px;color:var(--muted)}
    #grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));
          gap:16px;padding:16px 32px 40px}
    .card{background:var(--surface);border-radius:var(--radius-md);
          box-shadow:var(--shadow-sm);overflow:hidden;cursor:pointer;
          transition:transform .15s,box-shadow .15s}
    .card:hover{transform:translateY(-2px);box-shadow:var(--shadow-md)}
    .card img{width:100%;height:150px;object-fit:cover;display:block;background:var(--bg)}
    .card-meta{padding:8px 10px}
    .card-score{font-size:12px;font-weight:700;color:var(--accent);
                font-variant-numeric:tabular-nums}
    .card-name{font-size:11px;color:var(--muted);word-break:break-all;margin-top:2px}
    .empty{padding:40px 32px;color:var(--muted);font-size:14px}
    .empty code{background:var(--surface);padding:2px 6px;border-radius:4px;
                font-family:ui-monospace,'SF Mono',monospace;font-size:12px}
    #lightbox{position:fixed;inset:0;background:rgba(0,0,0,.88);display:none;
              align-items:center;justify-content:center;z-index:200}
    #lightbox.open{display:flex}
    #lightbox img{max-width:92vw;max-height:88vh;border-radius:var(--radius-sm)}
  </style>
</head>
<body>
__NAV__
<div class="page-hdr">
  <h1 class="page-title">Search</h1>
  <span class="page-meta">Describe the photo you are looking for &mdash; everything
        runs on this machine</span>
</div>

<div class="search-card">
  <div class="search-row">
    <input id="q" class="inp" type="search" autofocus
           placeholder="foggy morning on a red bridge">
    <button id="go" class="btn btn-primary">Search</button>
  </div>
  <div class="filter-row">
    <div class="field"><label for="f-person">Person</label>
      <input id="f-person" type="text" placeholder="Alice"></div>
    <div class="field"><label for="f-tag">Tag</label>
      <input id="f-tag" type="text" placeholder="sunset"></div>
    <div class="field"><label for="f-city">City</label>
      <input id="f-city" type="text" placeholder="Lisbon"></div>
    <div class="field"><label for="f-year">Year</label>
      <input id="f-year" type="text" placeholder="2024" size="6"></div>
    <div class="field"><label for="f-min-score">Min judge score</label>
      <input id="f-min-score" type="number" min="1" max="10"></div>
    <div class="field"><label for="f-top">Results</label>
      <input id="f-top" type="number" min="1" max="200" value="30"></div>
  </div>
</div>

<div id="status"></div>
<div id="grid"></div>
<div id="lightbox" onclick="this.classList.remove('open')"><img id="lightbox-img" alt=""></div>

<script>
const API = '__API_BASE__';
const grid = document.getElementById('grid');
const statusEl = document.getElementById('status');

function val(id) { return document.getElementById(id).value.trim(); }

async function runSearch() {
  const q = val('q');
  if (!q) {
    statusEl.textContent = 'Type something to search for.';
    grid.replaceChildren();
    return;
  }
  statusEl.textContent = 'Searching\\u2026';
  statusEl.style.color = '';
  grid.replaceChildren();

  const params = new URLSearchParams({ q: q, top: val('f-top') || '30' });
  for (const [id, key] of [['f-person','person'],['f-tag','tag'],['f-city','city'],
                           ['f-year','year'],['f-min-score','min_score']]) {
    if (val(id)) params.set(key, val(id));
  }

  let payload;
  try {
    const res = await fetch(API + '/api/search?' + params.toString());
    payload = await res.json();
    if (!res.ok) throw new Error(payload.detail || res.statusText);
  } catch (err) {
    statusEl.textContent = err.message;
    statusEl.style.color = 'var(--danger)';
    return;
  }

  if (payload.available === false) {
    // textContent, not innerHTML: these strings can carry a filesystem path
    // from the server's environment, and nothing here needs to be markup.
    statusEl.textContent = '';
    const box = document.createElement('div');
    box.className = 'empty';
    box.textContent = payload.message;
    if (payload.command) {
      const code = document.createElement('code');
      code.textContent = payload.command;
      box.appendChild(document.createElement('br'));
      box.appendChild(code);
    }
    grid.appendChild(box);
    return;
  }
  const hits = payload.results || [];
  statusEl.textContent = hits.length + ' result' + (hits.length === 1 ? '' : 's');
  for (const hit of hits) {
    const card = document.createElement('div');
    card.className = 'card';
    const name = hit.file_path.split('/').pop();
    const thumb = '/review/thumbnail?path=' + encodeURIComponent(hit.file_path) + '&size=400';
    card.innerHTML =
      '<img loading="lazy" alt="" src="' + thumb + '">' +
      '<div class="card-meta"><div class="card-score">' + hit.score.toFixed(3) + '</div>' +
      '<div class="card-name"></div></div>';
    card.querySelector('.card-name').textContent = name;
    card.onclick = () => {
      document.getElementById('lightbox-img').src = thumb.replace('size=400', 'size=1400');
      document.getElementById('lightbox').classList.add('open');
    };
    grid.appendChild(card);
  }
  if (!hits.length) {
    const box = document.createElement('div');
    box.className = 'empty';
    box.textContent = 'Nothing matched that description.';
    grid.appendChild(box);
  }
}

document.getElementById('go').onclick = runSearch;
document.getElementById('q').addEventListener('keydown', e => {
  if (e.key === 'Enter') runSearch();
});
</script>
</body>
</html>"""


def render_search_html(api_base: str = "") -> str:
    """Render the search page with the nav shell and API base injected."""
    return (
        _HTML_TEMPLATE.replace("__API_BASE__", api_base)
        .replace("__NAV__", render_nav("search"))
        .replace("__NAV_STYLES__", NAV_STYLES)
    )


def build_search_router(db: ProgressDB, api_base: str = "") -> Any:
    """Build a FastAPI APIRouter for the semantic-search UI.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into the HTML (e.g. ``"/search"``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, HTTPException, Query
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for the search UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    router = APIRouter()
    # One embedder for the process: loading the text tower costs ~64 MB, and
    # reloading it per keystroke-driven request would dominate the search.
    cached: dict[str, Any] = {}

    def _embedder() -> Any:
        if "embedder" not in cached:
            from pyimgtag.search.embedder import load_embedder

            cached["embedder"] = load_embedder(progress=False)
        return cached["embedder"]

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_search_html(api_base)

    @router.get("/api/search")
    async def api_search(
        q: str = Query(..., min_length=1),
        top: int = 30,
        person: str | None = None,
        tag: str | None = None,
        city: str | None = None,
        year: str | None = None,
        min_score: int | None = None,
    ) -> dict:
        """Rank indexed photos against *q*, inside the structured filters."""
        from pyimgtag.filters import parse_year

        stats = db.embedding_stats()
        if not stats["count"]:
            # Not an error: a library that has never been indexed is a normal
            # state, and the page should say what to do about it.
            return {
                "available": False,
                "message": "No semantic index yet. Build one with:",
                "command": "pyimgtag index --input-dir <DIR>",
            }

        # Nothing derived from an exception is returned. The two recoverable
        # setup failures are answered with strings this module owns, so no
        # exception text can reach a browser by accident (py/stack-trace-exposure).
        try:
            embedder = _embedder()
        except ImportError:
            logger.warning("Semantic search is unavailable: the [search] extra is missing")
            return {
                "available": False,
                "message": "Semantic search needs the [search] extra. Install it with:",
                "command": "pip install 'pyimgtag[search]'",
            }
        except ModelDownloadError as exc:
            logger.warning("Semantic search could not fetch a model: %s", exc.reason)
            return {
                "available": False,
                "message": "The search model is not available on this machine.",
                "command": exc.command,
            }
        except Exception:
            logger.exception("Semantic search could not load its model")
            return {
                "available": False,
                "message": (
                    "The search model could not be loaded. See the server log for details."
                ),
            }

        allowed: set[str] | None = None
        if any(v is not None for v in (tag, city, year, min_score)):
            date_prefix = None
            if year:
                try:
                    date_prefix = parse_year(year)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            rows = db.query_images(
                tag=tag, city=city, date_prefix=date_prefix, min_judge_score=min_score
            )
            allowed = {r["file_path"] for r in rows}
        if person:
            people = db.paths_for_person_label(person)
            allowed = people if allowed is None else (allowed & people)

        hits = db.search_similar(
            embedder.embed_text(q), limit=max(1, min(top, 200)), allowed_paths=allowed
        )
        return {
            "available": True,
            "results": [{"file_path": p, "score": s} for p, s in hits],
        }

    return router
