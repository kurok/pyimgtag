"""Simple JSON-file disk cache for geocoding results."""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class DiskCache:
    """Key-value cache backed by a JSON file with optional TTL and max-size eviction.

    On-disk format: ``{"key": {"v": <value>, "ts": <unix-timestamp-float>}, ...}``
    Legacy entries that lack the ``v``/``ts`` wrapper are silently treated as
    cache misses, so upgrading from an older cache is safe — entries are just
    re-fetched on the next access.

    Not thread-safe. Callers that share a DiskCache across threads must
    provide their own lock. Concurrent writers may produce a corrupt JSON file.
    """

    def __init__(
        self,
        cache_path: str | Path,
        max_size: int | None = None,
        ttl: timedelta | None = None,
    ) -> None:
        """Open the cache at ``cache_path``, loading it into memory.

        Args:
            cache_path: Path to the backing JSON file.
            max_size: Maximum number of entries. When exceeded on ``set``,
                the entry with the oldest timestamp is evicted. ``None`` means
                unbounded (the previous default behaviour).
            ttl: Maximum age of a cache entry. Entries older than this are
                treated as cache misses on ``get``. ``None`` means no TTL.
        """
        self._path = Path(cache_path)
        self._max_size = max_size
        self._ttl_seconds: float | None = ttl.total_seconds() if ttl else None
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                # _save writes UTF-8; read with the same explicit encoding so
                # non-ASCII place names survive regardless of the locale codec.
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                # Non-critical cache: drop the unreadable contents and carry on,
                # but leave a breadcrumb so a recurring corruption is findable.
                logger.debug("discarding unreadable cache %s: %s", self._path, e)
                self._data = {}

    def _is_valid(self, entry: object) -> bool:
        if not isinstance(entry, dict) or "v" not in entry or "ts" not in entry:
            return False
        if self._ttl_seconds is not None:
            if time.time() - entry["ts"] > self._ttl_seconds:
                return False
        return True

    def get(self, key: str) -> dict | None:
        """Return the cached dict for ``key``, or None if absent, expired, or not a dict."""
        entry = self._data.get(key)
        if not self._is_valid(entry):
            return None
        v = entry["v"]  # type: ignore[index]
        return v if isinstance(v, dict) else None

    def set(self, key: str, value: dict) -> None:
        """Store ``value`` under ``key`` and persist the cache via atomic replace."""
        self._data[key] = {"v": value, "ts": time.time()}
        if self._max_size is not None:
            while len(self._data) > self._max_size:
                oldest = min(self._data, key=lambda k: self._data[k].get("ts", 0))
                del self._data[oldest]
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
