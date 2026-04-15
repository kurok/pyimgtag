"""SQLite progress database for incremental image processing."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pyimgtag.models import ImageResult


class ProgressDB:
    """Track which images have been processed to enable incremental re-runs."""

    # Each entry is (target_version, sql_statement).
    # Version 1 is the baseline schema created by _create_table().
    # Migrations are applied in ascending version order on every open.
    _MIGRATIONS: tuple[tuple[int, str], ...] = (
        (2, "ALTER TABLE processed_images ADD COLUMN scene_category TEXT"),
        (2, "ALTER TABLE processed_images ADD COLUMN emotional_tone TEXT"),
        (2, "ALTER TABLE processed_images ADD COLUMN cleanup_class TEXT"),
        (2, "ALTER TABLE processed_images ADD COLUMN has_text INTEGER DEFAULT 0"),
        (2, "ALTER TABLE processed_images ADD COLUMN text_summary TEXT"),
        (2, "ALTER TABLE processed_images ADD COLUMN event_hint TEXT"),
        (2, "ALTER TABLE processed_images ADD COLUMN significance TEXT"),
    )

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".cache" / "pyimgtag" / "progress.db"
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()

    def _create_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_images (
                file_path    TEXT PRIMARY KEY,
                file_size    INTEGER,
                file_mtime   REAL,
                tags         TEXT,
                scene_summary TEXT,
                processed_at TEXT,
                status       TEXT,
                error_message TEXT
            )
            """
        )
        self._conn.commit()
        # Mark a brand-new database (user_version == 0) as version 1 so that
        # existing migration entries for version > 1 still apply on first open.
        current: int = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if current == 0:
            self._conn.execute("PRAGMA user_version = 1")
        self._migrate()

    def _migrate(self) -> None:
        """Apply any pending versioned migrations and update user_version."""
        current: int = self._conn.execute("PRAGMA user_version").fetchone()[0]
        # Collect all target versions that are still pending.
        pending_versions = sorted({ver for ver, _ in self._MIGRATIONS if ver > current})
        if not pending_versions:
            return
        for target_ver in pending_versions:
            stmts = [sql for ver, sql in self._MIGRATIONS if ver == target_ver]
            for sql in stmts:
                try:
                    self._conn.execute(sql)
                except sqlite3.OperationalError as exc:
                    # Tolerate "duplicate column name" so re-running migrations
                    # on an already-migrated DB is safe.
                    if "duplicate column name" not in str(exc).lower():
                        raise
            self._conn.execute(f"PRAGMA user_version = {target_ver}")
        self._conn.commit()

    def is_processed(self, file_path: Path) -> bool:
        """Return True if the file was already processed and hasn't changed."""
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

    def mark_done(self, file_path: Path, result: ImageResult) -> None:
        """Record a processed image result."""
        try:
            stat = file_path.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            size = 0
            mtime = 0.0
        self._conn.execute(
            """
            INSERT OR REPLACE INTO processed_images
                (file_path, file_size, file_mtime, tags, scene_summary,
                 processed_at, status, error_message,
                 scene_category, emotional_tone, cleanup_class, has_text,
                 text_summary, event_hint, significance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def get_cleanup_candidates(self, include_review: bool = False) -> list[dict]:
        """Return photos flagged for cleanup.

        Returns list of dicts with keys: file_path, file_name (basename),
        cleanup_class, tags, scene_summary, image_date, nearest_city, nearest_country.
        Always includes cleanup_class='delete'. If include_review=True, also includes 'review'.
        Orders by file_path.
        """
        try:
            if include_review:
                placeholders = "?, ?"
                params: tuple = ("delete", "review")
            else:
                placeholders = "?"
                params = ("delete",)
            # Parameterized query; placeholders are code-controlled literals
            query = (  # nosec B608
                "SELECT file_path, tags, scene_summary, processed_at, cleanup_class "
                "FROM processed_images "
                "WHERE cleanup_class IN ("
                + placeholders  # nosec B608
                + ") "
                "ORDER BY file_path"
            )
            rows = self._conn.execute(query, params).fetchall()
        except Exception:
            return []

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
                    "nearest_city": None,
                    "nearest_country": None,
                }
            )
        return result

    def close(self) -> None:
        self._conn.close()
