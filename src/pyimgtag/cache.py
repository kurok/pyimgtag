"""Simple JSON-file disk cache for geocoding results."""

from __future__ import annotations

import json
from pathlib import Path


class DiskCache:
    """Key-value cache backed by a JSON file."""

    def __init__(self, cache_path: str | Path) -> None:
        self._path = Path(cache_path)
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, key: str) -> dict | None:
        v = self._data.get(key)
        return v if isinstance(v, dict) else None

    def set(self, key: str, value: dict) -> None:
        self._data[key] = value
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False))
        tmp.replace(self._path)
