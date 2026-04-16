"""Handler for the ``query`` subcommand."""

from __future__ import annotations

import argparse
import sys

from pyimgtag.progress_db import ProgressDB


def cmd_query(args: argparse.Namespace) -> int:
    """Execute the query subcommand."""
    import json as _json

    db = ProgressDB(db_path=args.db)
    try:
        has_text: bool | None = None
        if args.has_text:
            has_text = True
        elif args.no_text:
            has_text = False

        results = db.query_images(
            tag=args.tag,
            has_text=has_text,
            cleanup_class=args.cleanup,
            scene_category=args.scene_category,
            city=args.city,
            country=args.country,
            status=args.status,
            limit=args.limit,
        )
    finally:
        db.close()

    if not results:
        print("No images matched the given filters.", file=sys.stderr)
        return 0

    fmt = args.format
    if fmt == "paths":
        for r in results:
            print(r["file_path"])
    elif fmt == "json":
        print(_json.dumps(results, indent=2))
    else:
        # table format
        col_path = 50
        col_tags = 40
        col_cat = 15
        col_clean = 8
        header = (
            f"{'PATH':<{col_path}}  {'TAGS':<{col_tags}}  "
            f"{'CATEGORY':<{col_cat}}  {'CLEANUP':<{col_clean}}"
        )
        print(header)
        print("-" * len(header))
        for r in results:
            path_str = (
                r["file_path"][-col_path:] if len(r["file_path"]) > col_path else r["file_path"]
            )
            tags_str = ", ".join(r["tags_list"])
            tags_str = tags_str[:col_tags] if len(tags_str) > col_tags else tags_str
            cat_str = (r["scene_category"] or "")[:col_cat]
            clean_str = (r["cleanup_class"] or "")[:col_clean]
            print(
                f"{path_str:<{col_path}}  {tags_str:<{col_tags}}  "
                f"{cat_str:<{col_cat}}  {clean_str:<{col_clean}}"
            )
        print(f"\n{len(results)} image(s) found.", file=sys.stderr)
    return 0
