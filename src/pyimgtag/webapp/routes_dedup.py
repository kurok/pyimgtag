"""Dedup page routes: browse duplicate groups and quarantine the losers.

The page is the visual half of ``pyimgtag dedup``: it renders one card per
group with side-by-side thumbnails, highlights the best pick with the reasons
it won, and lets the user override which copy to keep before applying.

The web action is deliberately narrower than the CLI:

* **Move only.** ``POST /dedup/api/apply`` relocates losing copies into a
  quarantine directory the user types into the confirm modal. There is no
  delete path from the browser — trashing needs ``dedup resolve --delete --yes``
  on the command line.
* **Apple Photos originals are never touched on disk.** They are recorded with
  the ``tag`` action so ``dedup resolve --write-back`` can apply the keyword
  later; the browser never shells out to AppleScript.
* **Reversible.** ``POST /dedup/api/undo`` moves the quarantined files back and
  un-resolves the group, exactly like ``dedup undo``.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyimgtag.db.dedup_db import ACTION_MOVE, ACTION_TAG
from pyimgtag.dedup_groups import (
    is_photos_library_path,
    parse_prefer,
    quarantine_destination,
    summarize_group,
)

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB

logger = logging.getLogger(__name__)

#: Pre-filled quarantine directory offered by the confirm modal.
DEFAULT_QUARANTINE_DIR = "~/pyimgtag-duplicates"

try:
    from pydantic import BaseModel as _BaseModel

    class _ApplyBody(_BaseModel):
        group_ids: list[int] = []
        move_to: str = ""
        keep: dict[str, str] = {}

    class _UndoBody(_BaseModel):
        group_id: int = 0

except ImportError:  # pragma: no cover — exercised in minimal envs only
    _ApplyBody = None  # type: ignore[assignment,misc]
    _UndoBody = None  # type: ignore[assignment,misc]


def render_dedup_html(api_base: str = "") -> str:
    """Return the Dedup page HTML with the given API base prefix inserted."""
    from pyimgtag.webapp.nav import MODAL_HTML, MODAL_JS, NAV_STYLES, render_nav
    from pyimgtag.webapp.templating import Markup, render

    return render(
        "dedup.html",
        api_base=Markup(api_base),
        nav=Markup(render_nav("dedup")),
        nav_styles=Markup(NAV_STYLES),
        modal_html=Markup(MODAL_HTML),
        modal_js=Markup(MODAL_JS),
        default_quarantine=DEFAULT_QUARANTINE_DIR,
    )


def _apply_group(db: ProgressDB, group: dict, keep_path: str, move_to: str) -> dict:
    """Move every non-keeper of one group into *move_to*. Returns a small report."""
    moved = 0
    tagged = 0
    errors: list[str] = []
    acted = False
    for member in group["members"]:
        path = member["file_path"]
        if path == keep_path:
            continue
        if is_photos_library_path(path):
            db.record_dedup_action(group["id"], path, ACTION_TAG)
            tagged += 1
            acted = True
            continue
        dest = quarantine_destination(path, move_to)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(path, str(dest))
        except OSError as exc:
            logger.warning("dedup apply: move failed for %s: %s", path, exc)
            errors.append(f"{Path(path).name}: {exc}")
            continue
        db.record_dedup_action(group["id"], path, ACTION_MOVE, str(dest))
        moved += 1
        acted = True
    if acted:
        db.mark_dedup_resolved(group["id"], keep_path)
    return {"moved": moved, "tagged": tagged, "errors": errors}


def _undo_group(db: ProgressDB, group: dict) -> dict:
    """Move a group's quarantined copies back and clear its records."""
    restored = 0
    errors: list[str] = []
    for member in group["members"]:
        if member.get("action") != ACTION_MOVE or not member.get("moved_to"):
            continue
        source = Path(member["moved_to"])
        target = Path(member["file_path"])
        if not source.exists():
            errors.append(f"{source.name}: quarantined file is gone")
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        except OSError as exc:
            logger.warning("dedup undo: restore failed for %s: %s", target, exc)
            errors.append(f"{target.name}: {exc}")
            continue
        restored += 1
    if not errors:
        db.undo_dedup_group(group["id"])
    return {"restored": restored, "errors": errors}


def build_dedup_router(db: ProgressDB, api_base: str = "") -> Any:
    """Build and return a FastAPI APIRouter for the dedup page.

    Args:
        db: An open ProgressDB instance.
        api_base: URL prefix inserted into HTML (e.g. ``"/dedup"`` or ``""``).

    Returns:
        A configured APIRouter ready to be included in a FastAPI app.

    Raises:
        ImportError: If fastapi is not installed.
    """
    try:
        from fastapi import APIRouter, Body, Query
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for the dedup UI. Install with: pip install 'pyimgtag[review]'"
        ) from exc

    import asyncio

    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return render_dedup_html(api_base)

    @router.get("/api/groups")
    async def get_groups(
        include_resolved: int = Query(default=0, ge=0, le=1),
        prefer: str = Query(default=""),
    ) -> Any:
        try:
            order = parse_prefer(prefer or None)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})

        def _load() -> dict:
            groups = [
                summarize_group(g, order)
                for g in db.list_dedup_groups(include_resolved=bool(include_resolved))
            ]
            totals = db.get_dedup_totals()
            return {
                "groups": groups,
                "prefer": list(order),
                "total_groups": totals["groups"],
                "reclaimable_bytes": sum(g["reclaimable_bytes"] for g in groups),
            }

        return await asyncio.to_thread(_load)

    @router.post("/api/apply")
    async def apply(body: _ApplyBody = Body(...)) -> Any:
        """Move the losing copies of the selected groups into a quarantine dir."""
        move_to = (body.move_to or "").strip()
        if not move_to:
            return JSONResponse(status_code=400, content={"ok": False, "error": "move_to_required"})
        if not body.group_ids:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "no_groups_selected"}
            )

        def _run() -> dict:
            moved = tagged = 0
            errors: list[str] = []
            applied: list[int] = []
            for group_id in body.group_ids:
                raw = db.get_dedup_group(int(group_id))
                if raw is None or raw.get("resolved_at"):
                    errors.append(f"group {group_id}: not an unresolved group")
                    continue
                group = summarize_group(raw)
                keep_path = body.keep.get(str(group_id)) or group["best_path"]
                if keep_path not in {m["file_path"] for m in group["members"]}:
                    errors.append(f"group {group_id}: keep path is not a member")
                    continue
                report = _apply_group(db, group, keep_path, move_to)
                moved += report["moved"]
                tagged += report["tagged"]
                errors.extend(report["errors"])
                applied.append(int(group_id))
            return {
                "ok": not errors,
                "moved": moved,
                "tagged": tagged,
                "groups": applied,
                "errors": errors,
            }

        return await asyncio.to_thread(_run)

    @router.post("/api/undo")
    async def undo(body: _UndoBody = Body(...)) -> Any:
        """Restore one group's quarantined copies and un-resolve it."""

        def _run() -> dict:
            raw = db.get_dedup_group(int(body.group_id))
            if raw is None:
                return {"ok": False, "error": "group_not_found"}
            report = _undo_group(db, raw)
            return {"ok": not report["errors"], **report}

        result = await asyncio.to_thread(_run)
        if not result.get("ok") and result.get("error") == "group_not_found":
            return JSONResponse(status_code=404, content=result)
        return result

    return router
