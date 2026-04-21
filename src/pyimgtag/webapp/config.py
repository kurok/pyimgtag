"""Configuration resolution for the webapp dashboard (framework-agnostic)."""

from __future__ import annotations

import argparse
import os


def web_enabled(args: argparse.Namespace) -> bool:
    """Resolve whether the dashboard should start for this invocation.

    Priority: ``--no-web`` beats everything, then ``--web`` overrides the env
    var, otherwise ``PYIMGTAG_NO_WEB`` (truthy values ``1``/``true``/``yes``)
    disables the dashboard. Default is enabled.
    """
    if getattr(args, "no_web", False):
        return False
    if getattr(args, "web", False):
        return True
    if os.environ.get("PYIMGTAG_NO_WEB", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return True


def add_web_flags(parser: argparse.ArgumentParser) -> None:
    """Register the five standard dashboard flags on ``parser``.

    Flags: ``--web``, ``--no-web``, ``--web-host``, ``--web-port``,
    ``--no-browser``. Defaults match :func:`web_enabled` semantics —
    dashboard on by default unless ``--no-web`` or ``PYIMGTAG_NO_WEB=1``.
    """
    parser.add_argument(
        "--web",
        action="store_true",
        help="Force-enable the live dashboard (overrides PYIMGTAG_NO_WEB)",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Disable the live dashboard (terminal-only mode)",
    )
    parser.add_argument(
        "--web-host",
        default="127.0.0.1",
        help="Dashboard bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8770,
        help="Dashboard bind port (default: 8770)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the dashboard in a browser",
    )
