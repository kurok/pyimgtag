"""Handlers for the ``dedup`` subcommand group.

The safety model is graduated and every step is opt-in:

1. ``dedup scan`` only reads images and writes hashes + a plan to the DB.
2. ``dedup list`` / ``dedup resolve`` with no action flag are **report-only** —
   nothing on disk and nothing in the plan changes.
3. ``dedup resolve --move-to DIR`` relocates the losing copies under ``DIR``,
   preserving their path structure, and records where each one went so
   ``dedup undo`` can put them back.
4. ``dedup resolve --delete --yes`` sends losers to the OS trash via
   ``send2trash`` (never ``os.remove``), and needs both flags.

Photos living inside an Apple Photos library are never touched on disk at any
level: the resolve step records a ``tag`` action for them and, with
``--write-back`` on macOS, adds the ``pyimgtag:duplicate`` keyword so they can
be cleaned up from a smart album inside Photos.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from pyimgtag.db.dedup_db import ACTION_MOVE, ACTION_TAG, ACTION_TRASH
from pyimgtag.dedup_groups import (
    DEFAULT_THRESHOLD,
    is_photos_library_path,
    parse_prefer,
    quarantine_destination,
    summarize_group,
)
from pyimgtag.insights_report import format_bytes
from pyimgtag.progress_db import ProgressDB

#: Keyword written to Apple Photos originals that lost a duplicate comparison.
DUPLICATE_KEYWORD = "pyimgtag:duplicate"


def cmd_dedup(args: argparse.Namespace) -> int:
    """Dispatch dedup subcommands."""
    action = getattr(args, "dedup_action", None)
    if action is None:
        print("Usage: pyimgtag dedup <scan|list|resolve|undo>", file=sys.stderr)
        return 1
    if action == "scan":
        return _handle_scan(args)
    if action == "list":
        return _handle_list(args)
    if action == "resolve":
        return _handle_resolve(args)
    if action == "undo":
        return _handle_undo(args)
    print(f"Unknown dedup action: {action}", file=sys.stderr)
    return 1


# --- helpers ---------------------------------------------------------------


def _measure(path: Path) -> tuple[str | None, int | None, int | None]:
    """Return ``(phash, width, height)`` for an image, or ``(None, None, None)``."""
    from PIL import Image

    from pyimgtag.dedup import compute_phash

    phash = compute_phash(path)
    width: int | None = None
    height: int | None = None
    try:
        with Image.open(path) as img:
            width, height = img.size
    except (OSError, ValueError, TypeError):
        pass
    return phash, width, height


def _resolve_prefer(args: argparse.Namespace) -> tuple[str, ...]:
    return parse_prefer(getattr(args, "prefer", None))


# --- scan ------------------------------------------------------------------


def _handle_scan(args: argparse.Namespace) -> int:
    """Hash DB rows that need it, then rebuild the duplicate plan."""
    from pyimgtag.dedup_groups import group_by_phash

    threshold = int(getattr(args, "threshold", DEFAULT_THRESHOLD))
    hashed = 0
    missing = 0
    failed = 0
    with ProgressDB(db_path=args.db) as db:
        for raw_path in list(db.iter_paths_missing_phash(include_hashed=bool(args.rehash))):
            path = Path(raw_path)
            if not path.exists():
                missing += 1
                continue
            phash, width, height = _measure(path)
            if phash is None:
                failed += 1
                continue
            db.set_phash(raw_path, phash, width, height)
            hashed += 1

        records = db.all_phashes()
        groups = group_by_phash(records, threshold)
        db.replace_unresolved_dedup_groups([(g.kind, list(g.paths)) for g in groups], threshold)
        stored = db.list_dedup_groups()

    duplicates = sum(1 for g in stored if g["kind"] == "duplicate")
    bursts = len(stored) - duplicates
    print(f"Hashed {hashed} image(s); {len(records)} row(s) have a perceptual hash.")
    if missing:
        print(f"Skipped {missing} row(s) whose file is missing from disk.")
    if failed:
        print(f"Skipped {failed} row(s) that could not be decoded.")
    print(
        f"{len(stored)} unresolved group(s) at threshold {threshold}: "
        f"{duplicates} duplicate, {bursts} burst."
    )
    if stored:
        print("Next: pyimgtag dedup list")
    return 0


# --- list ------------------------------------------------------------------


def _handle_list(args: argparse.Namespace) -> int:
    """Print the current duplicate plan as a table or JSON."""
    try:
        prefer = _resolve_prefer(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    with ProgressDB(db_path=args.db) as db:
        groups = [
            summarize_group(g, prefer)
            for g in db.list_dedup_groups(include_resolved=bool(args.include_resolved))
        ]

    if args.format == "json":
        print(json.dumps({"groups": groups}, indent=2, sort_keys=True))
        return 0

    if not groups:
        print("No duplicate groups. Run 'pyimgtag dedup scan' first.", file=sys.stderr)
        return 0

    print(f"{'ID':>5}  {'KIND':<9} {'N':>3}  {'RECLAIMABLE':>11}  BEST PICK")
    print("-" * 78)
    total = 0
    for group in groups:
        total += group["reclaimable_bytes"]
        state = " (resolved)" if group.get("resolved_at") else ""
        print(
            f"{group['id']:>5}  {group['kind']:<9} {group['count']:>3}  "
            f"{format_bytes(group['reclaimable_bytes']):>11}  {group['best_path']}{state}"
        )
    print(f"\n{len(groups)} group(s), {format_bytes(total)} reclaimable.")
    return 0


# --- resolve ---------------------------------------------------------------


def _apply_photos_tag(file_path: str, write_back: bool) -> str | None:
    """Add the duplicate keyword in Apple Photos. Returns an error string or None."""
    if not write_back:
        return None
    if sys.platform != "darwin":
        return "write-back is macOS only"
    from pyimgtag.applescript_writer import write_to_photos

    return write_to_photos(file_path, [DUPLICATE_KEYWORD], None, mode="append")


def _move_loser(file_path: str, move_to: str) -> tuple[str | None, str | None]:
    """Move one loser into the quarantine dir. Returns ``(moved_to, error)``."""
    dest = quarantine_destination(file_path, move_to)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(file_path, str(dest))
    except OSError as exc:
        return None, str(exc)
    return str(dest), None


def _trash_loser(file_path: str) -> str | None:
    """Send one loser to the OS trash. Returns an error string or None."""
    try:
        from send2trash import send2trash
    except ImportError:
        return "send2trash is required for --delete. Install with: pip install 'pyimgtag[dedup]'"
    try:
        send2trash(file_path)
    except Exception as exc:  # noqa: BLE001 — send2trash raises backend-specific errors
        return str(exc)
    return None


def _handle_resolve(args: argparse.Namespace) -> int:  # noqa: C901 — one plan loop, one action switch
    """Print the resolution plan and, when asked, carry it out."""
    try:
        prefer = _resolve_prefer(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.delete and not args.yes:
        print("error: --delete requires --yes (nothing was touched)", file=sys.stderr)
        return 2

    # Report-only unless an action flag is present; --dry-run forces it.
    acting = bool(args.move_to or args.delete) and not args.dry_run

    with ProgressDB(db_path=args.db) as db:
        groups = [summarize_group(g, prefer) for g in db.list_dedup_groups()]
        if args.group is not None:
            groups = [g for g in groups if g["id"] == args.group]
            if not groups:
                print(f"No unresolved group with id {args.group}.", file=sys.stderr)
                return 1
        if not groups:
            print("No duplicate groups. Run 'pyimgtag dedup scan' first.", file=sys.stderr)
            return 0

        prefix = "" if acting else "[plan] "
        acted_groups = 0
        errors = 0
        reclaimed = 0
        for group in groups:
            print(f"{prefix}group {group['id']} ({group['kind']}, {group['count']} copies)")
            print(f"{prefix}  keep {group['best_path']}")
            group_acted = False
            for member in group["members"]:
                path = member["file_path"]
                if member["is_best"]:
                    continue
                if is_photos_library_path(path):
                    print(f"{prefix}  tag  {path}  (Apple Photos original — never touched on disk)")
                    if acting:
                        err = _apply_photos_tag(path, bool(args.write_back))
                        if err:
                            print(f"       write-back failed: {err}", file=sys.stderr)
                            errors += 1
                        db.record_dedup_action(group["id"], path, ACTION_TAG)
                        group_acted = True
                    continue
                if args.delete:
                    print(f"{prefix}  trash {path}")
                    if acting:
                        err = _trash_loser(path)
                        if err:
                            print(f"       trash failed: {err}", file=sys.stderr)
                            errors += 1
                            continue
                        db.record_dedup_action(group["id"], path, ACTION_TRASH)
                        group_acted = True
                        reclaimed += int(member.get("file_size") or 0)
                    continue
                if args.move_to:
                    dest = quarantine_destination(path, args.move_to)
                    print(f"{prefix}  move {path} -> {dest}")
                    if acting:
                        moved_to, err = _move_loser(path, args.move_to)
                        if err or moved_to is None:
                            print(f"       move failed: {err}", file=sys.stderr)
                            errors += 1
                            continue
                        db.record_dedup_action(group["id"], path, ACTION_MOVE, moved_to)
                        group_acted = True
                        reclaimed += int(member.get("file_size") or 0)
                    continue
                print(f"{prefix}  keep-for-now {path}  (no action flag given)")
            if acting and group_acted:
                db.mark_dedup_resolved(group["id"], group["best_path"])
                acted_groups += 1

    if not acting:
        total = sum(g["reclaimable_bytes"] for g in groups)
        print(
            f"\n[plan] {len(groups)} group(s), {format_bytes(total)} reclaimable. "
            "Nothing was changed."
        )
        if not (args.move_to or args.delete):
            print("[plan] Add --move-to DIR (reversible) or --delete --yes to act.")
        return 0
    print(f"\nResolved {acted_groups} group(s), {format_bytes(reclaimed)} reclaimed.")
    if args.move_to:
        print("Reverse with: pyimgtag dedup undo")
    return 1 if errors else 0


# --- undo ------------------------------------------------------------------


def _handle_undo(args: argparse.Namespace) -> int:
    """Move quarantined copies back and un-resolve their groups."""
    restored = 0
    errors = 0
    cleared = 0
    trashed = 0
    with ProgressDB(db_path=args.db) as db:
        groups = db.list_dedup_groups(include_resolved=True)
        groups = [g for g in groups if g.get("resolved_at")]
        if args.group is not None:
            groups = [g for g in groups if g["id"] == args.group]
            if not groups:
                print(f"No resolved group with id {args.group}.", file=sys.stderr)
                return 1
        if not groups:
            print("Nothing to undo — no resolved groups.", file=sys.stderr)
            return 0

        for group in groups:
            group_failed = False
            for member in group["members"]:
                if member.get("action") == ACTION_TRASH:
                    trashed += 1
                    continue
                if member.get("action") != ACTION_MOVE or not member.get("moved_to"):
                    continue
                source = Path(member["moved_to"])
                target = Path(member["file_path"])
                if not source.exists():
                    print(f"missing quarantined file: {source}", file=sys.stderr)
                    errors += 1
                    group_failed = True
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(target))
                except OSError as exc:
                    print(f"restore failed for {target}: {exc}", file=sys.stderr)
                    errors += 1
                    group_failed = True
                    continue
                restored += 1
            if group_failed:
                # Leave the DB record alone so the undo can be retried.
                continue
            cleared += db.undo_dedup_group(group["id"])

    print(f"Restored {restored} file(s); cleared {cleared} member record(s).")
    if trashed:
        print(
            f"{trashed} copy/copies were sent to the trash and must be restored "
            "from there manually.",
            file=sys.stderr,
        )
    return 1 if errors else 0
