"""Semantic-search domain: per-image CLIP embeddings and brute-force retrieval.

Owns queries against the ``image_embeddings`` table (schema and migrations live
in :class:`pyimgtag.db.progress_db.ProgressDB`).

Embeddings are stored as raw little-endian float32 blobs — 512 floats is 2 KB
per photo, so a 100k-photo library costs about 200 MB. Retrieval scans them
with numpy rather than taking a vector-database dependency; see
:func:`search_similar` for the measured cost.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

#: Blobs are written little-endian regardless of host byte order, so a database
#: copied between machines keeps working.
_DTYPE = "<f4"


class SearchDB:
    """Embedding storage and cosine retrieval over a shared SQLite connection.

    The connection (including schema and migrations) is owned by
    :class:`pyimgtag.db.progress_db.ProgressDB`; this class only issues domain
    queries against it.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the domain helper to the facade's open connection."""
        self._conn = conn

    @staticmethod
    def _to_blob(embedding: np.ndarray) -> bytes:
        """Pack a 1-D float vector into a little-endian float32 blob."""
        return embedding.astype(_DTYPE, copy=False).tobytes()

    @staticmethod
    def _from_blob(blob: bytes) -> np.ndarray:
        """Unpack a blob written by :meth:`_to_blob`."""
        import numpy as np

        return np.frombuffer(blob, dtype=_DTYPE)

    def needs_embedding(self, file_path: Path, model: str) -> bool:
        """Return True when *file_path* has no current embedding for *model*.

        Uses the same size+mtime contract as
        :meth:`pyimgtag.db.image_db.ImageDB.is_processed`, so re-running
        ``index`` over an unchanged library does no model work. A row embedded
        by a different model is stale by definition: the vectors are not
        comparable across models.
        """
        row = self._conn.execute(
            "SELECT file_size, file_mtime, model FROM image_embeddings WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
        if row is None or row[2] != model:
            return True
        try:
            stat = file_path.stat()
        except OSError:
            # The file is gone or unreadable; nothing to re-embed.
            return False
        return not (row[0] == stat.st_size and row[1] == stat.st_mtime)

    def upsert_embedding(self, file_path: Path, model: str, embedding: np.ndarray) -> None:
        """Store (or replace) the embedding for one image."""
        try:
            stat = file_path.stat()
            size: int | None = stat.st_size
            mtime: float | None = stat.st_mtime
        except OSError:
            size = mtime = None
        self._conn.execute(
            """INSERT INTO image_embeddings
                   (file_path, model, dim, embedding, file_size, file_mtime, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(file_path) DO UPDATE SET
                   model=excluded.model, dim=excluded.dim, embedding=excluded.embedding,
                   file_size=excluded.file_size, file_mtime=excluded.file_mtime,
                   indexed_at=excluded.indexed_at""",
            (
                str(file_path),
                model,
                int(embedding.shape[-1]),
                self._to_blob(embedding),
                size,
                mtime,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        self._conn.commit()

    def get_embedding(self, file_path: Path | str) -> np.ndarray | None:
        """The stored vector for *file_path*, or None when it is not indexed.

        This is the fast path for query-by-example: an already-indexed photo
        needs no model at all, so ``--similar-to`` on a library photo costs one
        row read rather than an 89 MB session.
        """
        row = self._conn.execute(
            "SELECT embedding FROM image_embeddings WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
        return self._from_blob(row[0]) if row else None

    def clear_embeddings(self, model: str | None = None) -> int:
        """Delete every embedding (or every one for *model*); returns the row count."""
        if model is None:
            cur = self._conn.execute("DELETE FROM image_embeddings")
        else:
            cur = self._conn.execute("DELETE FROM image_embeddings WHERE model = ?", (model,))
        self._conn.commit()
        return int(cur.rowcount or 0)

    def embedding_stats(self) -> dict[str, int | str | None]:
        """Row count, distinct models, and the newest indexed_at."""
        row = self._conn.execute(
            "SELECT COUNT(*), MAX(indexed_at), COUNT(DISTINCT model) FROM image_embeddings"
        ).fetchone()
        model_row = self._conn.execute(
            "SELECT model FROM image_embeddings GROUP BY model ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
        return {
            "count": int(row[0] or 0),
            "last_indexed_at": row[1],
            "models": int(row[2] or 0),
            "model": model_row[0] if model_row else None,
        }

    def search_similar(
        self,
        query: np.ndarray,
        limit: int = 20,
        allowed_paths: set[str] | None = None,
        min_score: float | None = None,
        exclude_paths: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Return the ``(file_path, cosine_score)`` pairs closest to *query*.

        *allowed_paths* is the filter-then-rank hook: structured filters
        (``--person``, ``--date-from``, ``--tag``, …) are resolved to a path set
        by the existing query machinery and semantic ranking is applied inside
        it, so the two compose without a second ranking pass.

        *exclude_paths* drops rows before ranking. Query-by-example uses it for
        the example photo, which would otherwise always be its own best match
        at a cosine of 1.0 and waste a slot.

        Brute force on purpose. 100k photos is a 100k x 512 float32 matrix —
        200 MB and a single numpy dot product — which is well under the 1 s
        budget and costs no extra dependency. Revisit only if profiling says so.
        """
        import numpy as np

        if allowed_paths is not None and not allowed_paths:
            return []

        rows = self._conn.execute("SELECT file_path, embedding FROM image_embeddings").fetchall()
        if allowed_paths is not None:
            rows = [r for r in rows if r[0] in allowed_paths]
        if exclude_paths:
            rows = [r for r in rows if r[0] not in exclude_paths]
        if not rows:
            return []

        matrix = np.stack([self._from_blob(r[1]) for r in rows])
        # Stored vectors are unit-normalised at write time, so a dot product is
        # the cosine. Normalise the query defensively: a caller passing a raw
        # model output should still get cosines rather than dot products.
        norm = float(np.linalg.norm(query))
        q = query.astype("float32") / norm if norm else query.astype("float32")
        scores = matrix @ q

        order = np.argsort(-scores)[: max(0, limit)] if limit else np.argsort(-scores)
        out: list[tuple[str, float]] = []
        for i in order:
            score = float(scores[i])
            if min_score is not None and score < min_score:
                break
            out.append((rows[int(i)][0], score))
        return out
