"""Dashboard bootstrap helper shared by all long-running CLI commands."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from pyimgtag import run_registry
from pyimgtag.run_session import RunSession
from pyimgtag.webapp.config import web_enabled

if TYPE_CHECKING:
    from pyimgtag.webapp.server_thread import DashboardServer


def start_dashboard_for(
    args: argparse.Namespace,
    command: str,
) -> tuple[RunSession | None, "DashboardServer | None"]:
    """Start the dashboard for ``command`` if enabled and return (session, dashboard).

    Honours ``--no-web`` / ``PYIMGTAG_NO_WEB=1``. Gracefully falls back to
    terminal-only mode (returning ``(None, None)``) if fastapi/uvicorn are
    missing.
    """
    if not web_enabled(args):
        return None, None

    session = RunSession(command=command)
    run_registry.set_current(session)

    try:
        from pyimgtag.webapp.server_thread import DashboardServer
        from pyimgtag.webapp.unified_app import create_unified_app

        # Thread args.db all the way through so the in-process dashboard
        # reads from the exact same SQLite the CLI is writing to. Without
        # this, ``create_unified_app()`` opens the default
        # ``~/.cache/pyimgtag/progress.db`` while the worker may be using
        # a user-supplied path — the user then sees "0 scored" on the
        # Judge page even though the CLI is happily writing rows.
        dashboard = DashboardServer(
            create_unified_app(db_path=getattr(args, "db", None)),
            host=args.web_host,
            port=args.web_port,
        )
    except ImportError as exc:
        print(f"Warning: dashboard disabled ({exc})", file=sys.stderr)
        run_registry.set_current(None)
        return None, None

    ready = dashboard.start()
    session.web_url = dashboard.url
    if ready:
        print(f"Dashboard: {dashboard.url}", flush=True)
    else:
        print(
            f"Dashboard: {dashboard.url} (not yet ready; retrying in background)",
            flush=True,
        )
    if not getattr(args, "no_browser", False):
        import webbrowser

        try:
            webbrowser.open(dashboard.url)
        except Exception as exc:  # noqa: BLE001 — best effort
            print(f"Warning: could not open browser ({exc})", file=sys.stderr)
    return session, dashboard
