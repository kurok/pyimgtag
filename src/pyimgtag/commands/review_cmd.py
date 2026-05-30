"""Handler for the ``review`` subcommand (local review UI)."""

from __future__ import annotations

import argparse
import sys


def cmd_review(args: argparse.Namespace) -> int:
    """Launch the local review UI server."""
    try:
        from pyimgtag.review_server import serve
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        serve(
            db_path=args.db,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
        )
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # Most commonly the port is already in use (EADDRINUSE); surface an
        # actionable message instead of an unhandled traceback.
        print(
            f"Error: could not start review server on {args.host}:{args.port}: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0
