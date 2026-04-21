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
