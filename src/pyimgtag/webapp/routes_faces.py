"""Faces UI routes as a reusable APIRouter factory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

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
    .faces-grid{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px}
    .face-thumb{width:60px;height:60px;object-fit:cover;border-radius:var(--radius-sm);
                border:1px solid var(--border);cursor:pointer;transition:opacity .15s}
    .face-thumb:hover{opacity:.7}
    .person-actions{display:flex;gap:6px;flex-wrap:wrap}
    .person-actions button{padding:5px 12px;border-radius:var(--radius-sm);font-size:12px;
                           font-weight:500;border:1px solid var(--border);
                           background:var(--surface);color:var(--text);cursor:pointer;
                           transition:all .15s}
    .person-actions button:hover{border-color:var(--accent);color:var(--accent)}
    .del-btn{color:var(--danger)!important;border-color:rgba(255,59,48,.3)!important}
    .del-btn:hover{background:rgba(255,59,48,.05)!important}
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
<div id="persons"></div>
<script>
// Hover preview overlay
const _preview = document.createElement('div');
_preview.style.cssText = 'display:none;position:fixed;z-index:9999;pointer-events:none;'
  + 'border-radius:8px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,.45);';
document.body.appendChild(_preview);
const _previewImg = document.createElement('img');
_previewImg.style.cssText = 'display:block;max-width:320px;max-height:320px;';
_preview.appendChild(_previewImg);

function showPreview(faceId, rect) {
  _previewImg.src = '__API_BASE__/api/faces/' + faceId + '/preview';
  const left = Math.min(rect.right + 10, window.innerWidth - 340);
  const top  = Math.max(rect.top, 8);
  _preview.style.left = left + 'px';
  _preview.style.top  = top  + 'px';
  _preview.style.display = 'block';
}
function hidePreview() { _preview.style.display = 'none'; }

async function load() {
  const resp = await fetch('__API_BASE__/api/persons');
  const persons = await resp.json();
  document.getElementById('status').textContent = persons.length + ' person(s)';
  const grid = document.getElementById('persons');
  grid.innerHTML = '';
  for (const p of persons) {
    const fr = await fetch('__API_BASE__/api/persons/' + p.id + '/faces');
    const faces = await fr.json();
    grid.appendChild(renderPerson(p, faces));
  }
}

function renderPerson(p, faces) {
  const card = document.createElement('div');
  card.className = 'person-card';

  const nameEl = document.createElement('div');
  nameEl.className = 'person-name';
  nameEl.textContent = p.label || ('(unlabelled #' + p.id + ')');
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

  const facesGrid = document.createElement('div');
  facesGrid.className = 'faces-grid';
  for (const f of faces.slice(0, 8)) {
    if (!f.thumb) continue;
    const img = document.createElement('img');
    img.className = 'face-thumb';
    img.src = 'data:image/jpeg;base64,' + f.thumb;
    img.title = 'Click to unassign';
    img.addEventListener('mouseenter', () => showPreview(f.id, img.getBoundingClientRect()));
    img.addEventListener('mouseleave', hidePreview);
    img.addEventListener('click', async () => {
      hidePreview();
      await fetch('__API_BASE__/api/faces/' + f.id + '/unassign', {method: 'POST'});
      load();
    });
    facesGrid.appendChild(img);
  }
  card.appendChild(facesGrid);

  const acts = document.createElement('div');
  acts.className = 'person-actions';

  const renBtn = document.createElement('button');
  renBtn.textContent = 'Rename';
  renBtn.addEventListener('click', () => {
    openModal(
      'Rename person',
      'Enter a new name for this person.',
      '<input class="inp" id="m-inp" placeholder="Name" />',
      'Rename', 'btn-primary',
      async () => {
        const val = document.getElementById('m-inp').value.trim();
        if (!val) return;
        await fetch('__API_BASE__/api/persons/' + p.id + '/label', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({label: val}),
        });
        closeModal();
        load();
      }
    );
    // Set value via DOM after modal renders — avoids XSS via innerHTML attribute
    document.getElementById('m-inp').value = p.label || '';
    setTimeout(() => document.getElementById('m-inp').focus(), 50);
  });
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

  card.appendChild(acts);
  return card;
}

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
        from fastapi.responses import HTMLResponse
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
            if p.face_ids or p.trusted  # skip non-trusted ghost persons with no faces
        ]

    @router.get("/api/persons/{person_id}/faces")
    async def get_person_faces(person_id: int) -> list[dict]:
        persons = db.get_persons()
        if not any(p.person_id == person_id for p in persons):
            raise HTTPException(status_code=404, detail="Person not found")
        faces = db.get_faces_for_person(person_id)
        result = []
        for f in faces:
            thumb = face_thumbnail_b64(
                f["image_path"],
                f["bbox_x"],
                f["bbox_y"],
                f["bbox_w"],
                f["bbox_h"],
            )
            result.append({**f, "thumb": thumb})
        return result

    @router.get("/api/faces/{face_id}/preview")
    async def face_preview(face_id: int):  # type: ignore[return]
        from io import BytesIO

        from fastapi.responses import Response
        from PIL import Image, ImageDraw

        face = db.get_face_by_id(face_id)
        if face is None:
            raise HTTPException(status_code=404, detail="Face not found")
        try:
            img = Image.open(face["image_path"]).convert("RGB")
        except Exception:
            raise HTTPException(status_code=404, detail="Image not readable")

        # Scale bounding box to actual image size (face_detection resizes to max_dim)
        # The bbox is stored in resized coordinates; recover scale factor.
        max_dim = 1280
        w, h = img.size
        scale = min(max_dim / w, max_dim / h, 1.0)
        if scale < 1.0:
            rw = int(w * scale)
        else:
            rw = w
        factor = w / rw  # bbox was recorded at rw×rh, display at w×h

        x = int(face["bbox_x"] * factor)
        y = int(face["bbox_y"] * factor)
        bw = int(face["bbox_w"] * factor)
        bh = int(face["bbox_h"] * factor)

        draw = ImageDraw.Draw(img)
        lw = max(3, int(min(w, h) / 150))
        draw.rectangle([x, y, x + bw, y + bh], outline="red", width=lw)

        # Downscale for preview if large
        preview_max = 800
        if max(w, h) > preview_max:
            img.thumbnail((preview_max, preview_max), Image.LANCZOS)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
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

    @router.post("/api/faces/{face_id}/unassign")
    async def unassign_face(face_id: int) -> dict:
        db.unassign_face(face_id)
        return {"ok": True}

    return router
