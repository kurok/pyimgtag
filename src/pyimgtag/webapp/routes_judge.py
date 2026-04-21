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
    :root{--bg:#121212;--surface:#1e1e1e;--card:#252525;--accent:#bb86fc;
          --text:#e0e0e0;--muted:#888;--border:#333;
          --ok:#81c784;--warn:#f9a825;--danger:#cf6679}
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text)}
    __NAV_STYLES__
    header{background:var(--surface);border-bottom:1px solid var(--border);
           padding:.75rem 1.5rem;display:flex;align-items:center;gap:1rem}
    h1{font-size:1rem;font-weight:600;color:var(--accent)}
    #status{margin-left:auto;font-size:.8rem;color:var(--muted)}
    #grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));
          gap:.75rem;padding:1rem 1.5rem}
    .card{background:var(--card);border-radius:8px;border:1px solid var(--border);
          padding:.75rem;display:flex;flex-direction:column;gap:.4rem}
    .fname{font-size:.85rem;font-weight:600;overflow:hidden;text-overflow:ellipsis;
           white-space:nowrap}
    .score-row{display:flex;align-items:center;gap:.5rem}
    .bar-bg{flex:1;background:#333;border-radius:999px;height:6px}
    .bar-fill{background:var(--accent);border-radius:999px;height:6px}
    .score-val{font-size:.9rem;font-weight:700;min-width:2.5rem;text-align:right}
    .sub-scores{font-size:.72rem;color:var(--muted)}
    .badge{font-size:.65rem;padding:.1rem .35rem;border-radius:3px;
           font-weight:700;text-transform:uppercase;display:inline-block;width:fit-content}
    .badge-excellent{background:#0d3320;color:var(--ok)}
    .badge-good{background:#1a2e10;color:#a5d6a7}
    .badge-average{background:#2e2a00;color:var(--warn)}
    .badge-poor{background:#2e0e0e;color:var(--danger)}
    .verdict{font-size:.72rem;color:var(--muted);font-style:italic;
             overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  </style>
</head>
<body>
__NAV__
<header>
  <h1>pyimgtag &mdash; Judge Rankings</h1>
  <span id="status">Loading&hellip;</span>
</header>
<div id="grid"></div>
<script>
async function load() {
  const resp = await fetch('__API_BASE__/api/scores');
  const scores = await resp.json();
  document.getElementById('status').textContent = scores.length + ' scored image(s)';
  const el = document.getElementById('grid');
  el.innerHTML = '';
  for (const s of scores) {
    const pct = Math.min(100, Math.round((s.weighted_score / 10) * 100));

    const card = document.createElement('div');
    card.className = 'card';

    const fname = document.createElement('div');
    fname.className = 'fname';
    fname.title = s.file_path;
    fname.textContent = s.file_name;

    const scoreRow = document.createElement('div');
    scoreRow.className = 'score-row';
    const barBg = document.createElement('div');
    barBg.className = 'bar-bg';
    const barFill = document.createElement('div');
    barFill.className = 'bar-fill';
    barFill.style.width = pct + '%';
    barBg.appendChild(barFill);
    const scoreVal = document.createElement('div');
    scoreVal.className = 'score-val';
    scoreVal.textContent = s.weighted_score.toFixed(1);
    scoreRow.appendChild(barBg);
    scoreRow.appendChild(scoreVal);

    const badge = document.createElement('span');
    badge.className = 'badge badge-' + tier(s.weighted_score);
    badge.textContent = tierLabel(s.weighted_score);

    const sub = document.createElement('div');
    sub.className = 'sub-scores';
    sub.textContent = 'core\u00a0' + (s.core_score || 0).toFixed(1)
      + '\u2002visible\u00a0' + (s.visible_score || 0).toFixed(1);

    card.appendChild(fname);
    card.appendChild(scoreRow);
    card.appendChild(badge);
    card.appendChild(sub);
    if (s.verdict) {
      const v = document.createElement('div');
      v.className = 'verdict';
      v.title = s.verdict;
      v.textContent = s.verdict;
      card.appendChild(v);
    }
    el.appendChild(card);
  }
}

function tier(score) {
  if (score >= 7.5) return 'excellent';
  if (score >= 5.5) return 'good';
  if (score >= 3.5) return 'average';
  return 'poor';
}

function tierLabel(score) {
  if (score >= 7.5) return 'Excellent';
  if (score >= 5.5) return 'Good';
  if (score >= 3.5) return 'Average';
  return 'Poor';
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
            "fastapi is required for the judge UI. "
            "Install with: pip install 'pyimgtag[review]'"
        ) from exc

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_judge_html(api_base)

    @router.get("/api/scores")
    async def list_scores(limit: int | None = 200) -> list[dict]:
        return db.get_all_judge_results(limit=limit)

    return router
