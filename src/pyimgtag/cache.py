"""Simple JSON-file disk cache for geocoding results."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class DiskCache:
    """Key-value cache backed by a JSON file.

    Not thread-safe. Callers that share a DiskCache across threads must
    provide their own lock. Concurrent writers may produce a corrupt JSON file.
    """

    def __init__(self, cache_path: str | Path) -> None:
        """Open the cache at ``cache_path``, loading it into memory.

        The file is read eagerly on construction. A missing, unreadable, or
        corrupt file is treated as an empty cache (its contents are discarded)
        rather than raising — the cache is non-critical.
        """
        self._path = Path(cache_path)
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                # Non-critical cache: drop the unreadable contents and carry on,
                # but leave a breadcrumb so a recurring corruption is findable.
                logger.debug("discarding unreadable cache %s: %s", self._path, e)
                self._data = {}

    def get(self, key: str) -> dict | None:
        """Return the cached dict for ``key``, or None if absent or not a dict."""
        v = self._data.get(key)
        return v if isinstance(v, dict) else None

    def set(self, key: str, value: dict) -> None:
        """Store ``value`` under ``key`` and persist the cache via atomic replace."""
        self._data[key] = value
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
