"""Image-progress domain: queries against the ``processed_images`` table.

Covers incremental-run bookkeeping (size/mtime freshness), tag management,
review-UI pagination, cleanup candidates, and drift-cleanup helpers. Schema
and migrations live in :class:`pyimgtag.db.progress_db.ProgressDB`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pyimgtag.models import ImageResult

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

# Default batch size for iter_image_paths; 1 000 rows per round-trip
# keeps memory usage low on a 22 k-photo library.
_DEFAULT_PATH_BATCH_SIZE: int = 1000


class ImageDB:
    """Image-progress queries over a shared SQLite connection.

    The connection (including schema and migrations) is owned by
    :class:`pyimgtag.db.progress_db.ProgressDB`; this class only issues
    domain queries against it.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the domain helper to the facade's open connection."""
        self._conn = conn

    def is_processed(self, file_path: Path) -> bool:
        """Return True if the file was already successfully processed and hasn't changed.

        Rows with status != 'ok' (e.g. transient Ollama failures) are treated as
        not processed so they will be retried on the next run.
        """
        row = self._conn.execute(
            "SELECT file_size, file_mtime, status FROM processed_images WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
        if row is None:
            return False
        if row[2] != "ok":
            return False
        try:
            stat = file_path.stat()
        except OSError:
            return False
        return row[0] == stat.st_size and row[1] == stat.st_mtime

    def mark_done(self, file_path: Path, result: ImageResult) -> None:
        """Record a processed image result.

        If ``file_path.stat()`` fails (permission error, vanished file), the
        stored size/mtime fall back to ``0``/``0.0``. Such a row can never match
        a real file in :meth:`is_processed`, so the file would be re-processed on
        every subsequent run; the failure is logged at debug level.
        """
        try:
            stat = file_path.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError as e:
            logger.debug("stat() failed for %s; recording size/mtime as 0: %s", file_path, e)
            size = 0
            mtime = 0.0
        self._conn.execute(
            """
            INSERT OR REPLACE INTO processed_images
                (file_path, file_size, file_mtime, tags, scene_summary,
                 processed_at, status, error_message,
                 scene_category, emotional_tone, cleanup_class, has_text,
                 text_summary, event_hint, significance,
                 nearest_city, nearest_region, nearest_country, image_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(file_path),
                size,
                mtime,
                json.dumps(result.tags),
                result.scene_summary,
                datetime.now(timezone.utc).isoformat(),
                result.processing_status,
                result.error_message,
                result.scene_category,
                result.emotional_tone,
                result.cleanup_class,
                1 if result.has_text else 0,
                result.text_summary,
                result.event_hint,
                result.significance,
                result.nearest_city,
                result.nearest_region,
                result.nearest_country,
                result.image_date,
            ),
        )
        self._conn.commit()

    def get_stats(self) -> dict:
        """Return counts of processed images by status."""
        total = self._conn.execute("SELECT COUNT(*) FROM processed_images").fetchone()[0]
        ok = self._conn.execute(
            "SELECT COUNT(*) FROM processed_images WHERE status = 'ok'"
        ).fetchone()[0]
        error = self._conn.execute(
            "SELECT COUNT(*) FROM processed_images WHERE status = 'error'"
        ).fetchone()[0]
        return {"total": total, "ok": ok, "error": error}

    def reset_all(self) -> int:
        """Delete all rows from the processed_images table. Returns count deleted."""
        cur = self._conn.execute("SELECT COUNT(*) FROM processed_images")
        count = cur.fetchone()[0]
        self._conn.execute("DELETE FROM processed_images")
        self._conn.commit()
        return count

    def reset_by_status(self, status: str) -> int:
        """Delete rows with the given status (e.g. 'error'). Returns count deleted."""
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM processed_images WHERE status = ?", (status,)
        )
        count = cur.fetchone()[0]
        self._conn.execute("DELETE FROM processed_images WHERE status = ?", (status,))
        self._conn.commit()
        return count

    _QUERY_SORTS: dict[str, str] = {
        "path_asc": "pi.file_path ASC",
        "path_desc": "pi.file_path DESC",
        "newest": "pi.processed_at DESC, pi.file_path ASC",
        "oldest": "pi.processed_at ASC, pi.file_path ASC",
        "judge_desc": "js.weighted_score DESC NULLS LAST, pi.file_path ASC",
        "judge_asc": "js.weighted_score ASC NULLS LAST, pi.file_path ASC",
        "shot_desc": "pi.image_date DESC NULLS LAST, pi.file_path ASC",
        "shot_asc": "pi.image_date ASC NULLS LAST, pi.file_path ASC",
    }

    @staticmethod
    def _build_query_conditions(
        tag: str | None,
        has_text: bool | None,
        cleanup_class: str | None,
        scene_category: str | None,
        city: str | None,
        country: str | None,
        status: str | None,
        min_judge_score: int | None,
        max_judge_score: int | None,
        judged: bool | None,
    ) -> tuple[list[str], list[object]]:
        """Translate filter arguments into ``(conditions, params)`` for query_images."""
        conditions: list[str] = []
        params: list[object] = []

        if tag is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM json_each(pi.tags) WHERE LOWER(value) LIKE LOWER(?))"
            )
            params.append(f"%{tag}%")
        if has_text is True:
            conditions.append("pi.has_text = 1")
        elif has_text is False:
            conditions.append("(pi.has_text = 0 OR pi.has_text IS NULL)")
        if cleanup_class is not None:
            conditions.append("pi.cleanup_class = ?")
            params.append(cleanup_class)
        if scene_category is not None:
            conditions.append("pi.scene_category = ?")
            params.append(scene_category)
        if city is not None:
            conditions.append("LOWER(pi.nearest_city) LIKE LOWER(?)")
            params.append(f"%{city}%")
        if country is not None:
            conditions.append("LOWER(pi.nearest_country) LIKE LOWER(?)")
            params.append(f"%{country}%")
        if status is not None:
            conditions.append("pi.status = ?")
            params.append(status)
        if min_judge_score is not None:
            conditions.append("js.weighted_score >= ?")
            params.append(min_judge_score)
        if max_judge_score is not None:
            conditions.append("js.weighted_score <= ?")
            params.append(max_judge_score)
        if judged is True:
            conditions.append("js.weighted_score IS NOT NULL")
        elif judged is False:
            conditions.append("js.weighted_score IS NULL")

        return conditions, params

    @staticmethod
    def _build_images_query(conditions: list[str], order_clause: str, limit: int | None) -> str:
        """Assemble the SELECT SQL for query_images from pre-validated parts."""
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
        return (  # nosec B608
            "SELECT pi.file_path, pi.tags, pi.scene_summary, pi.processed_at, pi.status, "
            "pi.cleanup_class, pi.scene_category, pi.emotional_tone, pi.event_hint, "
            "pi.significance, pi.nearest_city, pi.nearest_region, pi.nearest_country, "
            "pi.error_message, js.weighted_score, js.reason, js.verdict, pi.image_date "
            "FROM processed_images pi "
            "LEFT JOIN judge_scores js ON js.file_path = pi.file_path "
            + where  # nosec B608
            + f" ORDER BY {order_clause} "  # nosec B608
            + limit_clause
        )

    def query_images(
        self,
        tag: str | None = None,
        has_text: bool | None = None,
        cleanup_class: str | None = None,
        scene_category: str | None = None,
        city: str | None = None,
        country: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        min_judge_score: int | None = None,
        max_judge_score: int | None = None,
        judged: bool | None = None,
        sort: str = "path_asc",
    ) -> list[dict]:
        """Query images with advanced filters.

        Args:
            tag: Case-insensitive substring match against any tag value.
            has_text: True = only images with text; False = only without; None = any.
            cleanup_class: Exact match against cleanup_class ('delete', 'review', etc.).
            scene_category: Exact match against scene_category.
            city: Case-insensitive substring match against nearest_city.
            country: Case-insensitive substring match against nearest_country.
            status: Exact match against status ('ok', 'error').
            limit: Max rows to return. None = no limit.
            min_judge_score: Only return images whose judge weighted_score >= this value.
            max_judge_score: Only return images whose judge weighted_score <= this value.
            judged: True = only images with a judge_scores row; False = only un-judged;
                None = any.
            sort: One of ``path_asc`` (default), ``path_desc``, ``newest``,
                ``oldest``, ``judge_desc``, ``judge_asc``, ``shot_desc``,
                ``shot_asc``.

        Returns:
            List of image metadata dicts.
        """
        conditions, params = self._build_query_conditions(
            tag,
            has_text,
            cleanup_class,
            scene_category,
            city,
            country,
            status,
            min_judge_score,
            max_judge_score,
            judged,
        )
        order_clause = self._QUERY_SORTS.get(sort, self._QUERY_SORTS["path_asc"])
        query = self._build_images_query(conditions, order_clause, limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._query_row_to_dict(r) for r in rows]

    @staticmethod
    def _query_row_to_dict(row: tuple) -> dict:
        """Convert a query_images SELECT row to a metadata dict.

        Columns 14-16 (``js.weighted_score``, ``js.reason``, ``js.verdict``)
        come from the LEFT JOIN with ``judge_scores`` and are ``None`` for any
        image that has not been judged yet; the trailing column 17 is
        ``pi.image_date``.
        """
        file_path: str = row[0]
        tags_raw: str | None = row[1]
        try:
            tags_list: list[str] = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags_list = []
        weighted_raw = row[14] if len(row) > 14 else None
        return {
            "file_path": file_path,
            "file_name": Path(file_path).name,
            "tags_list": tags_list,
            "scene_summary": row[2],
            "processed_at": row[3],
            "status": row[4],
            "cleanup_class": row[5],
            "scene_category": row[6],
            "emotional_tone": row[7],
            "event_hint": row[8],
            "significance": row[9],
            "nearest_city": row[10],
            "nearest_region": row[11],
            "nearest_country": row[12],
            "error_message": row[13] if len(row) > 13 else None,
            "judge_score": int(round(float(weighted_raw))) if weighted_raw is not None else None,
            "judge_reason": row[15] if len(row) > 15 else None,
            "judge_verdict": row[16] if len(row) > 16 else None,
            "image_date": row[17] if len(row) > 17 else None,
        }

    def get_tag_counts(self) -> list[tuple[str, int]]:
        """Return (tag, count) pairs sorted by count descending.

        Counts how many images have each distinct tag.

        Returns:
            List of (tag_name, image_count) tuples.
        """
        rows = self._conn.execute(
            "SELECT LOWER(value), COUNT(*) AS cnt "
            "FROM processed_images, json_each(tags) "
            "WHERE tags IS NOT NULL "
            "GROUP BY LOWER(value) "
            "ORDER BY cnt DESC, LOWER(value)"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def _iter_tag_rows(self) -> list[tuple[str, list[str]]]:
        """Return ``(file_path, tags)`` for every row that has tags.

        Used by :meth:`rename_tag`, :meth:`delete_tag`, and
        :meth:`merge_tags` to share the fetch-parse boilerplate.  Rows
        whose ``tags`` column cannot be parsed as JSON are silently
        excluded.
        """
        rows = self._conn.execute(
            "SELECT file_path, tags FROM processed_images WHERE tags IS NOT NULL"
        ).fetchall()
        result = []
        for file_path, tags_raw in rows:
            try:
                tags: list[str] = json.loads(tags_raw)
            except (json.JSONDecodeError, TypeError):
                continue
            result.append((file_path, tags))
        return result

    def _write_tags(self, file_path: str, new_tags: list[str]) -> None:
        """Overwrite the tags for *file_path* inside an open transaction."""
        self._conn.execute(
            "UPDATE processed_images SET tags = ? WHERE file_path = ?",
            (json.dumps(new_tags), file_path),
        )

    def rename_tag(self, old_tag: str, new_tag: str) -> int:
        """Rename a tag across all images.

        Replaces every occurrence of *old_tag* (case-insensitive) with *new_tag*
        (lowercased). Images that already contain *new_tag* are de-duplicated so
        the tag only appears once.

        Args:
            old_tag: The tag to rename.
            new_tag: The replacement tag (will be lowercased).

        Returns:
            Number of images updated.
        """
        old_lower = old_tag.lower()
        new_lower = new_tag.lower()
        updated = 0
        with self._conn:
            for file_path, tags in self._iter_tag_rows():
                tags_lower = [t.lower() for t in tags]
                if old_lower not in tags_lower:
                    continue
                new_tags: list[str] = []
                seen: set[str] = set()
                for t in tags:
                    val = t.lower()
                    replacement = new_lower if val == old_lower else val
                    if replacement not in seen:
                        seen.add(replacement)
                        new_tags.append(replacement)
                self._write_tags(file_path, new_tags)
                updated += 1
        return updated

    def delete_tag(self, tag: str) -> int:
        """Remove a tag from all images that have it.

        Args:
            tag: The tag to delete (case-insensitive match).

        Returns:
            Number of images updated.
        """
        tag_lower = tag.lower()
        updated = 0
        with self._conn:
            for file_path, tags in self._iter_tag_rows():
                new_tags = [t for t in tags if t.lower() != tag_lower]
                if len(new_tags) == len(tags):
                    continue
                self._write_tags(file_path, new_tags)
                updated += 1
        return updated

    def merge_tags(self, source_tag: str, target_tag: str) -> int:
        """Merge *source_tag* into *target_tag*.

        For every image that has *source_tag*, removes it and adds *target_tag*
        (if not already present). The *target_tag* is lowercased.

        Args:
            source_tag: The tag to replace (case-insensitive match).
            target_tag: The tag to add (will be lowercased).

        Returns:
            Number of images updated.
        """
        src_lower = source_tag.lower()
        tgt_lower = target_tag.lower()
        updated = 0
        with self._conn:
            for file_path, tags in self._iter_tag_rows():
                tags_lower = [t.lower() for t in tags]
                if src_lower not in tags_lower:
                    continue
                new_tags = [t for t in tags if t.lower() != src_lower]
                if tgt_lower not in [t.lower() for t in new_tags]:
                    new_tags.append(tgt_lower)
                self._write_tags(file_path, new_tags)
                updated += 1
        return updated

    def get_cleanup_candidates(self, include_review: bool = False) -> list[dict]:
        """Return photos flagged for cleanup.

        Returns list of dicts with keys: file_path, file_name (basename),
        cleanup_class, tags, scene_summary, image_date, nearest_city, nearest_country.
        Always includes cleanup_class='delete'. If include_review=True, also includes 'review'.
        Orders by file_path.
        """
        if include_review:
            placeholders = "?, ?"
            params: tuple = ("delete", "review")
        else:
            placeholders = "?"
            params = ("delete",)
        # Parameterized query; placeholders are code-controlled literals
        query = (  # nosec B608
            "SELECT file_path, tags, scene_summary, image_date, cleanup_class, "
            "nearest_city, nearest_country "
            "FROM processed_images "
            "WHERE cleanup_class IN ("
            + placeholders  # nosec B608
            + ") "
            "ORDER BY file_path"
        )
        rows = self._conn.execute(query, params).fetchall()

        result = []
        for row in rows:
            file_path = row[0]
            result.append(
                {
                    "file_path": file_path,
                    "file_name": Path(file_path).name,
                    "tags": row[1],
                    "scene_summary": row[2],
                    "image_date": row[3],
                    "cleanup_class": row[4],
                    "nearest_city": row[5],
                    "nearest_country": row[6],
                }
            )
        return result

    # --- review UI query/update methods ---

    # SQLite basename idiom over the separator-normalized path NORM
    # (backslashes folded to '/' so Windows paths work too):
    # replace(NORM, '/', '') yields the set of non-slash characters,
    # rtrim(NORM, <that>) strips the basename off the right (stopping at the
    # last '/'), and the outer replace() removes that directory prefix.
    # A file_path tie-breaker keeps pagination stable for duplicate basenames.
    _NORM_PATH = "replace(file_path, '\\', '/')"
    _NAME_SORT_EXPR = (
        f"LOWER(replace({_NORM_PATH}, rtrim({_NORM_PATH}, replace({_NORM_PATH}, '/', '')), ''))"
    )

    _GET_IMAGES_SORTS: dict[str, str] = {
        "path_asc": "file_path ASC",
        "path_desc": "file_path DESC",
        "newest": "processed_at DESC, file_path ASC",
        "oldest": "processed_at ASC, file_path ASC",
        "name_asc": f"{_NAME_SORT_EXPR} ASC, file_path ASC",
        "name_desc": f"{_NAME_SORT_EXPR} DESC, file_path DESC",
    }

    def get_images(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        cleanup_class: str | None = None,
        sort: str = "path_asc",
    ) -> list[dict]:
        """Return paginated image records with all metadata.

        Args:
            limit: Max rows to return.
            offset: Row offset for pagination.
            status: Optional filter by status ('ok', 'error').
            cleanup_class: Optional filter by cleanup_class ('delete', 'review').
            sort: One of ``path_asc`` (default), ``path_desc``, ``newest``,
                ``oldest``, ``name_asc``, ``name_desc``.

        Returns:
            List of image metadata dicts.
        """
        conditions: list[str] = []
        params: list[object] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if cleanup_class is not None:
            conditions.append("cleanup_class = ?")
            params.append(cleanup_class)
        order_clause = self._GET_IMAGES_SORTS.get(sort, self._GET_IMAGES_SORTS["path_asc"])
        # All filter columns live on ``processed_images`` and the join key
        # ``file_path`` is the only column that collides with judge_scores,
        # so qualify each predicate with the ``pi.`` alias.
        joined_where = "WHERE " + " AND ".join(f"pi.{c}" for c in conditions) if conditions else ""
        # Sort clauses reference unambiguous columns (file_path resolves to
        # processed_images via the alias, processed_at and the LOWER()
        # variants only exist on processed_images).
        order_qualified = order_clause.replace("file_path", "pi.file_path").replace(
            "processed_at", "pi.processed_at"
        )
        query = (  # nosec B608
            "SELECT pi.file_path, pi.tags, pi.scene_summary, pi.processed_at, "
            "pi.status, pi.cleanup_class, pi.scene_category, pi.emotional_tone, "
            "pi.event_hint, pi.significance, pi.error_message, "
            "js.weighted_score, js.verdict, js.reason "
            "FROM processed_images pi "
            "LEFT JOIN judge_scores js ON js.file_path = pi.file_path "
            + joined_where  # nosec B608
            + f" ORDER BY {order_qualified} LIMIT ? OFFSET ?"  # nosec B608
        )
        params.extend([limit, offset])
        rows = self._conn.execute(query, params).fetchall()
        return [self._image_row_to_dict(r) for r in rows]

    def count_images(
        self,
        status: str | None = None,
        cleanup_class: str | None = None,
    ) -> int:
        """Count images matching optional filters."""
        conditions: list[str] = []
        params: list[object] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if cleanup_class is not None:
            conditions.append("cleanup_class = ?")
            params.append(cleanup_class)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = "SELECT COUNT(*) FROM processed_images " + where  # nosec B608
        return self._conn.execute(query, params).fetchone()[0]

    def get_image(self, file_path: str) -> dict | None:
        """Return metadata for a single image, or None if not found.

        The join with ``judge_scores`` brings the weighted-judge score and
        verdict back so the review UI and dashboard click-through can show
        them on the per-image card without a follow-up request.
        """
        row = self._conn.execute(
            "SELECT pi.file_path, pi.tags, pi.scene_summary, pi.processed_at, "
            "pi.status, pi.cleanup_class, pi.scene_category, pi.emotional_tone, "
            "pi.event_hint, pi.significance, pi.error_message, "
            "js.weighted_score, js.verdict, js.reason "
            "FROM processed_images pi "
            "LEFT JOIN judge_scores js ON js.file_path = pi.file_path "
            "WHERE pi.file_path = ?",
            (file_path,),
        ).fetchone()
        if row is None:
            return None
        return self._image_row_to_dict(row)

    def get_known_file_path(self, file_path: str) -> str | None:
        """Return the stored path if known to tagging **or** judging, else None.

        ``processed_images`` (from ``run``) and ``judge_scores`` (from ``judge``)
        are independent: an image can be judged without ever being tagged. The
        webapp's thumbnail/original/open-in-photos endpoints use the HTTP value
        only as a lookup key and then read the DB-stored path, so resolving via
        either table lets a judged-but-untagged image still preview (previously
        only ``processed_images`` was consulted, so the Judge grid fell back to
        filename text for every untagged image).
        """
        row = self._conn.execute(
            "SELECT file_path FROM processed_images WHERE file_path = ? "
            "UNION SELECT file_path FROM judge_scores WHERE file_path = ? "
            "LIMIT 1",
            (file_path, file_path),
        ).fetchone()
        return row[0] if row else None

    def update_image_tags(self, file_path: str, tags: list[str]) -> None:
        """Overwrite the tags list for an image."""
        self._conn.execute(
            "UPDATE processed_images SET tags = ? WHERE file_path = ?",
            (json.dumps(tags), file_path),
        )
        self._conn.commit()

    def update_image_cleanup(self, file_path: str, cleanup_class: str | None) -> None:
        """Set or clear the cleanup_class for an image."""
        self._conn.execute(
            "UPDATE processed_images SET cleanup_class = ? WHERE file_path = ?",
            (cleanup_class, file_path),
        )
        self._conn.commit()

    def delete_image(self, file_path: str) -> bool:
        """Delete a single row from ``processed_images`` by file_path.

        Used by the Edit page after successfully removing the photo from
        Apple Photos so a re-scan does not re-process the now-trashed
        image. Returns True if a row was deleted, False if the path was
        not present.
        """
        cur = self._conn.execute(
            "DELETE FROM processed_images WHERE file_path = ?",
            (file_path,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def iter_image_paths(self, batch_size: int = _DEFAULT_PATH_BATCH_SIZE) -> "Iterator[str]":
        """Yield every ``file_path`` from ``processed_images`` in batches.

        The drift-cleanup walk runs over a 22 k-row DB on the user's
        machine; pulling everything into a single Python list pays an
        unnecessary memory cost. A keyset cursor (``WHERE file_path >
        last_seen``) is used instead of ``LIMIT … OFFSET`` so each page
        costs O(log N) rather than a full or partial table scan, and rows
        cannot be skipped or repeated if concurrent deletes occur between
        pages (WAL mode, ``check_same_thread=False``).
        """
        last_path: str | None = None
        while True:
            if last_path is None:
                rows = self._conn.execute(
                    "SELECT file_path FROM processed_images ORDER BY file_path LIMIT ?",
                    (int(batch_size),),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT file_path FROM processed_images"
                    " WHERE file_path > ? ORDER BY file_path LIMIT ?",
                    (last_path, int(batch_size)),
                ).fetchall()
            if not rows:
                return
            for (path,) in rows:
                yield path
            if len(rows) < batch_size:
                return
            last_path = rows[-1][0]

    def delete_image_rows(self, paths: list[str]) -> int:
        """Bulk-delete rows from ``processed_images`` matching *paths*.

        Uses a single ``DELETE … WHERE file_path IN (…)`` statement so the
        returned ``rowcount`` is accurate without any before/after COUNT(*)
        queries. Returns the number of rows actually removed (which may be
        smaller than ``len(paths)`` when some paths were already gone).
        """
        if not paths:
            return 0
        placeholders = ",".join("?" * len(paths))
        cur = self._conn.execute(
            f"DELETE FROM processed_images WHERE file_path IN ({placeholders})",  # nosec B608
            paths,
        )
        self._conn.commit()
        return cur.rowcount

    @staticmethod
    def _image_row_to_dict(row: tuple) -> dict:
        """Convert a processed_images SELECT row to a metadata dict.

        ``tags`` is returned as a parsed list of strings; iterating it as
        ``for t in row['tags']`` yields tag values, not characters. The
        legacy ``tags_list`` alias is kept for any callers that already
        depend on it.
        """
        file_path: str = row[0]
        tags_raw: str | None = row[1]
        try:
            tags_list: list[str] = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags_list = []
        weighted_raw = row[11] if len(row) > 11 else None
        return {
            "file_path": file_path,
            "file_name": Path(file_path).name,
            "tags": tags_list,
            "tags_list": tags_list,
            "scene_summary": row[2],
            "processed_at": row[3],
            "status": row[4],
            "cleanup_class": row[5],
            "scene_category": row[6],
            "emotional_tone": row[7],
            "event_hint": row[8],
            "significance": row[9],
            "error_message": row[10] if len(row) > 10 else None,
            # Pulled from the optional LEFT JOIN with judge_scores. These
            # are ``None`` for any image that has not been judged yet.
            "judge_score": int(round(float(weighted_raw))) if weighted_raw is not None else None,
            "judge_verdict": row[12] if len(row) > 12 else None,
            "judge_reason": row[13] if len(row) > 13 else None,
        }

    def is_fresh(self, file_path: Path) -> bool:
        """Return True if DB has a row for this file and size/mtime still match."""
        row = self._conn.execute(
            "SELECT file_size, file_mtime FROM processed_images WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
        if row is None:
            return False
        try:
            stat = file_path.stat()
        except OSError:
            return False
        return row[0] == stat.st_size and row[1] == stat.st_mtime

    def get_cached_result(self, file_path: Path) -> ImageResult | None:
        """Return an ImageResult built from the stored DB row, or None if not found."""
        row = self._conn.execute(
            """
            SELECT tags, scene_summary, status, error_message,
                   scene_category, emotional_tone, cleanup_class,
                   has_text, text_summary, event_hint, significance
            FROM processed_images
            WHERE file_path = ?
            """,
            (str(file_path),),
        ).fetchone()
        if row is None:
            return None
        (
            tags_raw,
            scene_summary,
            status,
            error_message,
            scene_category,
            emotional_tone,
            cleanup_class,
            has_text_int,
            text_summary,
            event_hint,
            significance,
        ) = row
        try:
            tags: list[str] = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        return ImageResult(
            file_path=str(file_path),
            file_name=file_path.name,
            tags=tags,
            scene_summary=scene_summary,
            processing_status=status if status is not None else "ok",
            error_message=error_message,
            scene_category=scene_category,
            emotional_tone=emotional_tone,
            cleanup_class=cleanup_class,
            has_text=bool(has_text_int),
            text_summary=text_summary,
            event_hint=event_hint,
            significance=significance,
        )

    def is_complete_cached(self, file_path: Path) -> bool:
        """Return True if the DB has a fresh, successful row with non-empty tags.

        Combines the freshness (size+mtime), status, and non-empty-tags checks
        into a single query so the skip decision costs one SELECT plus one
        ``stat()`` — the fast path used by ``run --skip-existing`` to bypass
        EXIF, geocoding, and write-back for already-processed photos.
        """
        row = self._conn.execute(
            "SELECT file_size, file_mtime, status, tags FROM processed_images WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
        if row is None:
            return False
        size, mtime, status, tags_raw = row
        if status != "ok":
            return False
        try:
            tags: list[str] = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        if not tags:
            return False
        try:
            stat = file_path.stat()
        except OSError:
            return False
        return size == stat.st_size and mtime == stat.st_mtime

    def has_usable_model_result(self, file_path: Path) -> bool:
        """Return True if the DB has a fresh row with non-empty tags.

        A tags value that is not valid JSON is treated as no tags, so a row
        with a corrupt tags blob is reported as not usable (returns False)
        rather than raising.
        """
        if not self.is_fresh(file_path):
            return False
        row = self._conn.execute(
            "SELECT tags FROM processed_images WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
        if row is None:
            return False
        try:
            tags: list[str] = json.loads(row[0]) if row[0] else []
        except (json.JSONDecodeError, TypeError):
            tags = []  # malformed JSON -> treat as no tags
        return len(tags) > 0

    def update_missing_fields(self, file_path: Path, result: ImageResult) -> None:
        """Update only NULL columns from result.

        ``has_text`` additionally treats 0 as unset (it can be upgraded
        0 -> 1 but never downgraded). Other existing non-null values are
        never overwritten.
        """
        self._conn.execute(
            """
            UPDATE processed_images SET
                scene_summary  = COALESCE(scene_summary,  ?),
                scene_category = COALESCE(scene_category, ?),
                emotional_tone = COALESCE(emotional_tone, ?),
                cleanup_class  = COALESCE(cleanup_class,  ?),
                has_text       = CASE WHEN has_text = 0 OR has_text IS NULL
                                      THEN ? ELSE has_text END,
                text_summary   = COALESCE(text_summary,   ?),
                event_hint     = COALESCE(event_hint,     ?),
                significance   = COALESCE(significance,   ?)
            WHERE file_path = ?
            """,
            (
                result.scene_summary,
                result.scene_category,
                result.emotional_tone,
                result.cleanup_class,
                1 if result.has_text else 0,
                result.text_summary,
                result.event_hint,
                result.significance,
                str(file_path),
            ),
        )
        self._conn.commit()
