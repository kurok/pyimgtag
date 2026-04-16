"""Handlers for the ``tags`` subcommand group."""

from __future__ import annotations

import argparse
import sys

from pyimgtag.progress_db import ProgressDB


def cmd_tags(args: argparse.Namespace) -> int:
    """Dispatch tags subcommands."""
    if not hasattr(args, "tags_action") or args.tags_action is None:
        print("Usage: pyimgtag tags <list|rename|delete|merge>", file=sys.stderr)
        return 1
    if args.tags_action == "list":
        return _handle_tags_list(args)
    if args.tags_action == "rename":
        return _handle_tags_rename(args)
    if args.tags_action == "delete":
        return _handle_tags_delete(args)
    if args.tags_action == "merge":
        return _handle_tags_merge(args)
    print(f"Unknown tags action: {args.tags_action}", file=sys.stderr)
    return 1


def _handle_tags_list(args: argparse.Namespace) -> int:
    """List all tags with image counts."""
    with ProgressDB(db_path=args.db) as db:
        counts = db.get_tag_counts()

    if not counts:
        print("No tags found.", file=sys.stderr)
        return 0

    col_tag = max(len(t) for t, _ in counts)
    col_tag = max(col_tag, 4)
    print(f"{'TAG':<{col_tag}}  COUNT")
    print("-" * (col_tag + 8))
    for tag, count in counts:
        print(f"{tag:<{col_tag}}  {count}")
    print(f"\n{len(counts)} unique tag(s).")
    return 0


def _handle_tags_rename(args: argparse.Namespace) -> int:
    """Rename a tag across all images."""
    with ProgressDB(db_path=args.db) as db:
        if args.dry_run:
            tag_counts = db.get_tag_counts()
            count = next((c for t, c in tag_counts if t == args.old_tag.lower()), 0)
            print(
                f"[dry-run] Would rename '{args.old_tag}' → '{args.new_tag}' in {count} image(s).",
                file=sys.stderr,
            )
        else:
            count = db.rename_tag(args.old_tag, args.new_tag)
            print(
                f"Renamed '{args.old_tag}' → '{args.new_tag}' in {count} image(s).",
                file=sys.stderr,
            )
    return 0


def _handle_tags_delete(args: argparse.Namespace) -> int:
    """Delete a tag from all images."""
    with ProgressDB(db_path=args.db) as db:
        if args.dry_run:
            tag_counts = db.get_tag_counts()
            count = next((c for t, c in tag_counts if t == args.tag.lower()), 0)
            print(f"[dry-run] Would delete '{args.tag}' from {count} image(s).", file=sys.stderr)
        else:
            count = db.delete_tag(args.tag)
            print(f"Deleted '{args.tag}' from {count} image(s).", file=sys.stderr)
    return 0


def _handle_tags_merge(args: argparse.Namespace) -> int:
    """Merge source tag into target tag across all images."""
    with ProgressDB(db_path=args.db) as db:
        if args.dry_run:
            tag_counts = db.get_tag_counts()
            count = next((c for t, c in tag_counts if t == args.source_tag.lower()), 0)
            print(
                f"[dry-run] Would merge '{args.source_tag}' → '{args.target_tag}' "
                f"in {count} image(s).",
                file=sys.stderr,
            )
        else:
            count = db.merge_tags(args.source_tag, args.target_tag)
            print(
                f"Merged '{args.source_tag}' → '{args.target_tag}' in {count} image(s).",
                file=sys.stderr,
            )
    return 0
