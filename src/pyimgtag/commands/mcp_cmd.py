"""Handler for the ``mcp`` subcommand."""

from __future__ import annotations

import argparse
import sys


def cmd_mcp(args: argparse.Namespace) -> int:
    """Run the stdio MCP server, or print a friendly install hint."""
    from pyimgtag.mcp_server import serve_stdio, writes_enabled_from_env

    enable_writes = bool(getattr(args, "enable_writes", False)) or writes_enabled_from_env()
    try:
        serve_stdio(
            db_path=getattr(args, "db", None),
            enable_writes=enable_writes,
            export_root=getattr(args, "export_root", None),
        )
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive stop
        return 0
    return 0
