"""FastAPI face management UI for pyimgtag."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pyimgtag Faces</title>
  <style>
    :root{--bg:#121212;--surface:#1e1e1e;--card:#252525;--accent:#bb86fc;
          --danger:#cf6679;--warn:#f9a825;--ok:#81c784;--text:#e0e0e0;
          --muted:#888;--border:#333}
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text)}
    header{position:sticky;top:0;background:var(--surface);border-bottom:1px solid var(--border);
           padding:.75rem 1.5rem;display:flex;align-items:center;gap:1rem}
    h1{font-size:1rem;font-weight:600;color:var(--accent)}
    #persons{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
             gap:.75rem;padding:1rem 1.5rem}
    .card{background:var(--card);border-radius:8px;overflow:hidden;border:1px solid var(--border);padding:.75rem}
    .name{font-weight:600;font-size:.9rem;margin-bottom:.4rem}
    .meta{font-size:.75rem;color:var(--muted);margin-bottom:.5rem}
    .badge{display:inline-block;font-size:.6rem;padding:.1rem .35rem;border-radius:3px;
           font-weight:700;text-transform:uppercase;margin-right:.3rem}
    .trusted{background:#1b4332;color:#81c784}
    .auto{background:#1a1a2e;color:#888}
    .faces{display:flex;flex-wrap:wrap;gap:4px;margin-top:.5rem}
    .face-thumb{width:60px;height:60px;object-fit:cover;border-radius:4px;border:1px solid var(--border)}
    .actions{display:flex;gap:.4rem;margin-top:.6rem;flex-wrap:wrap}
    button{padding:.25rem .6rem;font-size:.75rem;border:1px solid var(--border);background:transparent;
           color:var(--text);cursor:pointer;border-radius:4px}
    button:hover{background:var(--surface)}
    button.danger{color:var(--danger);border-color:var(--danger)}
    #status{margin-left:auto;font-size:.8rem;color:var(--muted)}
  </style>
</head>
<body>
<header>
  <h1>pyimgtag &mdash; Faces</h1>
  <span id="status">Loading&hellip;</span>
</header>
<div id="persons"></div>
<script>
async function load() {
  const resp = await fetch('/api/persons');
  const persons = await resp.json();
  const el = document.getElementById('persons');
  document.getElementById('status').textContent = persons.length + ' person(s)';
  el.innerHTML = '';
  for (const p of persons) {
    const card = document.createElement('div');
    card.className = 'card';
    const badge = p.trusted
      ? '<span class="badge trusted">&#9733; trusted</span>'
      : '<span class="badge auto">auto</span>';
    card.innerHTML = `
      <div class="name">${esc(p.label || '(unlabelled #' + p.id + ')')}</div>
      <div class="meta">${badge}${p.face_count} face(s) &bull; source: ${esc(p.source)}</div>
      <div class="faces" id="faces-${p.id}"></div>
      <div class="actions">
        <button onclick="rename(${p.id}, '${esc(p.label)}')">Rename</button>
        <button class="danger" onclick="deletePerson(${p.id})">Delete</button>
      </div>`;
    el.appendChild(card);
    loadFaces(p.id);
  }
}

async function loadFaces(pid) {
  const resp = await fetch('/api/persons/' + pid + '/faces');
  if (!resp.ok) return;
  const faces = await resp.json();
  const el = document.getElementById('faces-' + pid);
  if (!el) return;
  for (const f of faces.slice(0, 8)) {
    if (f.thumb) {
      const img = document.createElement('img');
      img.className = 'face-thumb';
      img.src = 'data:image/jpeg;base64,' + f.thumb;
      img.title = 'Face #' + f.id;
      img.onclick = () => unassign(f.id);
      el.appendChild(img);
    }
  }
}

async function rename(pid, cur) {
  const label = prompt('New name:', cur);
  if (!label) return;
  await fetch('/api/persons/' + pid + '/label', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({label})
  });
  load();
}

async function deletePerson(pid) {
  if (!confirm('Delete this person and unassign all their faces?')) return;
  await fetch('/api/persons/' + pid, {method: 'DELETE'});
  load();
}

async function unassign(fid) {
  if (!confirm('Remove this face from its person?')) return;
  await fetch('/api/faces/' + fid + '/unassign', {method: 'POST'});
  load();
}

function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

load();
</script>
</body>
</html>"""


def build_app(db: ProgressDB) -> Any:
    """Build and return the FastAPI application.

    Args:
        db: ProgressDB instance to use for all requests.

    Returns:
        FastAPI application instance.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import Body, FastAPI, HTTPException
        from fastapi.responses import HTMLResponse
        from pydantic import BaseModel
    except ImportError:
        raise ImportError(
            "fastapi is not installed. Install the [dev] extra: pip install pyimgtag[dev]"
        ) from None

    class _LabelBody(BaseModel):
        label: str

    from pyimgtag.face_thumb import face_thumbnail_b64

    app = FastAPI(title="pyimgtag Faces")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _HTML

    @app.get("/api/persons")
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
        ]

    @app.get("/api/persons/{person_id}/faces")
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

    async def update_label(person_id: int, body: _LabelBody = Body(...)) -> dict:
        db.update_person_label(person_id, body.label)
        return {"ok": True}

    # PEP 563 turns annotations into strings; patch the annotation to the
    # actual class object before FastAPI builds the TypeAdapter for this route.
    update_label.__annotations__["body"] = _LabelBody
    app.post("/api/persons/{person_id}/label")(update_label)

    @app.post("/api/persons/{source_id}/merge/{target_id}")
    async def merge_persons(source_id: int, target_id: int) -> dict:
        db.merge_persons(source_id=source_id, target_id=target_id)
        return {"ok": True}

    @app.delete("/api/persons/{person_id}")
    async def delete_person(person_id: int) -> dict:
        db.delete_person(person_id)
        return {"ok": True}

    @app.post("/api/faces/{face_id}/unassign")
    async def unassign_face(face_id: int) -> dict:
        db.unassign_face(face_id)
        return {"ok": True}

    return app


def run_server(db: ProgressDB, host: str = "127.0.0.1", port: int = 8766) -> None:
    """Start the face review server (blocking).

    Args:
        db: ProgressDB instance.
        host: Bind address.
        port: TCP port.

    Raises:
        ImportError: If uvicorn is not installed.
    """
    try:
        import uvicorn
    except ImportError:
        raise ImportError(
            "uvicorn is not installed. Install the [dev] extra: pip install pyimgtag[dev]"
        ) from None

    app = build_app(db)
    print(f"Face review UI: http://{host}:{port}/", flush=True)
    uvicorn.run(app, host=host, port=port)
