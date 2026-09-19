"""Handlers for the ``index`` and ``search`` subcommands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from pyimgtag.progress_db import ProgressDB


def _collect_paths(args: argparse.Namespace) -> list[Path]:
    """Resolve --input-dir / --photos-library into a sorted file list."""
    from pyimgtag.scanner import scan_directory, scan_photos_library

    extensions = {e.strip().lstrip(".").lower() for e in args.extensions.split(",") if e.strip()}
    if args.photos_library:
        return scan_photos_library(args.photos_library, extensions)
    return scan_directory(args.input_dir, extensions)


def _load_embedder(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    """Build the embedder, turning setup failures into a friendly message."""
    from pyimgtag.search.embedder import load_embedder
    from pyimgtag.search.model_cache import ModelDownloadError

    model_dir = Path(args.model_dir) if getattr(args, "model_dir", None) else None
    try:
        return load_embedder(model_dir)
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None
    except ModelDownloadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None


def cmd_index(args: argparse.Namespace) -> int:
    """Execute the index subcommand."""
    from pyimgtag.search.indexer import build_index

    try:
        paths = _collect_paths(args)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not paths:
        print("No images found to index.", file=sys.stderr)
        return 0

    embedder = _load_embedder(args)
    if embedder is None:
        return 1

    def _progress(done: int, total: int, path: Path) -> None:
        if args.verbose:
            print(f"[{done}/{total}] {path.name}", file=sys.stderr)

    with ProgressDB(db_path=args.db) as db:
        if args.rebuild:
            removed = db.clear_embeddings()
            if removed:
                print(f"Cleared {removed} existing embedding(s).", file=sys.stderr)
        stats = build_index(
            paths,
            db,
            embedder,
            rebuild=args.rebuild,
            limit=args.limit,
            on_progress=_progress,
        )

    for path, error in stats.errors[:10]:
        print(f"  {path}: {error}", file=sys.stderr)
    if len(stats.errors) > 10:
        print(f"  ... and {len(stats.errors) - 10} more", file=sys.stderr)
    print(stats.summary(), file=sys.stderr)
    # A run where every single file failed is a failure, not a quiet success.
    return 1 if stats.failed and not stats.embedded else 0


def _structured_filter(args: argparse.Namespace, db: ProgressDB) -> set[str] | None:
    """Resolve the structured filters to a path set, or None when none were given.

    This is the filter-then-rank half: the same query machinery ``pyimgtag
    query`` uses picks the candidates, and semantic similarity only orders what
    is left. Returning ``None`` means "no structured filter", which is different
    from an empty set — that means "filtered down to nothing".
    """
    filters = {
        "tag": args.tag,
        "scene_category": args.scene_category,
        "cleanup_class": args.cleanup,
        "city": args.city,
        "country": args.country,
        "date_prefix": args.year or args.month,
        "min_judge_score": args.min_score,
    }
    if not any(v is not None for v in filters.values()) and not args.person:
        return None

    rows = db.query_images(**filters)  # type: ignore[arg-type]
    paths = {r["file_path"] for r in rows}
    if args.person:
        paths &= db.paths_for_person_label(args.person)
    return paths


def _example_vector(args: argparse.Namespace, db: ProgressDB) -> tuple[Any, set[str]]:
    """Resolve ``--similar-to`` to a query vector and the paths to exclude.

    An already-indexed photo is answered from its stored row, which is the
    whole point of the fast path: no model is loaded, so query-by-example on a
    library photo costs a single row read. Anything else is embedded on the
    fly, which does need the model.

    Returns ``(vector, exclude)``; *vector* is None when the file could not be
    resolved, and the caller has already printed why.
    """
    raw = Path(args.similar_to).expanduser()
    # Both spellings are tried because the index stores whatever `index` was
    # given, and a user typing a relative path here should still hit the row.
    candidates = [str(raw)]
    resolved = raw.resolve()
    if str(resolved) not in candidates:
        candidates.append(str(resolved))

    for candidate in candidates:
        stored = db.get_embedding(candidate)
        if stored is not None:
            return stored, set(candidates)

    if not raw.is_file():
        print(
            f"Error: {raw} is neither an indexed photo nor a readable image file.",
            file=sys.stderr,
        )
        return None, set()

    embedder = _load_embedder(args)
    if embedder is None:
        return None, set()
    try:
        return embedder.embed_image(resolved), set(candidates)
    except Exception as exc:  # noqa: BLE001 — the path is user input
        print(f"Error: could not embed {raw}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None, set()


def cmd_search(args: argparse.Namespace) -> int:
    """Execute the search subcommand."""
    import json as _json

    with ProgressDB(db_path=args.db) as db:
        stats = db.embedding_stats()
        if not stats["count"]:
            print(
                "No embeddings in the database. Build the index first:\n"
                "    pyimgtag index --input-dir <DIR>",
                file=sys.stderr,
            )
            return 1

        exclude: set[str] = set()
        if args.similar_to:
            vector, exclude = _example_vector(args, db)
            if vector is None:
                return 1
        else:
            embedder = _load_embedder(args)
            if embedder is None:
                return 1
            if stats["model"] and stats["model"] != embedder.model_id:
                print(
                    f"Warning: the index was built with {stats['model']!r} but this "
                    f"install uses {embedder.model_id!r}. Re-run 'pyimgtag index --rebuild'.",
                    file=sys.stderr,
                )
            vector = embedder.embed_text(args.query)

        # One ranking path for both modes: only the vector differs, so the
        # structured filters cannot drift between text and example search.
        allowed = _structured_filter(args, db)
        hits = db.search_similar(
            vector,
            limit=args.top,
            allowed_paths=allowed,
            min_score=args.min_similarity,
            exclude_paths=exclude,
        )

    if not hits:
        print("No images matched.", file=sys.stderr)
        return 0

    if args.format == "paths":
        for path, _ in hits:
            print(path)
    elif args.format == "json":
        print(_json.dumps([{"file_path": p, "score": round(s, 6)} for p, s in hits], indent=2))
    else:
        width = 64
        print(f"{'SCORE':<7}  {'PATH':<{width}}")
        print("-" * (7 + 2 + width))
        for path, score in hits:
            shown = path[-width:] if len(path) > width else path
            print(f"{score:<7.4f}  {shown:<{width}}")
        print(f"\n{len(hits)} image(s) found.", file=sys.stderr)
    return 0
