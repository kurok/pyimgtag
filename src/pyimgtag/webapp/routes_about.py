"""About page + lightweight PyPI update-check endpoint.

Exposes:

- ``GET  /about``       — HTML page with the running version, curated
                          repo + wiki links, and a CTA panel pointing
                          at the GitHub wiki in a new tab. (GitHub
                          serves the wiki with ``X-Frame-Options: DENY``
                          so it cannot be iframed.)
- ``GET  /about/api/version`` — JSON ``{installed, latest, update}`` for
                          the small badge in the nav. ``latest`` is the
                          current PyPI release; ``update`` is True iff
                          the running version is older.

The PyPI lookup is best-effort (3-second timeout, single retry) and
cached for an hour so loading the dashboard doesn't issue an HTTP call
every refresh.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_PYPI_URL = "https://pypi.org/pypi/pyimgtag/json"
_CACHE_TTL_SECONDS = 3600.0
_CACHE: dict[str, Any] = {"at": 0.0, "value": None}


def _parse_version(s: str) -> tuple[int, ...]:
    """Tolerant version-tuple parser.

    Handles ``0.10.0`` (3 ints), ``1.2`` (2 ints), and falls back to a
    trailing-zero pad. Anything non-numeric in a segment short-circuits
    that segment to 0 so a pre-release suffix never crashes the compare.
    """
    parts: list[int] = []
    for raw in s.split("."):
        digits = ""
        for ch in raw:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _is_newer(latest: str, installed: str) -> bool:
    return _parse_version(latest) > _parse_version(installed)


def _fetch_latest_pypi(timeout: float = 3.0) -> str | None:
    """Return the current latest released version on PyPI, or None on failure."""
    try:
        import requests
    except ImportError:
        return None
    try:
        resp = requests.get(_PYPI_URL, timeout=timeout)
        resp.raise_for_status()
        info = resp.json().get("info") or {}
        version = info.get("version")
        return version if isinstance(version, str) and version else None
    except (requests.RequestException, ValueError) as exc:
        logger.debug("PyPI version lookup failed: %s", exc)
        return None


def _latest_version(now: float | None = None) -> str | None:
    """Return the latest version, hitting PyPI at most once per hour."""
    if now is None:
        now = time.monotonic()
    if _CACHE["value"] is not None and (now - _CACHE["at"]) < _CACHE_TTL_SECONDS:
        return _CACHE["value"]  # type: ignore[return-value]
    fresh = _fetch_latest_pypi()
    if fresh is not None:
        _CACHE["value"] = fresh
        _CACHE["at"] = now
    return fresh


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag — About</title>
  <style>
    __NAV_STYLES__
    .about{max-width:920px;margin:24px auto;padding:0 24px}
    .about h2{font-size:18px;font-weight:600;margin:24px 0 8px}
    .about p{font-size:14px;line-height:1.55;color:var(--text)}
    .about ul{font-size:14px;line-height:1.6;padding-left:22px}
    .about a{color:var(--accent);text-decoration:none}
    .about a:hover{text-decoration:underline}
    .meta{display:flex;flex-wrap:wrap;gap:16px;margin:8px 0 18px}
    .meta-card{background:var(--surface);padding:12px 16px;border-radius:8px;
               border:1px solid var(--border);min-width:160px}
    .meta-card .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.7px;
                    color:var(--muted)}
    .meta-card .val{font-size:15px;font-weight:600;margin-top:2px;
                    font-family:ui-monospace,'SF Mono',monospace}
    .meta-card.update .val{color:var(--accent)}
    .wiki-cta{display:flex;flex-wrap:wrap;align-items:center;gap:18px;
              padding:18px 22px;border:1px solid var(--border);border-radius:8px;
              background:var(--surface)}
    .wiki-cta p{margin:0;font-size:13px;color:var(--text);flex:1;min-width:240px}
    .wiki-cta .btn-row{display:flex;gap:10px;flex-wrap:wrap}
    /* The buttons live inside ``.about`` which has its own ``a`` rule
       (``color:var(--accent)``). That rule is class+element specificity
       (1,1) and beats a plain ``.wiki-btn`` (1,0) — the primary button
       ends up with blue text on a blue background. Scope the rules to
       ``.about .wiki-btn`` so the specificity (1,2,0) wins. */
    .about .wiki-btn{display:inline-block;padding:8px 16px;border-radius:6px;
                     background:var(--accent);color:#fff;text-decoration:none;
                     font-size:13px;font-weight:500}
    .about .wiki-btn:hover{filter:brightness(1.08);text-decoration:none;
                           color:#fff}
    .about .wiki-btn.secondary{background:transparent;color:var(--accent);
                               border:1px solid var(--accent)}
    .about .wiki-btn.secondary:hover{color:var(--accent)}
  </style>
</head>
<body>
__NAV__
<div class="about">
  <h1 class="page-title" style="padding:8px 0 4px">About pyimgtag</h1>
  <p>Tag and rate your photos with a local Ollama vision model (or any of
  the supported cloud backends). All metadata stays in a local SQLite
  progress DB; only EXIF GPS is ever sent off-device, and only to the
  Nominatim geocoder.</p>

  <div class="meta">
    <div class="meta-card">
      <div class="lbl">Installed</div>
      <div class="val" id="installed-ver">__VERSION__</div>
    </div>
    <div class="meta-card" id="latest-card">
      <div class="lbl">Latest on PyPI</div>
      <div class="val" id="latest-ver">…</div>
    </div>
    <div class="meta-card" id="status-card">
      <div class="lbl">Status</div>
      <div class="val" id="update-status">checking…</div>
    </div>
  </div>

  <h2>Links</h2>
  <ul>
    <li><a href="https://github.com/kurok/pyimgtag"
           target="_blank" rel="noopener">GitHub repo</a></li>
    <li><a href="https://github.com/kurok/pyimgtag/wiki"
           target="_blank" rel="noopener">Wiki (full documentation)</a></li>
    <li><a href="https://github.com/kurok/pyimgtag/wiki/Use-Case-Diagrams"
           target="_blank" rel="noopener">Use-case diagrams</a></li>
    <li><a href="https://github.com/kurok/pyimgtag/wiki/Choosing-a-Backend"
           target="_blank" rel="noopener">Choosing a backend</a></li>
    <li><a href="https://github.com/kurok/pyimgtag/blob/main/CHANGELOG.md"
           target="_blank" rel="noopener">Changelog</a></li>
    <li><a href="https://github.com/kurok/pyimgtag/releases"
           target="_blank" rel="noopener">All releases</a></li>
  </ul>

  <h2>Wiki</h2>
  <div class="wiki-cta">
    <p>The full documentation lives in the GitHub wiki — guides, mermaid
    use-case diagrams, and the backend chooser. GitHub blocks the wiki
    from being embedded in an iframe, so it opens in a new tab.</p>
    <div class="btn-row">
      <a class="wiki-btn"
         href="https://github.com/kurok/pyimgtag/wiki"
         target="_blank" rel="noopener">Open wiki ↗</a>
      <a class="wiki-btn secondary"
         href="https://github.com/kurok/pyimgtag/wiki/Use-Case-Diagrams"
         target="_blank" rel="noopener">Use-case diagrams ↗</a>
    </div>
  </div>
</div>
<script>
async function checkVersion() {
  try {
    const r = await fetch('/about/api/version');
    if (!r.ok) return;
    const d = await r.json();
    document.getElementById('latest-ver').textContent = d.latest || '—';
    const status = document.getElementById('update-status');
    if (d.update) {
      status.textContent = 'update available';
      document.getElementById('latest-card').classList.add('update');
      // Mark the nav badge so every page surfaces the upgrade prompt.
      document.querySelectorAll('a.nav-version').forEach(el => {
        el.classList.add('update-available');
        el.title = 'New release available: v' + d.latest;
      });
    } else if (d.latest) {
      status.textContent = 'up to date';
    } else {
      status.textContent = 'PyPI lookup failed';
    }
  } catch (_) { /* network / parse error — leave the placeholder */ }
}
checkVersion();
</script>
</body>
</html>"""


def render_about_html() -> str:
    from pyimgtag import __version__
    from pyimgtag.webapp.nav import NAV_STYLES, render_nav

    return (
        _HTML_TEMPLATE.replace("__NAV__", render_nav("about"))
        .replace("__NAV_STYLES__", NAV_STYLES)
        .replace("__VERSION__", __version__)
    )


def build_about_router() -> Any:
    """Return an APIRouter exposing the About page + version-check JSON."""
    try:
        from fastapi import APIRouter
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for the about page. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def about() -> str:
        return render_about_html()

    @router.get("/api/version")
    async def version() -> dict:
        from pyimgtag import __version__

        latest = _latest_version()
        update = bool(latest and _is_newer(latest, __version__))
        return {"installed": __version__, "latest": latest, "update": update}

    return router
