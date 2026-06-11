"""Faces UI routes as a reusable APIRouter factory.

Exposes the faces management surfaces: the persons grid (with face
thumbnails), unassigned-faces and trash assignment, person rename and merge,
and per-face preview rendering. ``render_person_detail_html`` coerces the
incoming ``person_id`` through ``int()`` to eliminate the URL XSS taint path —
keep that coercion when refactoring.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)


def render_faces_html(api_base: str = "") -> str:
    """Return the faces UI HTML with the given API base prefix inserted."""
    from pyimgtag.webapp.nav import MODAL_HTML, MODAL_JS, NAV_STYLES, render_nav
    from pyimgtag.webapp.templating import Markup, render

    return render(
        "faces.html",
        api_base=Markup(api_base),
        nav=Markup(render_nav("faces")),
        nav_styles=Markup(NAV_STYLES),
        modal_html=Markup(MODAL_HTML),
        modal_js=Markup(MODAL_JS),
    )


def render_person_detail_html(person_id: int, api_base: str = "") -> str:
    """Return the person detail page HTML."""
    from pyimgtag.webapp.nav import MODAL_HTML, MODAL_JS, NAV_STYLES, render_nav
    from pyimgtag.webapp.templating import Markup, render

    # Coerce through int() so the substituted value is guaranteed to be
    # a digit-only string — eliminates the XSS taint path from the URL.
    safe_id = str(int(person_id))
    return render(
        "faces_person.html",
        person_id=safe_id,
        api_base=Markup(api_base),
        nav=Markup(render_nav("faces")),
        nav_styles=Markup(NAV_STYLES),
        modal_html=Markup(MODAL_HTML),
        modal_js=Markup(MODAL_JS),
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

    from pyimgtag.face.thumb import face_thumbnail_b64

    async def _thumbs(faces: list[dict]) -> list[dict]:
        """Attach a base64 ``thumb`` to each face, cropping off the event loop.

        On request cancellation — the client navigated away mid-load, or the
        server is shutting down (Ctrl+C) — return the faces with ``thumb=None``
        instead of letting ``CancelledError`` propagate out of the handler.
        A propagated ``CancelledError`` is logged by uvicorn as a noisy
        "Exception in ASGI application" traceback even though it is the normal,
        expected outcome of a cancelled request; the response is discarded
        anyway, so the empty thumbnails are never rendered.
        """
        import asyncio

        def _work() -> list[dict]:
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

        try:
            return await asyncio.to_thread(_work)
        except asyncio.CancelledError:
            return [{**f, "thumb": None} for f in faces]

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_faces_html(api_base)

    @router.get("/persons/{person_id}")
    async def person_detail(person_id: int):
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
            faces_with_thumbs = await _thumbs(faces[:8])
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
    async def get_person_faces(person_id: int, offset: int = 0, limit: int = 60) -> dict:
        """Return one page of a person's faces, highest-confidence first.

        Paginated so a person with thousands of faces does not load — and
        thumbnail — them all at once; only the requested page's thumbnails are
        generated.

        Query params:
          offset  – 0-based index of the first face to return (default 0)
          limit   – faces per page (default 60, max 200)

        Response: ``{"total": N, "items": [...]}``.
        """
        limit = min(max(limit, 1), 200)
        persons = db.get_persons()
        if not any(p.person_id == person_id for p in persons):
            raise HTTPException(status_code=404, detail="Person not found")
        faces = db.get_faces_for_person(person_id)
        # Best matches first so page 1 shows the hero + strongest faces, and
        # pagination order is stable across pages.
        faces.sort(key=lambda f: f.get("confidence") or 0.0, reverse=True)
        total = len(faces)
        page = faces[offset : offset + limit]

        items = await _thumbs(page)
        return {"total": total, "items": items}

    @router.get("/api/persons/{person_id}/candidates")
    async def list_candidate_faces(
        person_id: int, source: str = "unassigned", offset: int = 0, limit: int = 40
    ) -> dict:
        """Return a page of candidate faces to add to this person.

        ``source="unassigned"`` lists faces not assigned to any person;
        ``source="biggest"`` lists faces from the largest *other* cluster (a
        common case: a person was split into two clusters and you want to fold
        the big one in). Highest-confidence first; only the page is thumbnailed.

        Response: ``{"total": N, "items": [...], "source_label": str | None}``
        where ``source_label`` names the cluster the candidates came from (only
        set for ``source="biggest"``).
        """
        limit = min(max(limit, 1), 200)
        persons = db.get_persons()
        if not any(p.person_id == person_id for p in persons):
            raise HTTPException(status_code=404, detail="Person not found")

        source_label = None
        if source == "biggest":
            others = [p for p in persons if p.person_id != person_id and p.face_ids]
            biggest = max(others, key=lambda p: len(p.face_ids), default=None)
            if biggest is None:
                faces: list[dict] = []
            else:
                faces = db.get_faces_for_person(biggest.person_id)
                source_label = biggest.label or f"Person {biggest.person_id}"
        else:
            faces = db.get_unassigned_faces()

        faces.sort(key=lambda f: f.get("confidence") or 0.0, reverse=True)
        total = len(faces)
        page = faces[offset : offset + limit]

        items = await _thumbs(page)
        return {"total": total, "items": items, "source_label": source_label}

    @router.get("/api/faces/unassigned")
    async def list_unassigned_faces(offset: int = 0, limit: int = 40) -> dict:
        """Return a page of faces with no person assignment, with thumbnails."""
        limit = min(max(limit, 1), 200)
        all_faces = db.get_unassigned_faces()
        total = len(all_faces)
        page = all_faces[offset : offset + limit]

        items = await _thumbs(page)
        return {"total": total, "items": items}

    @router.post("/api/faces/assign-batch")
    async def assign_faces_batch(
        face_ids: list[int] = Body(...),
        person_id: int | None = Body(None),
        label: str = Body(""),
    ) -> dict:
        """Assign multiple faces to a person.

        If ``person_id`` is provided, faces are assigned to that person.
        If ``person_id`` is None, a new person is created (with optional ``label``).

        Body params are declared with ``Body(...)`` rather than a pydantic model
        because this module uses ``from __future__ import annotations``: a
        function-local model's string annotation would not resolve and FastAPI
        would treat the body as a query parameter (returning 422).
        """
        if not face_ids:
            raise HTTPException(status_code=400, detail="face_ids must not be empty")
        if person_id is not None:
            # Reject unknown ids so faces are never left pointing at a person
            # row that doesn't exist (a dangling assignment).
            if not any(p.person_id == person_id for p in db.get_persons()):
                raise HTTPException(status_code=404, detail="Person not found")
            target_id = person_id
        else:
            target_id = db.create_person(
                label=label,
                confirmed=bool(label),
                trusted=bool(label),
            )
        for fid in face_ids:
            db.set_person_id(fid, target_id)
        return {"ok": True, "person_id": target_id}

    @router.get("/api/faces/{face_id}/preview")
    async def face_preview(face_id: int):
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
        converted = None
        try:
            if is_heic(image_path):
                # convert_heic_to_jpeg with no output_dir hands us a JPEG inside
                # a fresh temp dir that *we* own — clean it up after decoding.
                converted = convert_heic_to_jpeg(image_path)
                image_path = str(converted)
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
        finally:
            if converted is not None:
                converted.unlink(missing_ok=True)
                with contextlib.suppress(OSError):
                    converted.parent.rmdir()  # removes the owned mkdtemp dir only when empty

        # Scale bbox from detection space (max_dim=1280) to full-image coords.
        # face.detection resizes images to 1280px on the long side before detecting,
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
        try:
            db.merge_persons(source_id=source_id, target_id=target_id)
        except ValueError as exc:
            # Unknown merge target — a client error, not a 500.
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @router.delete("/api/persons/{person_id}")
    async def delete_person(person_id: int) -> dict:
        db.delete_person(person_id)
        return {"ok": True}

    @router.get("/api/faces/ignored")
    async def list_ignored_faces(offset: int = 0, limit: int = 40) -> dict:
        """Return a page of ignored (trashed) faces with thumbnails."""
        limit = min(max(limit, 1), 200)
        all_faces = db.get_ignored_faces()
        total = len(all_faces)
        page = all_faces[offset : offset + limit]

        items = await _thumbs(page)
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
