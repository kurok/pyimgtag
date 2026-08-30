"""Handlers for the ``prompt`` subcommand group."""

from __future__ import annotations

import argparse
import sys


def cmd_prompt(args: argparse.Namespace) -> int:
    """Dispatch prompt subcommands."""
    action = getattr(args, "prompt_action", None)
    if action is None:
        print("Usage: pyimgtag prompt <show>", file=sys.stderr)
        return 1
    if action == "show":
        return _handle_prompt_show(args)
    print(f"Unknown prompt action: {action}", file=sys.stderr)
    return 1


def _handle_prompt_show(args: argparse.Namespace) -> int:
    """Print the default template (or a rendered example) as a starting point."""
    from pyimgtag.prompt_template import DEFAULT_TEMPLATE, PromptBuilder

    if getattr(args, "rendered", False):
        sys.stdout.write(PromptBuilder().render(None))
        if not sys.stdout.isatty():
            sys.stdout.write("\n")
        return 0
    sys.stdout.write(DEFAULT_TEMPLATE)
    if not DEFAULT_TEMPLATE.endswith("\n"):
        sys.stdout.write("\n")
    return 0
