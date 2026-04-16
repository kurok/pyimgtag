"""Lightweight review UI server for pyimgtag (FastAPI)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

_THUMB_DIR = Path.home() / ".cache" / "pyimgtag" / "thumbs"

try:
    from pydantic import BaseModel as _BaseModel

    class _TagsBody(_BaseModel):
        file_path: str
        tags: list[str]

    class _CleanupBody(_BaseModel):
        file_path: str
        cleanup_class: str | None

except ImportError:
    _TagsBody = None  # type: ignore[assignment,misc]
    _CleanupBody = None  # type: ignore[assignment,misc]

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag Review</title>
  <style>
    :root {
      --bg:#121212; --surface:#1e1e1e; --card:#252525;
      --accent:#bb86fc; --danger:#cf6679; --warn:#f9a825; --ok:#81c784;
      --text:#e0e0e0; --muted:#888; --border:#333; --tag-bg:#1e3a5a;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
    header{position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--border);
           padding:.75rem 1.5rem;display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap}
    h1{font-size:1rem;font-weight:600;color:var(--accent);white-space:nowrap}
    #stats{font-size:.8rem;color:var(--muted);white-space:nowrap}
    .filters{display:flex;gap:.4rem}
    .fbtn{padding:.25rem .7rem;font-size:.8rem;border:1px solid var(--border);background:transparent;
          color:var(--muted);cursor:pointer;border-radius:999px;transition:all .15s}
    .fbtn:hover{color:var(--text);border-color:var(--text)}
    .fbtn.active{background:var(--accent);border-color:var(--accent);color:#000}
    #count{font-size:.8rem;color:var(--muted);margin-left:auto}
    #grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:.75rem;padding:1rem 1.5rem}
    .card{background:var(--card);border-radius:8px;overflow:hidden;border:1px solid var(--border);transition:border-color .15s}
    .card:hover{border-color:#555}
    .card.delete{border-color:var(--danger)}
    .card.review{border-color:var(--warn)}
    .thumb{width:100%;aspect-ratio:1;object-fit:cover;background:#1a1a1a;display:block}
    .body{padding:.6rem}
    .badge{display:inline-block;font-size:.65rem;padding:.1rem .4rem;border-radius:3px;
           margin-bottom:.4rem;font-weight:600;text-transform:uppercase}
    .badge.delete{background:var(--danger);color:#fff}
    .badge.review{background:var(--warn);color:#000}
    .fname{font-size:.72rem;color:var(--muted);white-space:nowrap;overflow:hidden;
           text-overflow:ellipsis;margin-bottom:.3rem}
    .summary{font-size:.72rem;color:var(--text);line-height:1.3;margin-bottom:.4rem;
             display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
    .tags{display:flex;flex-wrap:wrap;gap:.2rem;margin-bottom:.4rem;min-height:.5rem}
    .tag{font-size:.65rem;background:var(--tag-bg);padding:.15rem .4rem;border-radius:3px;
         cursor:pointer;border:1px solid transparent;transition:all .1s}
    .tag:hover{background:var(--danger);border-color:var(--danger)}
    .taginput{width:100%;background:transparent;border:1px dashed var(--border);color:var(--text);
              padding:.25rem .4rem;font-size:.72rem;border-radius:4px;margin-bottom:.4rem}
    .taginput:focus{outline:none;border-color:var(--accent)}
    .actions{display:flex;gap:.3rem}
    .abtn{flex:1;padding:.2rem;font-size:.65rem;border:1px solid var(--border);background:transparent;
          color:var(--muted);cursor:pointer;border-radius:3px;transition:all .1s}
    .abtn:hover{color:var(--text)}
    .abtn.keep:hover{color:var(--ok);border-color:var(--ok)}
    .abtn.rev:hover{color:var(--warn);border-color:var(--warn)}
    .abtn.del:hover{color:var(--danger);border-color:var(--danger)}
    #pager{display:flex;justify-content:center;align-items:center;gap:.75rem;padding:1rem}
    .pbtn{padding:.3rem .9rem;border:1px solid var(--border);background:transparent;
          color:var(--text);cursor:pointer;border-radius:4px;font-size:.85rem}
    .pbtn:disabled{opacity:.3;cursor:default}
    #msg{text-align:center;padding:3rem;color:var(--muted);font-size:.9rem}
  </style>
</head>
<body>
  <header>
    <h1>pyimgtag Review</h1>
    <div id="stats"></div>
    <div class="filters">
      <button class="fbtn active" data-filter="">All</button>
      <button class="fbtn" data-filter="delete">Delete</button>
      <button class="fbtn" data-filter="review">Review</button>
    </div>
    <div id="count"></div>
  </header>
  <div id="msg">Loading\u2026</div>
  <div id="grid" style="display:none"></div>
  <div id="pager"></div>
  <script>
  (() => {
    const PAGE = 50;
    let offset = 0, filter = '', total = 0;

    async function api(url, opts) {
      const r = await fetch(url, opts);
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    }

    async function patchTags(filePath, tags) {
      return api('/api/images/tags', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({file_path: filePath, tags}),
      });
    }

    async function patchCleanup(filePath, cleanupClass) {
      return api('/api/images/cleanup', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({file_path: filePath, cleanup_class: cleanupClass}),
      });
    }

    async function loadStats() {
      try {
        const s = await api('/api/stats');
        document.getElementById('stats').textContent =
          s.total + ' processed \u00b7 ' + s.ok + ' ok \u00b7 ' + s.error + ' errors';
      } catch (_) {}
    }

    async function load() {
      document.getElementById('msg').style.display = 'block';
      document.getElementById('msg').textContent = 'Loading\u2026';
      document.getElementById('grid').style.display = 'none';
      let url = '/api/images?limit=' + PAGE + '&offset=' + offset;
      if (filter) url += '&cleanup=' + encodeURIComponent(filter);
      try {
        const d = await api(url);
        total = d.total;
        document.getElementById('count').textContent =
          total ? total + ' image' + (total !== 1 ? 's' : '') : '';
        if (!d.items.length) {
          document.getElementById('msg').textContent = 'No images found.';
        } else {
          document.getElementById('msg').style.display = 'none';
          renderGrid(d.items);
          document.getElementById('grid').style.display = 'grid';
        }
        renderPager();
      } catch(e) {
        document.getElementById('msg').textContent = 'Error: ' + e.message;
      }
    }

    function renderGrid(items) {
      const g = document.getElementById('grid');
      g.innerHTML = '';
      items.forEach(img => g.appendChild(makeCard(img)));
    }

    function el(tag, cls) {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      return e;
    }

    function makeBadge(cls) {
      const b = el('div', 'badge ' + cls);
      b.textContent = cls;
      return b;
    }

    function makeCard(img) {
      const card = el('div', 'card' + (img.cleanup_class ? ' ' + img.cleanup_class : ''));
      card.dataset.path = img.file_path;

      const thumb = el('img', 'thumb');
      thumb.src = '/thumbnail?path=' + encodeURIComponent(img.file_path) + '&size=200';
      thumb.alt = img.file_name || '';
      thumb.loading = 'lazy';
      thumb.onerror = () => { thumb.style.background = '#1a1a1a'; thumb.removeAttribute('src'); };
      card.appendChild(thumb);

      const body = el('div', 'body');

      if (img.cleanup_class) body.appendChild(makeBadge(img.cleanup_class));

      const fname = el('div', 'fname');
      fname.textContent = img.file_name || img.file_path;
      fname.title = img.file_path;
      body.appendChild(fname);

      if (img.scene_summary) {
        const s = el('div', 'summary');
        s.textContent = img.scene_summary;
        body.appendChild(s);
      }

      const tagsDiv = el('div', 'tags');
      renderTags(tagsDiv, img.tags_list || [], img.file_path);
      body.appendChild(tagsDiv);

      const inp = el('input', 'taginput');
      inp.placeholder = 'add tag, press Enter';
      inp.addEventListener('keydown', async e => {
        if (e.key !== 'Enter') return;
        const tag = inp.value.trim().toLowerCase();
        if (!tag) return;
        inp.value = '';
        const cur = getTags(tagsDiv);
        if (cur.includes(tag)) return;
        const next = [...cur, tag];
        await patchTags(img.file_path, next);
        renderTags(tagsDiv, next, img.file_path);
      });
      body.appendChild(inp);

      const acts = el('div', 'actions');
      [['Keep', 'keep', null], ['Review', 'rev', 'review'], ['Delete', 'del', 'delete']]
        .forEach(([label, cls, val]) => {
          const b = el('button', 'abtn ' + cls);
          b.textContent = label;
          b.addEventListener('click', async () => {
            await patchCleanup(img.file_path, val);
            card.className = 'card' + (val ? ' ' + val : '');
            const old = body.querySelector('.badge');
            if (old) old.remove();
            if (val) body.insertBefore(makeBadge(val), body.firstChild);
          });
          acts.appendChild(b);
        });
      body.appendChild(acts);

      card.appendChild(body);
      return card;
    }

    function renderTags(container, tags, filePath) {
      container.innerHTML = '';
      tags.forEach(tag => {
        const s = el('span', 'tag');
        s.textContent = tag;
        s.dataset.tag = tag;
        s.title = 'Click to remove';
        s.addEventListener('click', async () => {
          const next = getTags(container).filter(t => t !== tag);
          await patchTags(filePath, next);
          renderTags(container, next, filePath);
        });
        container.appendChild(s);
      });
    }

    function getTags(container) {
      return Array.from(container.querySelectorAll('.tag')).map(s => s.dataset.tag);
    }

    function renderPager() {
      const p = document.getElementById('pager');
      const pages = Math.ceil(total / PAGE);
      const cur = Math.floor(offset / PAGE);
      if (pages <= 1) { p.innerHTML = ''; return; }
      p.innerHTML = '';
      const prev = el('button', 'pbtn');
      prev.textContent = '\u2190 Prev';
      prev.disabled = cur === 0;
      prev.addEventListener('click', () => { offset = (cur - 1) * PAGE; load(); scrollTo(0, 0); });
      p.appendChild(prev);
      const info = el('span', '');
      info.textContent = (cur + 1) + ' / ' + pages;
      info.style.color = 'var(--muted)';
      info.style.fontSize = '.85rem';
      p.appendChild(info);
      const next = el('button', 'pbtn');
      next.textContent = 'Next \u2192';
      next.disabled = offset + PAGE >= total;
      next.addEventListener('click', () => { offset = (cur + 1) * PAGE; load(); scrollTo(0, 0); });
      p.appendChild(next);
    }

    document.querySelectorAll('.fbtn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.fbtn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        filter = btn.dataset.filter;
        offset = 0;
        load();
      });
    });

    loadStats();
    load();
  })();
  </script>
</body>
</html>"""


def _make_thumbnail(image_path: str, size: int) -> bytes | None:
    """Return cached JPEG thumbnail bytes. Returns None on any failure."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return None

    try:
        from pillow_heif import register_heif_opener  # type: ignore[import-untyped]

        register_heif_opener()
    except ImportError:
        pass

    cache_key = hashlib.sha256(f"{image_path}:{size}".encode()).hexdigest()
    cache_path = _THUMB_DIR / f"{cache_key}.jpg"

    if cache_path.exists():
        return cache_path.read_bytes()

    try:
        with Image.open(image_path) as img:
            img.thumbnail((size, size), Image.Resampling.LANCZOS)
            img_rgb = img.convert("RGB")
            buf = io.BytesIO()
            img_rgb.save(buf, format="JPEG", quality=75)
            data = buf.getvalue()
        _THUMB_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
        return data
    except (OSError, UnidentifiedImageError):
        return None
    except Exception:  # noqa: BLE001 — catch-all for PIL/HEIC decode failures
        return None


def create_app(db_path: str | Path | None = None) -> Any:
    """Create and return the FastAPI application.

    Args:
        db_path: Path to the SQLite progress DB. Defaults to the standard location.

    Returns:
        A FastAPI application instance.

    Raises:
        ImportError: If fastapi or pydantic are not installed.
    """
    try:
        from fastapi import Body, FastAPI, Query, Response
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi and uvicorn are required for the review UI. "
            "Install with: pip install 'pyimgtag[review]'"
        ) from exc

    from pyimgtag.progress_db import ProgressDB

    db = ProgressDB(db_path=db_path)
    app = FastAPI(title="pyimgtag Review", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _HTML

    @app.get("/api/stats")
    async def get_stats() -> dict:
        return db.get_stats()

    @app.get("/api/images")
    async def list_images(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        cleanup: str | None = Query(default=None),
        status: str | None = Query(default=None),
    ) -> dict:
        items = db.get_images(limit=limit, offset=offset, status=status, cleanup_class=cleanup)
        total = db.count_images(status=status, cleanup_class=cleanup)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/thumbnail")
    async def get_thumbnail(
        path: str = Query(..., description="Absolute path to the image file"),
        size: int = Query(default=200, ge=50, le=800),
    ) -> Response:
        data = _make_thumbnail(path, size)
        if data is None:
            return Response(status_code=404)
        return Response(content=data, media_type="image/jpeg")

    @app.patch("/api/images/tags")
    async def update_tags(body: _TagsBody = Body(...)) -> dict:
        from pyimgtag.models import normalize_tags

        cleaned = normalize_tags(body.tags, max_tags=20)
        db.update_image_tags(body.file_path, cleaned)
        row = db.get_image(body.file_path)
        if row is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Image not found in DB")
        return row

    @app.patch("/api/images/cleanup")
    async def update_cleanup(body: _CleanupBody = Body(...)) -> dict:
        db.update_image_cleanup(body.file_path, body.cleanup_class)
        row = db.get_image(body.file_path)
        if row is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Image not found in DB")
        return row

    return app


def serve(
    db_path: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Start the review UI server.

    Args:
        db_path: Path to the SQLite progress DB.
        host: Bind host (default: 127.0.0.1).
        port: Bind port (default: 8765).
        open_browser: Open the default browser automatically.
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError(
            "uvicorn is required for the review UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    app = create_app(db_path=db_path)

    if open_browser:
        import threading
        import time
        import webbrowser

        def _open() -> None:
            time.sleep(1.0)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open, daemon=True).start()

    print(f"Review UI: http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port)
