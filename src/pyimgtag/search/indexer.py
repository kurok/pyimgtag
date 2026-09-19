"""Incremental embedding of a photo library."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from pyimgtag.progress_db import ProgressDB
    from pyimgtag.search.embedder import Embedder


@dataclass
class IndexStats:
    """What one ``pyimgtag index`` run did."""

    total: int = 0
    embedded: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)

    def summary(self) -> str:
        """One line for the terminal."""
        parts = [f"{self.embedded} embedded", f"{self.skipped} unchanged"]
        if self.failed:
            parts.append(f"{self.failed} failed")
        return ", ".join(parts)


def build_index(
    paths: list[Path],
    db: ProgressDB,
    embedder: Embedder,
    *,
    rebuild: bool = False,
    limit: int | None = None,
    on_progress: Callable[[int, int, Path], None] | None = None,
) -> IndexStats:
    """Embed *paths* into *db*, skipping files that are already current.

    Args:
        paths: Image files to consider.
        db: Open progress database; embeddings go in its ``image_embeddings``
            table.
        embedder: Anything satisfying :class:`~pyimgtag.search.embedder.Embedder`.
        rebuild: Re-embed everything, ignoring the size+mtime check.
        limit: Stop after this many *newly embedded* files.
        on_progress: Called with ``(done, total, path)`` before each embed.

    Returns:
        An :class:`IndexStats` describing the run.

    A file that cannot be read or embedded is recorded in ``stats.errors`` and
    the run continues: one unreadable photo in a library of 40,000 is not a
    reason to abandon the other 39,999.
    """
    stats = IndexStats(total=len(paths))
    for index, path in enumerate(paths, start=1):
        if limit is not None and stats.embedded >= limit:
            # Everything after the limit is untouched, not skipped-as-current.
            stats.total = index - 1
            break
        if not rebuild and not db.needs_embedding(path, embedder.model_id):
            stats.skipped += 1
            continue
        if on_progress is not None:
            on_progress(index, len(paths), path)
        try:
            vector = embedder.embed_image(path)
            db.upsert_embedding(path, embedder.model_id, vector)
            stats.embedded += 1
        except Exception as exc:  # noqa: BLE001 — one bad file must not end the run
            stats.failed += 1
            stats.errors.append((path, f"{type(exc).__name__}: {exc}"))
    return stats
