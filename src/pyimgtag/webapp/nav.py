"""Shared navigation shell and Apple Light design system for the unified webapp."""

from __future__ import annotations

import re

DESIGN_CSS = """
:root{--bg:#f5f5f7;--surface:#fff;--border:rgba(0,0,0,.08);
      --accent:#0071e3;--accent-h:#0077ed;--danger:#ff3b30;--warn:#ff9f0a;--ok:#34c759;
      --text:#1d1d1f;--muted:#86868b;
      --radius-sm:8px;--radius-md:12px;--radius-lg:16px;
      --shadow-sm:0 1px 4px rgba(0,0,0,.06),0 0 1px rgba(0,0,0,.04);
      --shadow-md:0 4px 16px rgba(0,0,0,.08),0 1px 4px rgba(0,0,0,.04);
      --shadow-lg:0 12px 40px rgba(0,0,0,.12),0 2px 8px rgba(0,0,0,.06)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:var(--bg);color:var(--text);min-height:100vh}
.nav{display:flex;align-items:center;height:52px;padding:0 24px;
     background:rgba(255,255,255,.72);backdrop-filter:blur(20px);
     -webkit-backdrop-filter:blur(20px);border-bottom:1px solid rgba(0,0,0,.07);
     box-shadow:0 1px 0 rgba(0,0,0,.04);position:sticky;top:0;z-index:100}
.nav-logo{font-size:17px;font-weight:700;letter-spacing:-.4px;color:var(--text);
          margin-right:32px;text-decoration:none}
.nav-link{font-size:13px;color:var(--muted);padding:0 12px;height:52px;display:flex;
          align-items:center;text-decoration:none;border-bottom:2px solid transparent;
          transition:color .15s}
.nav-link:hover{color:var(--text)}
.nav-link.active{color:var(--text);font-weight:500;border-bottom-color:var(--accent)}
.nav-spacer{flex:1}
.nav-status{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px}
.nav-version{font-family:ui-monospace,'SF Mono',monospace;font-size:11px;
             color:var(--muted);padding:0 14px;text-decoration:none;
             border-left:1px solid var(--border);height:52px;display:flex;
             align-items:center;letter-spacing:.3px}
.nav-version:hover{color:var(--accent)}
.nav-version.update-available{color:var(--accent);font-weight:600}
.nav-version.update-available::after{content:'\\2191';margin-left:6px;font-weight:700}
.page-hdr{display:flex;align-items:baseline;gap:12px;padding:28px 32px 0}
.page-title{font-size:28px;font-weight:700;letter-spacing:-.5px;color:var(--text)}
.page-meta{font-size:13px;color:var(--muted)}
.pills{display:flex;gap:8px;padding:16px 32px 0}
.pill{padding:6px 14px;border-radius:20px;font-size:12px;font-weight:500;
      border:1px solid var(--border);color:var(--muted);background:var(--surface);
      cursor:pointer;transition:all .15s;box-shadow:var(--shadow-sm)}
.pill.on{background:var(--accent);border-color:var(--accent);color:#fff;
         box-shadow:0 2px 8px rgba(0,113,227,.25)}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;
     border-radius:var(--radius-sm);font-size:13px;font-weight:500;border:none;
     cursor:pointer;transition:all .15s;font-family:inherit}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent-h)}
.btn-secondary{background:var(--surface);color:var(--text);
               border:1px solid var(--border)!important;box-shadow:var(--shadow-sm)}
.btn-danger-text{background:rgba(255,59,48,.1);color:var(--danger)}
.btn-sm{padding:6px 12px;font-size:12px}
.inp{padding:9px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);
     font-size:14px;font-family:inherit;color:var(--text);background:var(--surface);
     outline:none;box-shadow:var(--shadow-sm);transition:border-color .15s,box-shadow .15s;
     width:100%}
.inp:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(0,113,227,.15)}
.tag-chip{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:5px;
          font-size:11px;font-weight:500;background:#e8f0fe;color:#1a73e8}
.img-card{background:var(--surface);border-radius:var(--radius-md);box-shadow:var(--shadow-md);
          overflow:hidden;transition:transform .15s,box-shadow .15s}
.img-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg)}
.img-thumb-wrap{position:relative}
.img-thumb{width:100%;aspect-ratio:4/3;object-fit:cover;background:#e5e5ea;display:block}
.img-badge{position:absolute;top:8px;left:8px;padding:3px 8px;border-radius:6px;
           font-size:10px;font-weight:700}
.badge-del{background:rgba(255,59,48,.12);color:var(--danger);
           border:1px solid rgba(255,59,48,.2)}
.badge-rev{background:rgba(255,159,10,.12);color:var(--warn);
           border:1px solid rgba(255,159,10,.2)}
.img-body{padding:10px 12px 12px}
.img-name{font-size:12px;font-weight:500;color:var(--text);margin-bottom:2px;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.img-scene{font-size:11px;color:var(--muted);margin-bottom:6px;
           display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.img-tags{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}
.img-actions{display:flex;gap:5px}
.img-btn{flex:1;padding:6px;border-radius:6px;font-size:10px;font-weight:600;
         border:none;cursor:pointer;transition:opacity .15s}
.btn-keep{background:rgba(52,199,89,.1);color:#1a7f50}
.btn-rev{background:rgba(255,159,10,.1);color:#b45309}
.btn-del{background:rgba(255,59,48,.1);color:var(--danger)}
.tbl{width:100%;border-collapse:collapse;background:var(--surface);
     border-radius:var(--radius-md);overflow:hidden;box-shadow:var(--shadow-sm)}
.tbl th{padding:10px 16px;text-align:left;font-size:11px;font-weight:600;color:var(--muted);
        text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--border)}
.tbl td{padding:10px 16px;border-bottom:1px solid rgba(0,0,0,.04);
        font-size:13px;color:var(--text)}
.tbl tr:last-child td{border-bottom:none}
.tbl tr:hover td{background:var(--bg)}
.tbl .fname{font-family:ui-monospace,'SF Mono',monospace;font-size:12px;font-weight:500}
.stat-card{background:var(--surface);border-radius:var(--radius-md);
           box-shadow:var(--shadow-sm);padding:16px 20px}
.stat-val{font-size:32px;font-weight:700;letter-spacing:-.5px;margin-bottom:2px}
.stat-label{font-size:13px;color:var(--muted)}
.score-bar-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.score-bar-bg{flex:1;height:6px;background:rgba(0,0,0,.06);border-radius:3px;overflow:hidden}
.score-bar-fill{height:100%;border-radius:3px;background:var(--accent)}
.score-val{font-size:22px;font-weight:700;letter-spacing:-.3px}
.score-tier{display:inline-block;padding:2px 8px;border-radius:5px;font-size:10px;
            font-weight:600;margin-bottom:6px}
.tier-excellent{background:rgba(52,199,89,.1);color:#1a7f50}
.tier-good{background:rgba(52,199,89,.06);color:#2d9e5f}
.tier-average{background:rgba(255,159,10,.1);color:#b45309}
.tier-poor{background:rgba(255,59,48,.1);color:var(--danger)}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);
               backdrop-filter:blur(4px);z-index:200;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal-box{background:var(--surface);border-radius:var(--radius-lg);
           box-shadow:var(--shadow-lg);padding:28px;width:360px;max-width:90vw}
.modal-title{font-size:17px;font-weight:600;letter-spacing:-.2px;margin-bottom:4px}
.modal-sub{font-size:13px;color:var(--muted);margin-bottom:18px;line-height:1.5}
.modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:18px}
.load-error{display:flex;gap:12px;align-items:center;margin:12px 0;padding:12px 14px;
            border-radius:8px;background:rgba(255,59,48,.08);color:var(--danger);
            font-size:13px;line-height:1.4}
.load-error-text{flex:1}
.modal-sub.modal-error{color:var(--danger)}
.empty-state{background:var(--surface);border-radius:var(--radius-md);padding:48px;
             margin:16px 0;text-align:center;color:var(--muted);box-shadow:var(--shadow-sm);
             grid-column:1/-1}
.empty-state p:first-child{font-size:17px;color:var(--text);margin-bottom:8px}
.empty-state code{font-family:ui-monospace,'SF Mono',monospace;font-size:12px;
                  background:var(--bg);padding:2px 6px;border-radius:4px}
.empty-state a{color:var(--accent)}
"""

# Backward-compatibility alias — used by the routes_*.py page templates (__NAV_STYLES__).
NAV_STYLES = DESIGN_CSS

MODAL_HTML = (
    '<div id="modal-overlay" class="modal-overlay" onclick="closeModal()">'
    '<div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="modal-title" '
    'aria-describedby="modal-sub" onclick="event.stopPropagation()">'
    '<div class="modal-title" id="modal-title"></div>'
    '<p class="modal-sub" id="modal-sub"></p>'
    '<div id="modal-field" style="margin-bottom:4px"></div>'
    '<div class="modal-actions">'
    '<button class="btn btn-secondary btn-sm" onclick="closeModal()">Cancel</button>'
    '<button class="btn btn-sm btn-primary" id="modal-confirm">Confirm</button>'
    "</div></div></div>"
)

MODAL_JS = """<script>
// Keyboard and screen-reader behaviour: focus moves into the dialog when it
// opens and back to whatever opened it when it closes; Escape closes it; Tab
// stays inside it while it is open.
let _modalReturnFocus = null;
function _modalFocusables() {
  const box = document.querySelector('#modal-overlay .modal-box');
  return box ? [...box.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  )].filter(el => !el.disabled && el.offsetParent !== null) : [];
}
function openModal(title, sub, fieldHtml, confirmLabel, confirmCls, onConfirm) {
  _modalReturnFocus = document.activeElement;
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-sub').textContent = sub;
  document.getElementById('modal-sub').classList.remove('modal-error');
  document.getElementById('modal-field').innerHTML = fieldHtml;
  const btn = document.getElementById('modal-confirm');
  btn.textContent = confirmLabel;
  btn.className = 'btn btn-sm ' + (confirmCls || 'btn-primary');
  btn.onclick = onConfirm;
  document.getElementById('modal-overlay').classList.add('open');
  const first = _modalFocusables()[0];
  if (first) first.focus();
}
function closeModal() {
  const overlay = document.getElementById('modal-overlay');
  const wasOpen = overlay.classList.contains('open');
  overlay.classList.remove('open');
  const back = _modalReturnFocus;
  _modalReturnFocus = null;
  if (wasOpen && back && back.isConnected && typeof back.focus === 'function') back.focus();
}
document.addEventListener('keydown', (e) => {
  const overlay = document.getElementById('modal-overlay');
  if (!overlay || !overlay.classList.contains('open')) return;
  if (e.key === 'Escape') { e.preventDefault(); closeModal(); return; }
  if (e.key !== 'Tab') return;
  const items = _modalFocusables();
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});
</script>"""


# Shared request helpers, emitted once per page by :func:`render_nav` so every
# page can use them without its own copy.
#
# - ``apiFetch(url, opts)`` resolves to the parsed JSON (or text) of a 2xx
#   response and rejects with an ``Error`` whose message is the server's
#   ``detail`` (or the HTTP status) for anything else, or a plain sentence when
#   the server cannot be reached. ``err.status`` carries the HTTP status.
# - ``showLoadError(target, message, retry)`` replaces the contents of
#   *target* (an element or id) with an inline ``role="alert"`` banner and a
#   Retry button that calls *retry*.
# - ``showModalError(message)`` keeps the shared modal open and shows why the
#   action failed in place of its subtitle.
API_JS = """<script>
async function apiFetch(url, opts) {
  let r;
  try {
    r = await fetch(url, opts);
  } catch (_e) {
    throw new Error('Cannot reach the pyimgtag server. Is it still running?');
  }
  if (!r.ok) {
    let message = 'HTTP ' + r.status + (r.statusText ? ' ' + r.statusText : '');
    try {
      const data = await r.json();
      if (typeof data.detail === 'string') {
        message = data.detail;
      } else if (Array.isArray(data.detail)) {
        message = data.detail.map(d => (d && d.msg) ? d.msg : JSON.stringify(d)).join('; ');
      } else if (data.detail != null) {
        message = JSON.stringify(data.detail);
      }
    } catch (_e) { /* not JSON: keep the HTTP status */ }
    const err = new Error(message);
    err.status = r.status;
    throw err;
  }
  if (r.status === 204) return null;
  const type = r.headers.get('content-type') || '';
  if (type.includes('json')) return r.json();
  // Every caller expects JSON; anything else (e.g. a proxy's HTML page)
  // is an error, not data. A caller that wants text passes {text: true}.
  if (opts && opts.text) return r.text();
  const what = type || 'no content type';
  const err = new Error('Unexpected response from the server (' + what + ')');
  err.status = r.status;
  throw err;
}
// Retrying cannot fix a request the server rejected as invalid (4xx other
// than timeouts and rate limits), so those banners get no Retry button.
function retryFor(err, retry) {
  const s = err && err.status;
  if (s && s >= 400 && s < 500 && s !== 408 && s !== 429) return null;
  return retry;
}
function showLoadError(target, message, retry) {
  const host = typeof target === 'string' ? document.getElementById(target) : target;
  if (!host) return;
  host.innerHTML = '';
  const box = document.createElement('div');
  box.className = 'load-error';
  box.setAttribute('role', 'alert');
  const text = document.createElement('span');
  text.className = 'load-error-text';
  text.textContent = message;
  box.appendChild(text);
  if (retry) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-secondary btn-sm load-error-retry';
    btn.textContent = 'Retry';
    btn.addEventListener('click', () => { box.remove(); retry(); });
    box.appendChild(btn);
  }
  host.appendChild(box);
}
// Replaces the contents of *target* with an empty-state card: a title and,
// optionally, a hint line ending in a command to run. Built with
// textContent only. ``empty`` is the class pages and tests already use;
// ``empty-state`` carries the shared look.
function renderEmpty(target, title, hint, command) {
  const host = typeof target === 'string' ? document.getElementById(target) : target;
  if (!host) return null;
  host.innerHTML = '';
  const box = document.createElement('div');
  box.className = 'empty empty-state';
  const head = document.createElement('p');
  head.textContent = title;
  box.appendChild(head);
  if (hint || command) {
    const line = document.createElement('p');
    if (hint) line.appendChild(document.createTextNode(hint + (command ? ' ' : '')));
    if (command) {
      const code = document.createElement('code');
      code.textContent = command;
      line.appendChild(code);
    }
    box.appendChild(line);
  }
  host.appendChild(box);
  return box;
}
// Makes a non-button element (a thumbnail, a placeholder) reachable with Tab
// and activatable with Enter or Space, announced as a button.
function makeActivatable(el, label, onActivate) {
  el.setAttribute('role', 'button');
  el.setAttribute('tabindex', '0');
  if (label) el.setAttribute('aria-label', label);
  el.addEventListener('click', onActivate);
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onActivate(e); }
  });
}
function showModalError(message) {
  const sub = document.getElementById('modal-sub');
  if (!sub) return;
  sub.textContent = 'Error: ' + message;
  sub.classList.add('modal-error');
}
</script>"""


def render_nav(active: str, status_html: str = "") -> str:
    """Return nav HTML with ``active`` section highlighted.

    ``active``: one of dashboard, review, faces, tags, query, search, events, judge,
    insights, map, timeline, dedup, edit, about.
    ``status_html``: injected into the right-side status slot (Dashboard only).

    The version label is rendered on every page so the user can see at a
    glance which build is running, and clicking it links to the About
    page (which exposes the wiki + update check).
    """
    from pyimgtag import __version__ as _ver

    def cls(name: str) -> str:
        return "nav-link active" if name == active else "nav-link"

    status = f'<span class="nav-status">{status_html}</span>' if status_html else ""
    version_link = (
        f'<a class="nav-version" href="/about" title="About / wiki / version" '
        f'data-version="{_ver}">v{_ver}</a>'
    )
    html = (
        '<nav class="nav">'
        '<span class="nav-logo">pyimgtag</span>'
        f'<a class="{cls("dashboard")}" href="/">Dashboard</a>'
        f'<a class="{cls("review")}" href="/review">Review</a>'
        f'<a class="{cls("faces")}" href="/faces">Faces</a>'
        f'<a class="{cls("tags")}" href="/tags">Tags</a>'
        f'<a class="{cls("query")}" href="/query">Query</a>'
        f'<a class="{cls("search")}" href="/search">Search</a>'
        f'<a class="{cls("events")}" href="/events">Events</a>'
        f'<a class="{cls("judge")}" href="/judge">Judge</a>'
        f'<a class="{cls("insights")}" href="/insights">Insights</a>'
        f'<a class="{cls("map")}" href="/map">Map</a>'
        f'<a class="{cls("timeline")}" href="/timeline">Timeline</a>'
        f'<a class="{cls("dedup")}" href="/dedup">Dedup</a>'
        f'<a class="{cls("edit")}" href="/edit">Edit</a>'
        f'<a class="{cls("about")}" href="/about">About</a>'
        '<span class="nav-spacer"></span>'
        f"{status}"
        f"{version_link}"
        "</nav>" + API_JS
    )
    # Screen readers announce the active link as the current page.
    return re.sub(
        r'(class="nav-link active" href="[^"]*")', r'\1 aria-current="page"', html, count=1
    )
