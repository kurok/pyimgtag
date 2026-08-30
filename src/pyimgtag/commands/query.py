"""Handler for the ``query`` subcommand."""

from __future__ import annotations

import argparse
import os
import sys

from pyimgtag.progress_db import ProgressDB


def _rollup_tags(args: argparse.Namespace) -> list[str] | None:
    """Expand ``--tag`` into itself + vocabulary descendants for ``--include-children``.

    Returns ``None`` when roll-up is not requested. Raises
    :class:`pyimgtag.vocabulary.VocabularyError` on a bad vocabulary file.
    """
    if not getattr(args, "include_children", False):
        return None
    from pyimgtag.vocabulary import load_vocabulary

    path = getattr(args, "vocabulary", None) or os.environ.get("PYIMGTAG_VOCABULARY") or ""
    vocabulary = load_vocabulary(str(path))  # path presence is enforced by the parser
    return vocabulary.descendants(args.tag)


def cmd_query(args: argparse.Namespace) -> int:
    """Execute the query subcommand."""
    import json as _json

    from pyimgtag.vocabulary import VocabularyError

    try:
        tags_any = _rollup_tags(args)
    except VocabularyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    with ProgressDB(db_path=args.db) as db:
        has_text: bool | None = None
        if args.has_text:
            has_text = True
        elif args.no_text:
            has_text = False

        results = db.query_images(
            # Roll-up switches from substring to exact-set matching.
            tag=None if tags_any is not None else args.tag,
            has_text=has_text,
            cleanup_class=args.cleanup,
            scene_category=args.scene_category,
            city=args.city,
            country=args.country,
            status=args.status,
            limit=args.limit,
            tags_any=tags_any,
        )

    if tags_any is not None:
        print(f"Matching tags: {', '.join(tags_any)}", file=sys.stderr)

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
