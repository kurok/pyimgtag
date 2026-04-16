"""Handlers for the ``status``, ``reprocess``, and ``cleanup`` subcommands."""

from __future__ import annotations

import argparse
import json
import sys

from pyimgtag.progress_db import ProgressDB


def cmd_status(args: argparse.Namespace) -> int:
    """Show progress stats from the DB."""
    db = ProgressDB(db_path=args.db)
    try:
        stats = db.get_stats()
    finally:
        db.close()

    total = stats["total"]
    ok = stats["ok"]
    error = stats["error"]
    pending = total - ok - error

    pct = f"{ok * 100 // total}%" if total > 0 else "0%"
    print(f"Progress: {ok} / {total} ({pct})")
    print(f"  ok:      {ok}")
    print(f"  error:   {error}")
    print(f"  pending: {pending}")
    return 0


def cmd_reprocess(args: argparse.Namespace) -> int:
    """Reset DB entries so photos get re-tagged."""
    db = ProgressDB(db_path=args.db)
    try:
        if args.status:
            count = db.reset_by_status(args.status)
        else:
            count = db.reset_all()
    finally:
        db.close()

    print(f"Reset {count} entries for reprocessing.", file=sys.stderr)
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    """List photos flagged for cleanup and exit."""
    db = ProgressDB(db_path=args.db)
    try:
        candidates = db.get_cleanup_candidates(include_review=args.include_review)
    finally:
        db.close()

    if not candidates:
        print("No cleanup candidates found.", file=sys.stderr)
        return 0

    label = "delete + review" if args.include_review else "delete"
    print(f"Cleanup candidates ({label}): {len(candidates)}")
    print()
    for item in candidates:
        tags = ", ".join(json.loads(item["tags"])) if item.get("tags") else "(none)"
        loc = item.get("nearest_city") or ""
        if item.get("nearest_country") and loc:
            loc = f"{loc}, {item['nearest_country']}"
        parts = [f"[{item['cleanup_class']}]", item["file_path"]]
        if loc:
            parts.append(f"| {loc}")
        if item.get("image_date"):
            parts.append(f"| {item['image_date'][:10]}")
        parts.append(f"| tags: {tags}")
        print("  " + "  ".join(parts))
    return 0
