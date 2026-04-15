"""CLI entry point for pyimgtag."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyimgtag",
        description="Tag macOS Photos library images using a local Gemma model.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_get_version()}",
    )
    parser.add_argument(
        "--model",
        default="gemma3:4b",
        help="Ollama model to use for image tagging (default: gemma3:4b)",
    )
    parser.add_argument(
        "--library",
        help="Path to Photos library (default: system default)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what tags would be applied without writing",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of images to process per batch (default: 10)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of concurrent inference requests (default: 1)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("tag", help="Tag untagged images in the Photos library")
    subparsers.add_parser("search", help="Search images by tags")
    subparsers.add_parser("status", help="Show tagging status and statistics")
    subparsers.add_parser("export", help="Export tag database")

    return parser


def _get_version() -> str:
    from pyimgtag import __version__

    return __version__


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    # TODO: dispatch to subcommand handlers
    print(f"Command '{args.command}' is not yet implemented.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
