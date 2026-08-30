"""Handler for the ``insights`` subcommand."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pyimgtag.insights_report import render_html, render_terminal
from pyimgtag.progress_db import ProgressDB


def _thumb_loader(path: str, size: int) -> bytes | None:
    """Reuse the review server's cached thumbnail pipeline (PIL + sips fallback)."""
    try:
        from pyimgtag.webapp.routes_review import _make_thumbnail
    except ImportError:  # pragma: no cover — webapp package is always shipped
        return None
    return _make_thumbnail(path, size)


def cmd_insights(args: argparse.Namespace) -> int:
    """Execute the insights subcommand."""
    fmt: str = args.format
    output: str | None = args.output
    if output and fmt == "terminal" and output.lower().endswith((".html", ".htm")):
        fmt = "html"
    elif output and fmt == "terminal" and output.lower().endswith(".json"):
        fmt = "json"

    with ProgressDB(db_path=args.db) as db:
        doc = db.get_insights(top_n=args.top)

    if fmt == "json":
        text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    elif fmt == "html":
        text = render_html(
            doc,
            thumb_loader=None if args.no_thumbnails else _thumb_loader,
            max_thumbs=args.max_thumbnails,
        )
    else:
        text = render_terminal(doc)

    if output:
        Path(output).write_text(text, encoding="utf-8")
        print(f"Wrote {fmt} report to {output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    if doc.get("empty"):
        print("Database is empty — nothing tagged yet.", file=sys.stderr)
    return 0
