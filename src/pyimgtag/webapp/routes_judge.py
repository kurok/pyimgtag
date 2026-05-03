"""Judge scores ranking UI routes as a reusable APIRouter factory."""

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
    #grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
          gap:14px;padding:20px 32px}
    .score-card{background:var(--surface);border-radius:var(--radius-md);
                box-shadow:var(--shadow-md);padding:14px 16px;
                display:flex;flex-direction:column;gap:6px}
    .score-fname{font-family:ui-monospace,'SF Mono',monospace;font-size:12px;
                 font-weight:500;color:var(--text);white-space:nowrap;
                 overflow:hidden;text-overflow:ellipsis}
    .score-sub{font-size:11px;color:var(--muted)}
    .score-verdict{font-size:11px;color:var(--muted);font-style:italic;
                   white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  </style>
</head>
<body>
__NAV__
<div class="page-hdr">
  <h1 class="page-title">Judge</h1>
  <span id="status" class="page-meta">Loading\u2026</span>
</div>
<div id="grid"></div>
<script>
function tier(s) {
  if (s >= 8) return ['Excellent','tier-excellent'];
  if (s >= 6) return ['Good','tier-good'];
  if (s >= 4) return ['Average','tier-average'];
  return ['Poor','tier-poor'];
}

async function load() {
  const r = await fetch('__API_BASE__/api/scores');
  const scores = await r.json();
  document.getElementById('status').textContent = scores.length + ' scored';
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  for (const s of scores) {
    const card = document.createElement('div');
    card.className = 'score-card';

    const fname = document.createElement('div');
    fname.className = 'score-fname';
    fname.title = s.file_path;
    fname.textContent = s.file_name;
    card.appendChild(fname);

    const barRow = document.createElement('div');
    barRow.className = 'score-bar-row';
    const barBg = document.createElement('div');
    barBg.className = 'score-bar-bg';
    const barFill = document.createElement('div');
    barFill.className = 'score-bar-fill';
    barFill.style.width =
      Math.min(100, Math.round((s.weighted_score / 10) * 100)) + '%';
    barBg.appendChild(barFill);
    const scoreValEl = document.createElement('span');
    scoreValEl.className = 'score-val';
    scoreValEl.textContent = String(Math.round(s.weighted_score)) + '/10';
    barRow.appendChild(barBg);
    barRow.appendChild(scoreValEl);
    card.appendChild(barRow);

    const [label, cls] = tier(s.weighted_score);
    const tierEl = document.createElement('span');
    tierEl.className = 'score-tier ' + cls;
    tierEl.textContent = label;
    card.appendChild(tierEl);

    if (s.scored_at) {
      const sub = document.createElement('div');
      sub.className = 'score-sub';
      sub.textContent = 'scored ' + s.scored_at;
      card.appendChild(sub);
    }

    // Prefer the natural-language reason from the simple-prompt path;
    // fall back to the older verdict field on legacy DB rows.
    const explanation = s.reason || s.verdict;
    if (explanation) {
      const verdict = document.createElement('div');
      verdict.className = 'score-verdict';
      verdict.title = explanation;
      verdict.textContent = explanation;
      card.appendChild(verdict);
    }
    grid.appendChild(card);
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


def build_judge_router(db: "ProgressDB", api_base: str = "") -> Any:
    """Build and return a FastAPI APIRouter for the judge scores ranking UI.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into HTML (e.g. ``"/judge"`` or ``""``).

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
            "fastapi is required for the judge UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_judge_html(api_base)

    @router.get("/api/scores")
    async def list_scores(limit: int | None = 200) -> list[dict]:
        return db.get_all_judge_results(limit=limit)

    return router
