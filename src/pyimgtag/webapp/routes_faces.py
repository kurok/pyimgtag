"""Faces UI routes as a reusable APIRouter factory.

Exposes the faces management surfaces: the persons grid (with face
thumbnails), unassigned-faces and trash assignment, person rename and merge,
and per-face preview rendering. ``render_person_detail_html`` coerces the
incoming ``person_id`` through ``int()`` to eliminate the URL XSS taint path —
keep that coercion when refactoring.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi.responses import Response

    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag Faces</title>
  <style>
    __NAV_STYLES__
    #persons{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));
             gap:14px;padding:20px 32px}
    .person-card{background:var(--surface);border-radius:var(--radius-md);
                 box-shadow:var(--shadow-md);padding:16px}
    .person-name{font-size:15px;font-weight:600;color:var(--text);margin-bottom:4px}
    .person-meta{font-size:12px;color:var(--muted);margin-bottom:10px;
                 display:flex;align-items:center;gap:6px}
    .badge-trusted{background:rgba(52,199,89,.1);color:#1a7f50;font-size:10px;
                   font-weight:700;padding:2px 7px;border-radius:5px;text-transform:uppercase}
    .badge-auto{background:rgba(0,0,0,.05);color:var(--muted);font-size:10px;
                font-weight:700;padding:2px 7px;border-radius:5px;text-transform:uppercase}
    .faces-grid{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px;align-items:flex-start}
    .face-thumb{width:60px;height:60px;object-fit:cover;border-radius:var(--radius-sm);
                border:1px solid var(--border);cursor:pointer;transition:opacity .15s}
    .face-thumb:hover{opacity:.7}
    .face-thumb.hero{width:100px;height:100px;border-width:2px;
                     border-color:var(--accent);box-shadow:0 2px 8px rgba(0,0,0,.18)}
    .person-actions{display:flex;gap:6px;flex-wrap:wrap}
    .person-actions button{padding:5px 12px;border-radius:var(--radius-sm);font-size:12px;
                           font-weight:500;border:1px solid var(--border);
                           background:var(--surface);color:var(--text);cursor:pointer;
                           transition:all .15s}
    .person-actions button:hover{border-color:var(--accent);color:var(--accent)}
    .del-btn{color:var(--danger)!important;border-color:rgba(255,59,48,.3)!important}
    .del-btn:hover{background:rgba(255,59,48,.05)!important}
    .confirm-btn{color:#1a7f50!important;border-color:rgba(52,199,89,.4)!important}
    .confirm-btn:hover{background:rgba(52,199,89,.08)!important}
    .confirm-btn:disabled{opacity:.5;cursor:default}
    .pager{display:flex;align-items:center;gap:10px;padding:8px 32px 4px;
           font-size:13px;color:var(--muted)}
    .pager button{padding:4px 12px;border-radius:var(--radius-sm);font-size:12px;
                  border:1px solid var(--border);background:var(--surface);
                  color:var(--text);cursor:pointer;transition:all .15s}
    .pager button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
    .pager button:disabled{opacity:.35;cursor:default}
    .person-name a{color:var(--text);text-decoration:none}
    .person-name a:hover{color:var(--accent);text-decoration:underline}
    .more-hint{font-size:11px;color:var(--muted);margin-bottom:8px}
    .no-faces-hint{font-size:12px;color:var(--muted);font-style:italic;padding:4px 0 10px}
    .filter-bar{display:flex;gap:6px;padding:4px 32px 12px;align-items:center;flex-wrap:wrap}
    .filter-btn{padding:4px 14px;border-radius:var(--radius-sm);font-size:12px;font-weight:500;
                border:1px solid var(--border);background:var(--surface);
                color:var(--muted);cursor:pointer;transition:all .15s}
    .filter-btn:hover{border-color:var(--accent);color:var(--accent)}
    .filter-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
    #unassigned-section{padding:0 32px 32px}
    .unassigned-grid{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}
    .face-thumb.selectable{cursor:pointer}
    .face-thumb.selected{outline:3px solid var(--accent);outline-offset:1px}
    .assign-bar{display:flex;gap:8px;align-items:center;padding:8px 0;flex-wrap:wrap;
                font-size:13px;color:var(--muted)}
    .assign-bar button{padding:5px 14px;border-radius:var(--radius-sm);font-size:12px;
                       font-weight:500;border:1px solid var(--border);
                       background:var(--surface);color:var(--text);cursor:pointer}
    .assign-bar button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
    .assign-bar button:disabled{opacity:.35;cursor:default}
    .assign-bar .confirm-btn{color:#1a7f50!important;border-color:rgba(52,199,89,.4)!important}
    .assign-bar .confirm-btn:hover:not(:disabled){background:rgba(52,199,89,.08)!important}
  </style>
</head>
<body>
__NAV__
__MODAL_HTML__
__MODAL_JS__
<div class="page-hdr">
  <h1 class="page-title">Faces</h1>
  <span id="status" class="page-meta">Loading\u2026</span>
</div>
<div class="filter-bar">
  <button class="filter-btn active" data-filter="all" onclick="setFilter('all')">All</button>
  <button class="filter-btn" data-filter="trusted" onclick="setFilter('trusted')">Trusted</button>
  <button class="filter-btn" data-filter="auto" onclick="setFilter('auto')">Auto</button>
  <button class="filter-btn" data-filter="unassigned" onclick="setFilter('unassigned')">Unassigned</button>
  <button class="filter-btn" data-filter="trash" onclick="setFilter('trash')">🗑 Trash</button>
  <span style="flex:1"></span>
  <label id="sort-wrap" for="person-sort" style="color:var(--muted);font-size:13px">
    Sort:
    <select id="person-sort" onchange="setSort(this.value)">
      <option value="default">Default</option>
      <option value="count_desc">Most faces</option>
      <option value="count_asc">Fewest faces</option>
      <option value="name_asc">Name A-Z</option>
    </select>
  </label>
</div>
<div class="assign-bar" id="persons-bulk-bar" style="display:none">
  <span id="psel-count">0 selected</span>
  <button id="btn-psel-all" onclick="selectAllPersons()">Select all on page</button>
  <button id="btn-psel-clear" onclick="clearPersonSelection()">Clear</button>
  <button class="confirm-btn" id="btn-confirm-sel" disabled onclick="confirmSelectedPersons()">
    Confirm selected
  </button>
  <button class="del-btn" id="btn-delete-sel" disabled onclick="deleteSelectedPersons()">
    Delete selected
  </button>
</div>
<div class="pager" id="pager" style="display:none">
  <button id="btn-prev" onclick="goPage(-1)">\u2190 Previous</button>
  <span id="pager-info"></span>
  <button id="btn-next" onclick="goPage(1)">Next \u2192</button>
</div>
<div id="persons"></div>
<div id="unassigned-section" style="display:none">
  <div class="assign-bar" id="assign-bar">
    <span id="sel-count">0 selected</span>
    <button id="btn-select-all" onclick="selectAll()">Select all</button>
    <button id="btn-clear-sel" onclick="clearSelection()">Clear</button>
    <button class="confirm-btn" id="btn-assign" disabled onclick="openAssignModal()">
      Assign to person\u2026
    </button>
    <button id="btn-new-person" disabled onclick="createNewPerson()">
      New person from selected
    </button>
    <button id="btn-dismiss" disabled onclick="dismissSelected()" style="color:var(--muted)">
      Dismiss (move to trash)
    </button>
  </div>
  <div class="unassigned-grid" id="unassigned-grid"></div>
  <div class="pager" id="ua-pager" style="display:none">
    <button id="ua-prev" onclick="goUAPage(-1)">\u2190 Previous</button>
    <span id="ua-pager-info"></span>
    <button id="ua-next" onclick="goUAPage(1)">Next \u2192</button>
  </div>
</div>
<div id="trash-section" style="display:none">
  <div class="assign-bar">
    <span id="trash-count">0 dismissed faces</span>
    <button id="btn-restore-all" onclick="restoreSelected()">Restore selected</button>
    <button id="btn-trash-clear" onclick="clearSelection()">Clear selection</button>
  </div>
  <div class="unassigned-grid" id="trash-grid"></div>
  <div class="pager" id="tr-pager" style="display:none">
    <button id="tr-prev" onclick="goTRPage(-1)">\u2190 Previous</button>
    <span id="tr-pager-info"></span>
    <button id="tr-next" onclick="goTRPage(1)">Next \u2192</button>
  </div>
</div>
<script>
// Hover preview overlay
const _preview = document.createElement('div');
_preview.id = 'face-hover-preview';
_preview.style.cssText = 'display:none;position:fixed;z-index:9999;pointer-events:none;'
  + 'border-radius:8px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,.45);';
document.body.appendChild(_preview);
const _previewImg = document.createElement('img');
_previewImg.style.cssText = 'display:block;max-width:320px;max-height:320px;';
_previewImg.addEventListener('error', hidePreview);
_preview.appendChild(_previewImg);

function showPreview(faceId, rect) {
  _previewImg.src = '__API_BASE__/api/faces/' + faceId + '/preview';
  const left = Math.min(rect.right + 10, window.innerWidth - 340);
  const top  = Math.min(Math.max(rect.top, 8), window.innerHeight - 340);
  _preview.style.left = left + 'px';
  _preview.style.top  = top  + 'px';
  _preview.style.display = 'block';
}
function hidePreview() { _preview.style.display = 'none'; }

const PAGE_SIZE = 10;
const UA_PAGE_SIZE = 40;
let _offset = 0;
let _total = 0;
let _filter = 'all';
let _uaOffset = 0;
let _uaTotal = 0;
let _trOffset = 0;
let _trTotal = 0;
let _selected = new Set();  // face ids selected in unassigned / trash view
let _sort = 'default';             // persons-grid sort key
let _selectedPersons = new Set();  // person ids selected in the grid for bulk actions
let _pageIds = [];                 // person ids rendered on the current grid page

// ── Filter ──────────────────────────────────────────────────────────────────
function setFilter(f) {
  _filter = f;
  _offset = 0;
  _uaOffset = 0;
  _trOffset = 0;
  _selected.clear();
  document.querySelectorAll('.filter-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.filter === f));
  const isUA = f === 'unassigned';
  const isTR = f === 'trash';
  const isGrid = !isUA && !isTR;
  document.getElementById('persons').style.display = isGrid ? '' : 'none';
  document.getElementById('pager').style.display = 'none';
  document.getElementById('unassigned-section').style.display = isUA ? '' : 'none';
  document.getElementById('trash-section').style.display = isTR ? '' : 'none';
  // The sort control and bulk-action bar only apply to the persons grid.
  document.getElementById('sort-wrap').style.display = isGrid ? '' : 'none';
  _selectedPersons.clear();
  document.getElementById('persons-bulk-bar').style.display = 'none';
  if (isUA) loadUnassigned();
  else if (isTR) loadTrash();
  else load();
}

// ── Sort ──────────────────────────────────────────────────────────────────
function setSort(value) {
  _sort = value;
  _offset = 0;
  load();
}

function goPage(delta) {
  _offset = Math.max(0, Math.min(_offset + delta * PAGE_SIZE, _total - 1));
  load();
}

// ── Persons grid ─────────────────────────────────────────────────────────────
async function load() {
  document.getElementById('status').textContent = 'Loading…';
  const url = '__API_BASE__/api/persons/with-faces?offset=' + _offset
    + '&limit=' + PAGE_SIZE + '&filter=' + _filter + '&sort=' + _sort;
  const data = await fetch(url).then(r => r.json());
  _total = data.total;
  const persons = data.items;

  document.getElementById('status').textContent = _total + ' person(s)';

  // Selection is per page; rebuild it each load.
  _selectedPersons.clear();
  _pageIds = persons.map(p => p.id);

  const grid = document.getElementById('persons');
  grid.innerHTML = '';
  for (const p of persons) grid.appendChild(renderPerson(p, p.faces));

  // The bulk-action bar is only meaningful with at least one person on screen.
  const bulkBar = document.getElementById('persons-bulk-bar');
  bulkBar.style.display = persons.length ? 'flex' : 'none';
  updatePersonsBulkBar();

  const pager = document.getElementById('pager');
  if (_total > PAGE_SIZE) {
    pager.style.display = 'flex';
    const end = Math.min(_offset + PAGE_SIZE, _total);
    document.getElementById('pager-info').textContent =
      (_offset + 1) + '–' + end + ' of ' + _total;
    document.getElementById('btn-prev').disabled = _offset === 0;
    document.getElementById('btn-next').disabled = end >= _total;
  } else {
    pager.style.display = 'none';
  }
}

// ── Unassigned faces ─────────────────────────────────────────────────────────
function goUAPage(delta) {
  _uaOffset = Math.max(0, Math.min(_uaOffset + delta * UA_PAGE_SIZE, _uaTotal - 1));
  _selected.clear();
  updateSelectionUI();
  loadUnassigned();
}

async function loadUnassigned() {
  document.getElementById('status').textContent = 'Loading…';
  const url = '__API_BASE__/api/faces/unassigned?offset=' + _uaOffset + '&limit=' + UA_PAGE_SIZE;
  const data = await fetch(url).then(r => r.json());
  _uaTotal = data.total;
  document.getElementById('status').textContent = _uaTotal + ' unassigned face(s)';

  const grid = document.getElementById('unassigned-grid');
  grid.innerHTML = '';
  data.items.forEach(f => {
    if (!f.thumb) return;
    const img = document.createElement('img');
    img.className = 'face-thumb selectable' + (_selected.has(f.id) ? ' selected' : '');
    img.src = 'data:image/jpeg;base64,' + f.thumb;
    img.dataset.faceId = f.id;
    img.title = 'Click to select — confidence ' + (f.confidence ? f.confidence.toFixed(2) : '?');
    img.addEventListener('mouseenter', () => showPreview(f.id, img.getBoundingClientRect()));
    img.addEventListener('mouseleave', hidePreview);
    img.addEventListener('click', () => {
      hidePreview();
      _selected.has(f.id) ? _selected.delete(f.id) : _selected.add(f.id);
      img.classList.toggle('selected', _selected.has(f.id));
      updateSelectionUI();
    });
    grid.appendChild(img);
  });

  const pager = document.getElementById('ua-pager');
  if (_uaTotal > UA_PAGE_SIZE) {
    pager.style.display = 'flex';
    const end = Math.min(_uaOffset + UA_PAGE_SIZE, _uaTotal);
    document.getElementById('ua-pager-info').textContent =
      (_uaOffset + 1) + '–' + end + ' of ' + _uaTotal;
    document.getElementById('ua-prev').disabled = _uaOffset === 0;
    document.getElementById('ua-next').disabled = end >= _uaTotal;
  } else {
    pager.style.display = 'none';
  }
}

function selectAll() {
  document.querySelectorAll('#unassigned-grid .face-thumb').forEach(img => {
    _selected.add(Number(img.dataset.faceId));
    img.classList.add('selected');
  });
  updateSelectionUI();
}

function clearSelection() {
  _selected.clear();
  document.querySelectorAll('#unassigned-grid .face-thumb').forEach(img =>
    img.classList.remove('selected'));
  updateSelectionUI();
}

function updateSelectionUI() {
  const n = _selected.size;
  if (_filter === 'unassigned') {
    document.getElementById('sel-count').textContent = n + ' selected';
    document.getElementById('btn-assign').disabled = n === 0;
    document.getElementById('btn-new-person').disabled = n === 0;
    document.getElementById('btn-dismiss').disabled = n === 0;
  } else if (_filter === 'trash') {
    document.getElementById('trash-count').textContent = n + ' selected';
  }
}

async function openAssignModal() {
  const persons = await fetch('__API_BASE__/api/persons').then(r => r.json());
  if (!persons.length) { alert('No persons yet — use "New person from selected" instead.'); return; }
  const opts = persons.map(p =>
    '<option value="' + p.id + '">' +
    (p.label ? p.label : ('Person ' + p.id + ' (auto)')) +
    ' — ' + p.face_count + ' faces' +
    '</option>'
  ).join('');
  openModal(
    'Assign faces to person',
    _selected.size + ' face(s) selected.',
    '<select class="inp" id="m-person-sel" style="width:100%">' + opts + '</select>',
    'Assign', 'btn-primary',
    async () => {
      const personId = Number(document.getElementById('m-person-sel').value);
      await fetch('__API_BASE__/api/faces/assign-batch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({face_ids: [..._selected], person_id: personId}),
      });
      closeModal();
      _selected.clear();
      loadUnassigned();
    }
  );
}

// ── Rename / merge modal ─────────────────────────────────────────────────────
async function openRenameModal(personId, currentLabel, onDone) {
  const allPersons = await fetch('__API_BASE__/api/persons').then(r => r.json());
  const targets = allPersons.filter(p => p.trusted && p.label && p.id !== personId);

  const body = '<input class="inp" id="m-inp" placeholder="Type a new name…"'
    + ' style="margin-bottom:8px;display:block;width:100%" />'
    + '<label style="font-size:12px;color:var(--muted);display:block;margin:6px 0 4px">'
    + 'Or merge into existing trusted person:</label>'
    + '<select class="inp" id="m-merge-sel" style="display:block;width:100%">'
    + '<option value="">— assign a new name only —</option></select>';

  openModal(
    'Rename / merge person',
    'Type a new name or pick an existing trusted person to merge this cluster into.',
    body,
    'Apply', 'btn-primary',
    async () => {
      const sel = document.getElementById('m-merge-sel');
      const _raw = sel && sel.value ? Number(sel.value) : null;
      const targetId = (_raw && !isNaN(_raw)) ? _raw : null;
      if (targetId) {
        await fetch('__API_BASE__/api/persons/' + personId + '/merge/' + targetId,
          {method: 'POST'});
        closeModal();
        if (typeof onDone === 'function') onDone(targetId);
      } else {
        const val = document.getElementById('m-inp').value.trim();
        if (!val) return;
        await fetch('__API_BASE__/api/persons/' + personId + '/label', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({label: val}),
        });
        closeModal();
        if (typeof onDone === 'function') onDone(null);
      }
    }
  );

  setTimeout(() => {
    const inp = document.getElementById('m-inp');
    const sel = document.getElementById('m-merge-sel');
    if (inp) { inp.value = currentLabel || ''; inp.focus(); }
    if (sel && targets.length) {
      targets.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.label + ' (' + p.face_count + ' face'
          + (p.face_count !== 1 ? 's' : '') + ')';
        sel.appendChild(opt);
      });
      sel.addEventListener('change', () => {
        const t = targets.find(p => p.id === Number(sel.value));
        if (t && inp) inp.value = t.label;
      });
    }
  }, 50);
}

async function createNewPerson() {
  openModal(
    'Create person from selected faces',
    _selected.size + ' face(s) will be assigned.',
    '<input class="inp" id="m-inp" placeholder="Name (optional)" />',
    'Create', 'btn-primary',
    async () => {
      const label = document.getElementById('m-inp').value.trim();
      await fetch('__API_BASE__/api/faces/assign-batch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({face_ids: [..._selected], person_id: null, label}),
      });
      closeModal();
      _selected.clear();
      loadUnassigned();
    }
  );
  setTimeout(() => document.getElementById('m-inp').focus(), 50);
}

async function dismissSelected() {
  if (!_selected.size) return;
  await Promise.all([..._selected].map(id =>
    fetch('__API_BASE__/api/faces/' + id + '/ignore', {method: 'POST'})
  ));
  _selected.clear();
  loadUnassigned();
}

// ── Trash ────────────────────────────────────────────────────────────────────
function goTRPage(delta) {
  _trOffset = Math.max(0, Math.min(_trOffset + delta * UA_PAGE_SIZE, _trTotal - 1));
  _selected.clear();
  loadTrash();
}

async function loadTrash() {
  document.getElementById('status').textContent = 'Loading…';
  const url = '__API_BASE__/api/faces/ignored?offset=' + _trOffset + '&limit=' + UA_PAGE_SIZE;
  const data = await fetch(url).then(r => r.json());
  _trTotal = data.total;
  document.getElementById('status').textContent = _trTotal + ' dismissed face(s)';
  document.getElementById('trash-count').textContent = '0 selected';

  const grid = document.getElementById('trash-grid');
  grid.innerHTML = '';
  data.items.forEach(f => {
    if (!f.thumb) return;
    const img = document.createElement('img');
    img.className = 'face-thumb selectable' + (_selected.has(f.id) ? ' selected' : '');
    img.src = 'data:image/jpeg;base64,' + f.thumb;
    img.dataset.faceId = f.id;
    img.title = 'Click to select for restore';
    img.style.opacity = '0.6';
    img.addEventListener('mouseenter', () => showPreview(f.id, img.getBoundingClientRect()));
    img.addEventListener('mouseleave', hidePreview);
    img.addEventListener('click', () => {
      hidePreview();
      _selected.has(f.id) ? _selected.delete(f.id) : _selected.add(f.id);
      img.classList.toggle('selected', _selected.has(f.id));
      img.style.opacity = _selected.has(f.id) ? '1' : '0.6';
      updateSelectionUI();
    });
    grid.appendChild(img);
  });

  const pager = document.getElementById('tr-pager');
  if (_trTotal > UA_PAGE_SIZE) {
    pager.style.display = 'flex';
    const end = Math.min(_trOffset + UA_PAGE_SIZE, _trTotal);
    document.getElementById('tr-pager-info').textContent =
      (_trOffset + 1) + '–' + end + ' of ' + _trTotal;
    document.getElementById('tr-prev').disabled = _trOffset === 0;
    document.getElementById('tr-next').disabled = end >= _trTotal;
  } else {
    pager.style.display = 'none';
  }
}

async function restoreSelected() {
  if (!_selected.size) return;
  await Promise.all([..._selected].map(id =>
    fetch('__API_BASE__/api/faces/' + id + '/restore', {method: 'POST'})
  ));
  _selected.clear();
  loadTrash();
}

function renderPerson(p, faces) {
  const card = document.createElement('div');
  card.className = 'person-card';

  const nameEl = document.createElement('div');
  nameEl.className = 'person-name';
  const sel = document.createElement('input');
  sel.type = 'checkbox';
  sel.className = 'person-select';
  sel.style.cssText = 'margin-right:8px;vertical-align:middle;cursor:pointer';
  sel.title = 'Select for bulk confirm / delete';
  sel.checked = _selectedPersons.has(p.id);
  sel.addEventListener('change', () => {
    if (sel.checked) _selectedPersons.add(p.id);
    else _selectedPersons.delete(p.id);
    updatePersonsBulkBar();
  });
  nameEl.appendChild(sel);
  const nameLink = document.createElement('a');
  nameLink.href = '__API_BASE__/persons/' + p.id;
  nameLink.textContent = p.label || ('(unlabelled #' + p.id + ')');
  nameEl.appendChild(nameLink);
  card.appendChild(nameEl);

  const meta = document.createElement('div');
  meta.className = 'person-meta';
  const badge = document.createElement('span');
  badge.className = p.trusted ? 'badge-trusted' : 'badge-auto';
  badge.textContent = p.trusted ? 'trusted' : 'auto';
  meta.appendChild(badge);
  const cnt = document.createTextNode(
    p.face_count + ' face' + (p.face_count !== 1 ? 's' : ''));
  meta.appendChild(cnt);
  card.appendChild(meta);

  // Show hint + link when there are more faces than displayed.
  if (p.face_count > 8) {
    const hint = document.createElement('div');
    hint.className = 'more-hint';
    const a = document.createElement('a');
    a.href = '__API_BASE__/persons/' + p.id;
    a.textContent = 'Showing 8 of ' + p.face_count + ' — click to see all';
    hint.appendChild(a);
    card.appendChild(hint);
  }

  // Sort by confidence descending so the best face is first (shown as hero).
  const sorted = faces.slice().sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
  const facesGrid = document.createElement('div');
  facesGrid.className = 'faces-grid';
  let thumbsShown = 0;
  sorted.slice(0, 8).forEach((f, i) => {
    if (!f.thumb) return;
    thumbsShown++;
    const img = document.createElement('img');
    img.className = i === 0 ? 'face-thumb hero' : 'face-thumb';
    img.src = 'data:image/jpeg;base64,' + f.thumb;
    img.title = i === 0
      ? 'Best match (confidence ' + (f.confidence ? f.confidence.toFixed(2) : '?') + ') — click to unassign'
      : 'Click to unassign';
    img.addEventListener('mouseenter', () => showPreview(f.id, img.getBoundingClientRect()));
    img.addEventListener('mouseleave', hidePreview);
    img.addEventListener('click', async () => {
      hidePreview();
      await fetch('__API_BASE__/api/faces/' + f.id + '/unassign', {method: 'POST'});
      _offset = Math.min(_offset, Math.max(0, _total - PAGE_SIZE - 1));
      load();
    });
    facesGrid.appendChild(img);
  });
  if (thumbsShown === 0) {
    const hint = document.createElement('div');
    hint.className = 'no-faces-hint';
    hint.textContent = p.face_count === 0
      ? 'No faces scanned yet — run faces scan to populate'
      : 'Face images not available';
    facesGrid.appendChild(hint);
  }
  card.appendChild(facesGrid);

  const acts = document.createElement('div');
  acts.className = 'person-actions';

  const renBtn = document.createElement('button');
  renBtn.textContent = 'Rename';
  renBtn.addEventListener('click', () =>
    openRenameModal(p.id, p.label, targetId => {
      if (targetId) window.location.href = '__API_BASE__/persons/' + targetId;
      else load();
    })
  );
  acts.appendChild(renBtn);

  const delBtn = document.createElement('button');
  delBtn.className = 'del-btn';
  delBtn.textContent = 'Delete';
  delBtn.addEventListener('click', () => {
    openModal(
      'Delete person',
      'Delete this person record? Face crops are kept but unassigned.',
      '',
      'Delete', 'btn-danger-text',
      async () => {
        await fetch('__API_BASE__/api/persons/' + p.id, {method: 'DELETE'});
        closeModal();
        load();
      }
    );
  });
  acts.appendChild(delBtn);

  // Only show Confirm when all faces are visible (face_count ≤ 8).
  // For larger clusters, the detail page has the Confirm button.
  if (!p.trusted && p.face_count <= 8) {
    const cfmBtn = document.createElement('button');
    cfmBtn.className = 'confirm-btn';
    cfmBtn.textContent = 'Confirm';
    cfmBtn.title = 'Mark all faces in this cluster as correctly assigned (sets trusted)';
    cfmBtn.addEventListener('click', async () => {
      cfmBtn.disabled = true;
      cfmBtn.textContent = 'Confirming…';
      await fetch('__API_BASE__/api/persons/' + p.id + '/confirm', {method: 'POST'});
      load();
    });
    acts.appendChild(cfmBtn);
  } else if (!p.trusted && p.face_count > 8) {
    const viewBtn = document.createElement('button');
    viewBtn.textContent = 'View all & confirm';
    viewBtn.addEventListener('click', () => {
      window.location.href = '__API_BASE__/persons/' + p.id;
    });
    acts.appendChild(viewBtn);
  }

  card.appendChild(acts);
  return card;
}

// ── Bulk confirm / delete ─────────────────────────────────────────────────
function updatePersonsBulkBar() {
  const n = _selectedPersons.size;
  document.getElementById('psel-count').textContent = n + ' selected';
  document.getElementById('btn-confirm-sel').disabled = n === 0;
  document.getElementById('btn-delete-sel').disabled = n === 0;
}

function selectAllPersons() {
  _pageIds.forEach(id => _selectedPersons.add(id));
  document.querySelectorAll('.person-select').forEach(cb => { cb.checked = true; });
  updatePersonsBulkBar();
}

function clearPersonSelection() {
  _selectedPersons.clear();
  document.querySelectorAll('.person-select').forEach(cb => { cb.checked = false; });
  updatePersonsBulkBar();
}

async function confirmSelectedPersons() {
  const ids = [..._selectedPersons];
  if (!ids.length) return;
  await fetch('__API_BASE__/api/persons/confirm-batch', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({person_ids: ids}),
  });
  load();
}

function deleteSelectedPersons() {
  const ids = [..._selectedPersons];
  if (!ids.length) return;
  openModal(
    'Delete selected persons',
    'Delete ' + ids.length + ' person record(s)? Face crops are kept but unassigned.',
    '',
    'Delete', 'btn-danger-text',
    async () => {
      await fetch('__API_BASE__/api/persons/delete-batch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({person_ids: ids}),
      });
      closeModal();
      load();
    }
  );
}

load();
</script>
</body>
</html>"""


_PERSON_DETAIL_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag — Person</title>
  <style>
    __NAV_STYLES__
    .detail-hdr{display:flex;align-items:center;gap:12px;padding:20px 32px 4px;flex-wrap:wrap}
    .detail-hdr h1{font-size:20px;font-weight:700;color:var(--text);margin:0}
    .detail-hdr .back{font-size:13px;color:var(--muted);text-decoration:none;
                      padding:4px 10px;border:1px solid var(--border);border-radius:var(--radius-sm)}
    .detail-hdr .back:hover{border-color:var(--accent);color:var(--accent)}
    .detail-meta{padding:0 32px 12px;font-size:13px;color:var(--muted);
                 display:flex;align-items:center;gap:8px}
    .badge-trusted{background:rgba(52,199,89,.1);color:#1a7f50;font-size:10px;
                   font-weight:700;padding:2px 7px;border-radius:5px;text-transform:uppercase}
    .badge-auto{background:rgba(0,0,0,.05);color:var(--muted);font-size:10px;
                font-weight:700;padding:2px 7px;border-radius:5px;text-transform:uppercase}
    .detail-actions{display:flex;gap:8px;padding:0 32px 16px;flex-wrap:wrap}
    .detail-actions button{padding:6px 16px;border-radius:var(--radius-sm);font-size:13px;
                           font-weight:500;border:1px solid var(--border);
                           background:var(--surface);color:var(--text);cursor:pointer;
                           transition:all .15s}
    .detail-actions button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
    .confirm-btn{color:#1a7f50!important;border-color:rgba(52,199,89,.4)!important}
    .confirm-btn:hover:not(:disabled){background:rgba(52,199,89,.08)!important}
    .confirm-btn:disabled{opacity:.5;cursor:default}
    .del-btn{color:var(--danger)!important;border-color:rgba(255,59,48,.3)!important}
    .del-btn:hover{background:rgba(255,59,48,.05)!important}
    #faces-grid{display:flex;flex-wrap:wrap;gap:8px;padding:0 32px 32px;align-items:flex-start}
    .face-thumb{width:80px;height:80px;object-fit:cover;border-radius:var(--radius-sm);
                border:1px solid var(--border);cursor:pointer;transition:opacity .15s}
    .face-thumb:hover{opacity:.7}
    .face-thumb.hero{width:120px;height:120px;border-width:2px;
                     border-color:var(--accent);box-shadow:0 2px 8px rgba(0,0,0,.18)}
    #loading{padding:32px;color:var(--muted);font-size:14px}
  </style>
</head>
<body>
__NAV__
__MODAL_HTML__
__MODAL_JS__
<div class="detail-hdr">
  <a class="back" href="javascript:history.back()" onclick="if(!document.referrer)window.location.href='__API_BASE__/'">← All Faces</a>
  <h1 id="person-name">Loading…</h1>
</div>
<div class="detail-meta">
  <span id="person-badge"></span>
  <span id="person-count"></span>
</div>
<div class="detail-actions" id="actions" style="display:none">
  <button id="rename-btn">Rename</button>
  <button id="confirm-btn" class="confirm-btn">Confirm cluster</button>
  <button id="delete-btn" class="del-btn">Delete person</button>
</div>
<div id="loading">Loading faces…</div>
<div id="faces-grid"></div>
<script>
const _personId = __PERSON_ID__;
const _apiBase  = '__API_BASE__';

// Hover preview overlay
const _preview = document.createElement('div');
_preview.id = 'face-hover-preview';
_preview.style.cssText = 'display:none;position:fixed;z-index:9999;pointer-events:none;'
  + 'border-radius:8px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,.45);';
document.body.appendChild(_preview);
const _previewImg = document.createElement('img');
_previewImg.style.cssText = 'display:block;max-width:320px;max-height:320px;';
_previewImg.addEventListener('error', () => { _preview.style.display = 'none'; });
_preview.appendChild(_previewImg);

function showPreview(faceId, rect) {
  _previewImg.src = _apiBase + '/api/faces/' + faceId + '/preview';
  const left = Math.min(rect.right + 10, window.innerWidth - 340);
  const top  = Math.min(Math.max(rect.top, 8), window.innerHeight - 340);
  _preview.style.left = left + 'px';
  _preview.style.top  = top  + 'px';
  _preview.style.display = 'block';
}

let _person = null;

async function load() {
  const [personRes, facesRes] = await Promise.all([
    fetch(_apiBase + '/api/persons/' + _personId),
    fetch(_apiBase + '/api/persons/' + _personId + '/faces'),
  ]);
  // Person no longer exists (deleted or merged into another) — go back to list.
  if (!personRes.ok) {
    window.location.href = _apiBase + '/';
    return;
  }
  const [person, faces] = await Promise.all([personRes.json(), facesRes.json()]);
  _person = person;

  document.getElementById('person-name').textContent =
    person.label || ('(unlabelled #' + person.id + ')');
  const badge = document.getElementById('person-badge');
  badge.className = person.trusted ? 'badge-trusted' : 'badge-auto';
  badge.textContent = person.trusted ? 'trusted' : 'auto';
  document.getElementById('person-count').textContent =
    faces.length + ' face' + (faces.length !== 1 ? 's' : '');

  const cfmBtn = document.getElementById('confirm-btn');
  cfmBtn.disabled = person.trusted;
  cfmBtn.textContent = person.trusted ? 'Confirmed ✓' : 'Confirm cluster';

  document.getElementById('actions').style.display = 'flex';
  document.getElementById('loading').style.display = 'none';

  renderFaces(faces);
}

function renderFaces(faces) {
  const grid = document.getElementById('faces-grid');
  grid.innerHTML = '';
  const sorted = faces.slice().sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
  sorted.forEach((f, i) => {
    if (!f.thumb) return;
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:relative;display:inline-block';

    const img = document.createElement('img');
    img.className = i === 0 ? 'face-thumb hero' : 'face-thumb';
    img.src = 'data:image/jpeg;base64,' + f.thumb;
    img.title = i === 0
      ? 'Best match (conf ' + (f.confidence ? f.confidence.toFixed(2) : '?') + ') — click to unassign'
      : 'Click to unassign';
    img.addEventListener('mouseenter', () => showPreview(f.id, img.getBoundingClientRect()));
    img.addEventListener('mouseleave', () => { _preview.style.display = 'none'; });
    img.addEventListener('click', async () => {
      _preview.style.display = 'none';
      img.style.opacity = '0.3';
      await fetch(_apiBase + '/api/faces/' + f.id + '/unassign', {method: 'POST'});
      load();
    });
    wrap.appendChild(img);
    grid.appendChild(wrap);
  });
}

document.getElementById('rename-btn').addEventListener('click', async () => {
  const allPersons = await fetch(_apiBase + '/api/persons').then(r => r.json());
  const targets = allPersons.filter(p => p.trusted && p.label && p.id !== _personId);

  const body = '<input class="inp" id="m-inp" placeholder="Type a new name…"'
    + ' style="margin-bottom:8px;display:block;width:100%" />'
    + '<label style="font-size:12px;color:var(--muted);display:block;margin:6px 0 4px">'
    + 'Or merge into existing trusted person:</label>'
    + '<select class="inp" id="m-merge-sel" style="display:block;width:100%">'
    + '<option value="">— assign a new name only —</option></select>';

  openModal(
    'Rename / merge person',
    'Type a new name or pick an existing trusted person to merge this cluster into.',
    body,
    'Apply', 'btn-primary',
    async () => {
      const sel = document.getElementById('m-merge-sel');
      const _raw = sel && sel.value ? Number(sel.value) : null;
      const targetId = (_raw && !isNaN(_raw)) ? _raw : null;
      if (targetId) {
        await fetch(_apiBase + '/api/persons/' + _personId + '/merge/' + targetId,
          {method: 'POST'});
        closeModal();
        window.location.href = _apiBase + '/persons/' + targetId;
      } else {
        const val = document.getElementById('m-inp').value.trim();
        if (!val) return;
        await fetch(_apiBase + '/api/persons/' + _personId + '/label', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({label: val}),
        });
        closeModal();
        load();
      }
    }
  );

  setTimeout(() => {
    const inp = document.getElementById('m-inp');
    const sel = document.getElementById('m-merge-sel');
    if (inp) { inp.value = (_person && _person.label) || ''; inp.focus(); }
    if (sel && targets.length) {
      targets.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.label + ' (' + p.face_count + ' face'
          + (p.face_count !== 1 ? 's' : '') + ')';
        sel.appendChild(opt);
      });
      sel.addEventListener('change', () => {
        const t = targets.find(tp => tp.id === Number(sel.value));
        if (t && inp) inp.value = t.label;
      });
    }
  }, 50);
});

document.getElementById('confirm-btn').addEventListener('click', async () => {
  const btn = document.getElementById('confirm-btn');
  btn.disabled = true;
  btn.textContent = 'Confirming…';
  await fetch(_apiBase + '/api/persons/' + _personId + '/confirm', {method: 'POST'});
  load();
});

document.getElementById('delete-btn').addEventListener('click', () => {
  openModal(
    'Delete person',
    'Delete this person record? Face crops are kept but unassigned.',
    '',
    'Delete', 'btn-danger-text',
    async () => {
      await fetch(_apiBase + '/api/persons/' + _personId, {method: 'DELETE'});
      closeModal();
      window.location.href = _apiBase + '/';
    }
  );
});

load();
</script>
</body>
</html>"""


def render_faces_html(api_base: str = "") -> str:
    """Return the faces UI HTML with the given API base prefix inserted."""
    from pyimgtag.webapp.nav import MODAL_HTML, MODAL_JS, NAV_STYLES, render_nav

    return (
        _HTML_TEMPLATE.replace("__API_BASE__", api_base)
        .replace("__NAV__", render_nav("faces"))
        .replace("__NAV_STYLES__", NAV_STYLES)
        .replace("__MODAL_HTML__", MODAL_HTML)
        .replace("__MODAL_JS__", MODAL_JS)
    )


def render_person_detail_html(person_id: int, api_base: str = "") -> str:
    """Return the person detail page HTML."""
    from pyimgtag.webapp.nav import MODAL_HTML, MODAL_JS, NAV_STYLES, render_nav

    # Coerce through int() so the substituted value is guaranteed to be
    # a digit-only string — eliminates the XSS taint path from the URL.
    safe_id = str(int(person_id))
    return (
        _PERSON_DETAIL_TEMPLATE.replace("__PERSON_ID__", safe_id)
        .replace("__API_BASE__", api_base)
        .replace("__NAV__", render_nav("faces"))
        .replace("__NAV_STYLES__", NAV_STYLES)
        .replace("__MODAL_HTML__", MODAL_HTML)
        .replace("__MODAL_JS__", MODAL_JS)
    )


def build_faces_router(db: ProgressDB, api_base: str = "") -> Any:
    """Build and return a FastAPI APIRouter with all faces UI routes.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into the HTML (e.g. ``"/faces"`` or ``""``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, Body, HTTPException
        from fastapi.responses import HTMLResponse, RedirectResponse
        from pydantic import BaseModel
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for the faces review UI. "
            "Install with: pip install 'pyimgtag[review]'"
        ) from exc

    class _LabelBody(BaseModel):
        label: str

    from pyimgtag.face_thumb import face_thumbnail_b64

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_faces_html(api_base)

    @router.get("/persons/{person_id}")
    async def person_detail(person_id: int) -> Response:
        persons = db.get_persons()
        if not any(p.person_id == person_id for p in persons):
            # The person was deleted or re-clustered away since the grid was
            # rendered (auto-clustering deletes and recreates persons, so cards
            # can point at ids that no longer exist). Bounce back to the faces
            # list instead of dumping a raw "Person not found" JSON body.
            # Static message (no request value) keeps this breadcrumb free of
            # any log-injection vector.
            logger.debug("requested person no longer exists; redirecting to faces list")
            return RedirectResponse(url=f"{api_base}/", status_code=303)
        return HTMLResponse(render_person_detail_html(person_id, api_base))

    @router.get("/api/persons")
    async def list_persons() -> list[dict]:
        persons = db.get_persons()
        return [
            {
                "id": p.person_id,
                "label": p.label,
                "confirmed": p.confirmed,
                "source": p.source,
                "trusted": p.trusted,
                "face_count": len(p.face_ids),
            }
            for p in persons
            if p.face_ids or p.trusted
        ]

    @router.get("/api/persons/with-faces")
    async def list_persons_with_faces(
        offset: int = 0, limit: int = 10, filter: str = "all", sort: str = "default"
    ) -> dict:
        """Return a page of persons with their top-8 face thumbnails.

        Query params:
          offset  – 0-based index of the first person to return (default 0)
          limit   – number of persons per page (default 10, max 50)
          filter  – ``all`` | ``trusted`` | ``auto`` (default ``all``)
          sort    – ``default`` (id order) | ``count_desc`` | ``count_asc`` |
                    ``name_asc``. Sorting is applied to the whole filtered set
                    before pagination.

        Response: ``{"total": N, "items": [...]}``
        """
        import asyncio

        limit = min(max(limit, 1), 50)
        persons = db.get_persons()
        visible = [p for p in persons if p.face_ids or p.trusted]
        if filter == "trusted":
            visible = [p for p in visible if p.trusted]
        elif filter == "auto":
            visible = [p for p in visible if not p.trusted]
        # Sort the full filtered set before paginating so the order is stable
        # across pages.
        if sort == "count_desc":
            visible.sort(key=lambda p: len(p.face_ids), reverse=True)
        elif sort == "count_asc":
            visible.sort(key=lambda p: len(p.face_ids))
        elif sort == "name_asc":
            visible.sort(key=lambda p: (p.label or "").lower())
        total = len(visible)
        page = visible[offset : offset + limit]

        async def _person_entry(p) -> dict:
            faces = db.get_faces_for_person(p.person_id)

            def _gen_thumbs() -> list[dict]:
                return [
                    {
                        **f,
                        "thumb": face_thumbnail_b64(
                            f["image_path"],
                            f["bbox_x"],
                            f["bbox_y"],
                            f["bbox_w"],
                            f["bbox_h"],
                        ),
                    }
                    for f in faces[:8]
                ]

            faces_with_thumbs = await asyncio.to_thread(_gen_thumbs)
            return {
                "id": p.person_id,
                "label": p.label,
                "confirmed": p.confirmed,
                "source": p.source,
                "trusted": p.trusted,
                "face_count": len(p.face_ids),
                "faces": faces_with_thumbs,
            }

        items = list(await asyncio.gather(*[_person_entry(p) for p in page]))
        return {"total": total, "items": items}

    # Bulk actions. Declared before the ``/api/persons/{person_id}`` routes so
    # the static ``confirm-batch`` / ``delete-batch`` paths always win.
    # ``Body(embed=True)`` with a builtin ``list[int]`` is used instead of a
    # pydantic model because this module enables ``from __future__ import
    # annotations`` — a function-local model's string annotation would not
    # resolve and FastAPI would mistake the body for a query param.
    @router.post("/api/persons/confirm-batch")
    async def confirm_persons_batch(person_ids: list[int] = Body(..., embed=True)) -> dict:
        confirmed = db.confirm_persons(person_ids)
        return {"ok": True, "confirmed": confirmed}

    @router.post("/api/persons/delete-batch")
    async def delete_persons_batch(person_ids: list[int] = Body(..., embed=True)) -> dict:
        deleted = db.delete_persons(person_ids)
        return {"ok": True, "deleted": deleted}

    @router.get("/api/persons/{person_id}")
    async def get_person(person_id: int) -> dict:
        persons = db.get_persons()
        p = next((p for p in persons if p.person_id == person_id), None)
        if p is None:
            raise HTTPException(status_code=404, detail="Person not found")
        return {
            "id": p.person_id,
            "label": p.label,
            "confirmed": p.confirmed,
            "source": p.source,
            "trusted": p.trusted,
            "face_count": len(p.face_ids),
        }

    @router.get("/api/persons/{person_id}/faces")
    async def get_person_faces(person_id: int) -> list[dict]:
        import asyncio

        persons = db.get_persons()
        if not any(p.person_id == person_id for p in persons):
            raise HTTPException(status_code=404, detail="Person not found")
        faces = db.get_faces_for_person(person_id)

        def _gen_thumbs() -> list[dict]:
            return [
                {
                    **f,
                    "thumb": face_thumbnail_b64(
                        f["image_path"],
                        f["bbox_x"],
                        f["bbox_y"],
                        f["bbox_w"],
                        f["bbox_h"],
                    ),
                }
                for f in faces
            ]

        return await asyncio.to_thread(_gen_thumbs)

    @router.get("/api/faces/unassigned")
    async def list_unassigned_faces(offset: int = 0, limit: int = 40) -> dict:
        """Return a page of faces with no person assignment, with thumbnails."""
        import asyncio

        limit = min(max(limit, 1), 200)
        all_faces = db.get_unassigned_faces()
        total = len(all_faces)
        page = all_faces[offset : offset + limit]

        def _gen_thumbs() -> list[dict]:
            return [
                {
                    **f,
                    "thumb": face_thumbnail_b64(
                        f["image_path"],
                        f["bbox_x"],
                        f["bbox_y"],
                        f["bbox_w"],
                        f["bbox_h"],
                    ),
                }
                for f in page
            ]

        items = await asyncio.to_thread(_gen_thumbs)
        return {"total": total, "items": items}

    class _AssignBatchBody(BaseModel):
        face_ids: list[int]
        person_id: int | None = None
        label: str = ""

    @router.post("/api/faces/assign-batch")
    async def assign_faces_batch(body: _AssignBatchBody) -> dict:
        """Assign multiple faces to a person.

        If ``person_id`` is provided, faces are assigned to that person.
        If ``person_id`` is None, a new person is created (with optional ``label``).
        """
        if not body.face_ids:
            raise HTTPException(status_code=400, detail="face_ids must not be empty")
        if body.person_id is not None:
            target_id = body.person_id
        else:
            target_id = db.create_person(
                label=body.label,
                confirmed=bool(body.label),
                trusted=bool(body.label),
            )
        for fid in body.face_ids:
            db.set_person_id(fid, target_id)
        return {"ok": True, "person_id": target_id}

    @router.get("/api/faces/{face_id}/preview")
    async def face_preview(face_id: int) -> Response:
        """Render a cropped, bbox-annotated preview JPEG for one detected face.

        Raises:
            HTTPException: 404 if the face id is unknown or the source image
                cannot be read/decoded.
        """
        from io import BytesIO

        from fastapi.responses import Response
        from PIL import Image, ImageDraw

        from pyimgtag.heic_converter import convert_heic_to_jpeg, is_heic

        face = db.get_face_by_id(face_id)
        if face is None:
            raise HTTPException(status_code=404, detail="Face not found")

        image_path = face["image_path"]
        try:
            if is_heic(image_path):
                image_path = str(convert_heic_to_jpeg(image_path))
            img = Image.open(image_path).convert("RGB")
        except Exception as exc:  # noqa: BLE001 — PIL/HEIC decode can fail many ways
            # Strip CR/LF from the request-influenced values so they cannot
            # forge extra log lines (CodeQL py/log-injection).
            logger.warning(
                "face preview: could not read image %s for face %s: %s",
                str(image_path).replace("\n", " ").replace("\r", " "),
                str(face_id).replace("\n", " ").replace("\r", " "),
                str(exc).replace("\n", " ").replace("\r", " "),
            )
            raise HTTPException(status_code=404, detail="Image not readable") from exc

        # Scale bbox from detection space (max_dim=1280) to full-image coords.
        # face_detection resizes images to 1280px on the long side before detecting,
        # so all stored bbox values are in that coordinate space.
        detect_max = 1280
        iw, ih = img.size
        if max(iw, ih) > detect_max:
            det_scale = detect_max / max(iw, ih)
            rw = int(iw * det_scale)
            inv = iw / rw
            bx = round(face["bbox_x"] * inv)
            by = round(face["bbox_y"] * inv)
            bw = round(face["bbox_w"] * inv)
            bh = round(face["bbox_h"] * inv)
        else:
            bx = face["bbox_x"]
            by = face["bbox_y"]
            bw = face["bbox_w"]
            bh = face["bbox_h"]

        draw = ImageDraw.Draw(img)
        lw = max(2, round(max(bw, bh) / 30))
        draw.rectangle([bx, by, bx + bw, by + bh], outline="red", width=lw)

        # Crop to the face region with generous padding so the preview is an
        # enlarged face image rather than a tiny red box on a full photo.
        pad = int(max(bw, bh) * 0.8)
        left = max(0, bx - pad)
        top = max(0, by - pad)
        right = min(iw, bx + bw + pad)
        bottom = min(ih, by + bh + pad)
        cropped = img.crop((left, top, right, bottom))
        cropped.thumbnail((400, 400), Image.Resampling.LANCZOS)

        buf = BytesIO()
        cropped.save(buf, format="JPEG", quality=85)
        return Response(content=buf.getvalue(), media_type="image/jpeg")

    async def update_label(person_id: int, body: _LabelBody = Body(...)) -> dict:
        db.update_person_label(person_id, body.label)
        return {"ok": True}

    # PEP 563 turns annotations into strings; patch the annotation to the
    # actual class object before FastAPI builds the TypeAdapter for this route.
    update_label.__annotations__["body"] = _LabelBody
    router.post("/api/persons/{person_id}/label")(update_label)

    @router.post("/api/persons/{source_id}/merge/{target_id}")
    async def merge_persons(source_id: int, target_id: int) -> dict:
        db.merge_persons(source_id=source_id, target_id=target_id)
        return {"ok": True}

    @router.delete("/api/persons/{person_id}")
    async def delete_person(person_id: int) -> dict:
        db.delete_person(person_id)
        return {"ok": True}

    @router.get("/api/faces/ignored")
    async def list_ignored_faces(offset: int = 0, limit: int = 40) -> dict:
        """Return a page of ignored (trashed) faces with thumbnails."""
        import asyncio

        limit = min(max(limit, 1), 200)
        all_faces = db.get_ignored_faces()
        total = len(all_faces)
        page = all_faces[offset : offset + limit]

        def _gen_thumbs() -> list[dict]:
            return [
                {
                    **f,
                    "thumb": face_thumbnail_b64(
                        f["image_path"],
                        f["bbox_x"],
                        f["bbox_y"],
                        f["bbox_w"],
                        f["bbox_h"],
                    ),
                }
                for f in page
            ]

        items = await asyncio.to_thread(_gen_thumbs)
        return {"total": total, "items": items}

    @router.post("/api/faces/{face_id}/ignore")
    async def ignore_face(face_id: int) -> dict:
        db.ignore_face(face_id)
        return {"ok": True}

    @router.post("/api/faces/{face_id}/restore")
    async def restore_face(face_id: int) -> dict:
        db.restore_face(face_id)
        return {"ok": True}

    @router.post("/api/faces/{face_id}/unassign")
    async def unassign_face(face_id: int) -> dict:
        db.unassign_face(face_id)
        return {"ok": True}

    @router.post("/api/persons/{person_id}/confirm")
    async def confirm_person(person_id: int) -> dict:
        db.confirm_person(person_id)
        return {"ok": True}

    return router
