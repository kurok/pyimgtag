"""Handler for the ``preflight`` subcommand."""

from __future__ import annotations

import argparse

from pyimgtag.preflight import run_preflight


def cmd_preflight(args: argparse.Namespace) -> int:
    """Run preflight checks, print results, and return exit code."""
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
    all_passed = True
    for name, passed, msg in results:
        label = "[PASS]" if passed else "[FAIL]"
        print(f"  {label} {msg}")
        if not passed:
            all_passed = False

    return 0 if all_passed else 1
