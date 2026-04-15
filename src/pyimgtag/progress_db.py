"""SQLite progress database for incremental image processing."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pyimgtag.models import ImageResult


class ProgressDB:
    """Track which images have been processed to enable incremental re-runs."""

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
                 processed_at, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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

    def close(self) -> None:
        self._conn.close()
