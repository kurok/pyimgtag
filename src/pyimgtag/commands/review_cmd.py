"""Handler for the ``review`` subcommand (local review UI)."""

from __future__ import annotations

import argparse

from pyimgtag.review_server import serve


def cmd_review(args: argparse.Namespace) -> int:
    """Launch the local review UI server."""
    return serve(
        db_path=args.db,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )
