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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyimgtag.db.dedup_db import ACTION_MOVE, ACTION_TAG, ACTION_TRASH
from pyimgtag.dedup_groups import (
    CRITERIA,
    action_never_completed,
    free_destination,
    in_place,
    is_photos_library_path,
    parse_prefer,
    quarantine_destination,
    safe_move,
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
    if group.get("keep_path") != keep_path:
        # Pin the keeper before acting, so a re-run keeps the same copy.
        db.set_dedup_keep_path(group["id"], keep_path)
    for member in group["members"]:
        path = member["file_path"]
        if path == keep_path:
            if action_never_completed(member):
                db.clear_dedup_action(group["id"], path)  # stale record
            continue
        if member.get("action") in (ACTION_MOVE, ACTION_TRASH):
            if not action_never_completed(member):
                acted = True  # done by an earlier run that stopped part-way
                continue
            db.clear_dedup_action(group["id"], path)
        if is_photos_library_path(path):
            db.record_dedup_action(group["id"], path, ACTION_TAG)
            tagged += 1
            acted = True
            continue
        dest = free_destination(quarantine_destination(path, move_to))
        # Record before moving, so no file reaches the quarantine unrecorded.
        db.record_dedup_action(group["id"], path, ACTION_MOVE, str(dest))
        try:
            safe_move(path, dest)
        except OSError as exc:
            db.clear_dedup_action(group["id"], path)
            logger.warning("dedup apply: move failed for %s: %s", path, exc)
            errors.append(f"{Path(path).name}: {exc}")
            continue
        moved += 1
        acted = True
    if acted:
        db.mark_dedup_resolved(group["id"], keep_path)
    elif not any(
        m.get("action") for m in (db.get_dedup_group(group["id"]) or {}).get("members", [])
    ):
        db.set_dedup_keep_path(group["id"], None)  # nothing done: drop the pin
    return {"moved": moved, "tagged": tagged, "errors": errors}


def _undo_group(db: ProgressDB, group: dict) -> dict:
    """Move a group's quarantined copies back and clear its records."""
    restored = 0
    errors: list[str] = []
    lost: list[str] = []
    for member in group["members"]:
        if member.get("action") != ACTION_MOVE or not member.get("moved_to"):
            continue
        source = Path(member["moved_to"])
        target = Path(member["file_path"])
        if target.exists():
            continue  # the original is in place; never overwrite it
        if not source.exists() and not source.parent.is_dir():
            # Unplugged or unmounted quarantine: keep the record for later.
            errors.append(
                f"{source.name}: the quarantine is not reachable. Connect the drive and undo "
                "again; if that folder was deleted on purpose, run 'pyimgtag dedup undo "
                f"--group {group['id']} --forget-missing'"
            )
            continue
        if not source.exists():
            # Gone from both places: report it, but do not keep the group
            # locked forever over a copy nothing can bring back.
            lost.append(f"{target.name}: neither the original nor the quarantined copy exists")
            continue
        if not target.parent.is_dir():
            # Unplugged library drive: do not re-create the folder elsewhere.
            errors.append(f"{target.name}: the original location is not reachable")
            continue
        try:
            safe_move(source, target, create_parents=False)
        except OSError as exc:
            logger.warning("dedup undo: restore failed for %s: %s", target, exc)
            errors.append(f"{target.name}: {exc}")
            continue
        restored += 1
    if not errors:
        db.undo_dedup_group(group["id"])
    return {"restored": restored, "errors": errors + lost}


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
            # A fixed message rather than str(exc): the exception text is not sent
            # to the client (CodeQL py/stack-trace-exposure); the valid names are
            # all the caller needs to correct the request.
            del exc
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "invalid prefer order; use a comma list of distinct criteria from: "
                    + ", ".join(CRITERIA),
                },
            )

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
                members = {m["file_path"]: m for m in group["members"]}
                if keep_path not in members:
                    errors.append(f"group {group_id}: keep path is not a member")
                    continue
                if not in_place(members[keep_path]):
                    errors.append(
                        f"group {group_id}: the copy to keep was already moved; "
                        "undo the group first"
                    )
                    continue
                stored = raw.get("keep_path")
                acted = any(m.get("action") for m in raw["members"])
                if acted and stored and keep_path != stored:
                    errors.append(
                        f"group {group_id}: a run already started keeping "
                        f"{Path(stored).name}; undo the group to choose another copy"
                    )
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
