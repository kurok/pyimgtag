"""Handler for the ``preflight`` subcommand."""

from __future__ import annotations

import argparse

from pyimgtag.preflight import OPTIONAL_CHECKS, SKIPPED_PREFIX, run_preflight


def cmd_preflight(args: argparse.Namespace) -> int:
    """Run preflight checks, print results, and return exit code.

    Only a failed *required* check (Ollama, the model, the source path) makes
    the exit code 1. Optional capabilities print ``[WARN]``; a check skipped
    because an earlier one failed prints ``[SKIP]``.
    """
    source_path: str | None = None
    source_type = "directory"
    if args.input_dir:
        source_path = args.input_dir
        source_type = "directory"
    elif args.photos_library:
        source_path = args.photos_library
        source_type = "photos_library"

    results = run_preflight(args.ollama_url, args.model, source_path, source_type)

    print("Preflight checks:")
    required_failed = False
    for name, passed, msg in results:
        if passed:
            label = "[PASS]"
        elif msg.startswith(SKIPPED_PREFIX):
            label = "[SKIP]"
            msg = msg.removeprefix(SKIPPED_PREFIX)
            # A required check that could not run still means "not ready",
            # whatever made it skip.
            required_failed |= name not in OPTIONAL_CHECKS
        elif name in OPTIONAL_CHECKS:
            label = "[WARN]"
        else:
            label = "[FAIL]"
            required_failed = True
        print(f"  {label} {msg}")

    return 1 if required_failed else 0
