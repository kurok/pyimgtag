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
import sys
from pathlib import Path
from typing import Any

from pyimgtag.db.dedup_db import ACTION_MOVE, ACTION_TAG, ACTION_TRASH
from pyimgtag.dedup_groups import (
    DEFAULT_THRESHOLD,
    action_never_completed,
    free_destination,
    is_photos_library_path,
    parse_prefer,
    pick_keeper,
    quarantine_destination,
    safe_move,
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
        # Dimensions are optional metadata for ranking; an undecodable or
        # truncated image still gets its phash and is reported without them.
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
    with ProgressDB(db_path=args.db, must_exist=args.db is not None) as db:
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


def _photos_writer() -> Any:
    """Return the Apple Photos keyword writer (seam for tests)."""
    from pyimgtag.applescript_writer import write_to_photos

    return write_to_photos


def _apply_photos_tag(file_path: str, write_back: bool) -> str | None:
    """Add the duplicate keyword in Apple Photos. Returns an error string or None."""
    if not write_back:
        return None
    if sys.platform != "darwin":
        return "write-back is macOS only"
    writer = _photos_writer()
    result: str | None = writer(file_path, [DUPLICATE_KEYWORD], None, mode="append")
    return result


def _move_loser(file_path: str, move_to: str) -> tuple[str | None, str | None]:
    """Move one loser into the quarantine dir. Returns ``(moved_to, error)``."""
    dest = free_destination(quarantine_destination(file_path, move_to))
    try:
        safe_move(file_path, dest)
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


def _say(*args: object, **kwargs: Any) -> None:
    """Print, but never let a console problem stop a resolve or undo half-way.

    A line the console cannot encode is printed with escapes instead, so the
    file name is still there to read.
    """
    stream = kwargs.get("file") or sys.stdout
    text = " ".join(str(a) for a in args)
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeError, LookupError):
        text = text.encode(encoding, "backslashreplace").decode(encoding, "replace")
    try:
        print(text, **kwargs)
    except (UnicodeError, OSError):
        pass


def _has_actions(db: ProgressDB, group_id: int) -> bool:
    group = db.get_dedup_group(group_id)
    return bool(group and any(m.get("action") for m in group["members"]))


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
            keeper = pick_keeper(group)
            _say(f"{prefix}group {group['id']} ({group['kind']}, {group['count']} copies)")
            if keeper is None:
                _say(
                    f"{prefix}  skipped: every copy was already moved or trashed by an "
                    "earlier run (see 'pyimgtag dedup undo')",
                    file=sys.stderr,
                )
                errors += 1
                continue
            _say(f"{prefix}  keep {keeper}")
            if acting and group.get("keep_path") != keeper:
                # Pin the keeper before acting, so a re-run keeps the same copy.
                db.set_dedup_keep_path(group["id"], keeper)
            group_acted = False
            for member in group["members"]:
                path = member["file_path"]
                if path == keeper:
                    if acting and action_never_completed(member):
                        db.clear_dedup_action(group["id"], path)  # stale record
                    continue
                done = member.get("action")
                if done in (ACTION_MOVE, ACTION_TRASH) and action_never_completed(member):
                    # Recorded just before a move/trash that was interrupted.
                    leftover = member.get("moved_to")
                    if leftover and Path(leftover).exists():
                        _say(f"{prefix}  leftover copy in quarantine: {leftover}")
                    if acting:
                        db.clear_dedup_action(group["id"], path)
                    done = None
                if done in (ACTION_MOVE, ACTION_TRASH):
                    # Acted on by an earlier run that stopped part-way.
                    _say(f"{prefix}  already {done}: {path}")
                    group_acted = True
                    continue
                if is_photos_library_path(path):
                    _say(f"{prefix}  tag  {path}  (Apple Photos original — never touched on disk)")
                    if acting:
                        err = _apply_photos_tag(path, bool(args.write_back))
                        if err:
                            _say(f"       write-back failed: {err}", file=sys.stderr)
                            errors += 1
                        db.record_dedup_action(group["id"], path, ACTION_TAG)
                        group_acted = True
                    continue
                if args.delete:
                    _say(f"{prefix}  trash {path}")
                    if acting:
                        # Record first: a crash after the trash then still knows.
                        db.record_dedup_action(group["id"], path, ACTION_TRASH)
                        err = _trash_loser(path)
                        if err:
                            db.clear_dedup_action(group["id"], path)
                            _say(f"       trash failed: {err}", file=sys.stderr)
                            errors += 1
                            continue
                        group_acted = True
                        reclaimed += int(member.get("file_size") or 0)
                    continue
                if args.move_to:
                    dest = free_destination(quarantine_destination(path, args.move_to))
                    _say(f"{prefix}  move {path} -> {dest}")
                    if acting:
                        # Record where it goes *before* moving it, so no file can
                        # reach the quarantine without undo knowing about it.
                        db.record_dedup_action(group["id"], path, ACTION_MOVE, str(dest))
                        moved_to, err = _move_loser(path, args.move_to)
                        if err or moved_to is None:
                            db.clear_dedup_action(group["id"], path)
                            _say(f"       move failed: {err}", file=sys.stderr)
                            errors += 1
                            continue
                        group_acted = True
                        reclaimed += int(member.get("file_size") or 0)
                    continue
                _say(f"{prefix}  keep-for-now {path}  (no action flag given)")
            # Only a loop that ran to the end resolves the group. One that was
            # interrupted leaves it unresolved, so the copies it did not reach
            # stay listed and plannable, while undo still sees what was moved.
            if acting and group_acted:
                db.mark_dedup_resolved(group["id"], keeper)
                acted_groups += 1
            elif acting and not _has_actions(db, group["id"]):
                # Nothing was done (every action failed): drop the pin, so the
                # next run ranks the copies afresh.
                db.set_dedup_keep_path(group["id"], None)

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
        # A group counts once anything was done to it, even if the resolve run
        # stopped before marking it resolved.
        groups = [
            g for g in groups if g.get("resolved_at") or any(m.get("action") for m in g["members"])
        ]
        if args.group is not None:
            groups = [g for g in groups if g["id"] == args.group]
            if not groups:
                print(f"No resolved or partly applied group with id {args.group}.", file=sys.stderr)
                return 1
        if not groups:
            print("Nothing to undo — no resolved or partly applied groups.", file=sys.stderr)
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
                if target.exists():
                    # The original is in place (an earlier undo put it back, or
                    # the move never finished). Never overwrite it; a leftover
                    # quarantine copy is reported, not used.
                    if source.exists():
                        _say(f"kept the original; leftover copy in quarantine: {source}")
                    continue
                if not source.exists() and not source.parent.is_dir():
                    # The quarantine is not there (an unplugged or unmounted
                    # drive): keep the record so a later undo can finish.
                    _say(
                        f"quarantine not reachable: {source} (connect the drive and "
                        "run undo again)",
                        file=sys.stderr,
                    )
                    errors += 1
                    group_failed = True
                    continue
                if not source.exists():
                    # The quarantine is there but neither copy exists: nothing
                    # can bring it back. Forget the record so the rest of the
                    # group can still be undone and rescanned.
                    _say(f"lost: neither {target} nor {source} exists", file=sys.stderr)
                    errors += 1
                    continue
                if not target.parent.is_dir():
                    # The library folder is gone (an unplugged library drive, an
                    # empty mount point): restoring would re-create it on the
                    # wrong disk. Keep the record for a later undo.
                    _say(
                        f"original location not reachable: {target.parent} (connect the "
                        "drive and run undo again)",
                        file=sys.stderr,
                    )
                    errors += 1
                    group_failed = True
                    continue
                try:
                    safe_move(source, target, create_parents=False)
                except OSError as exc:
                    _say(f"restore failed for {target}: {exc}", file=sys.stderr)
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
