"""Tests for disk cache."""

from __future__ import annotations

from pyimgtag.cache import DiskCache


class TestDiskCache:
    def test_set_and_get(self, tmp_path):
        c = DiskCache(tmp_path / "cache.json")
        c.set("k1", {"city": "SF"})
        assert c.get("k1") == {"city": "SF"}

    def test_persistence(self, tmp_path):
        path = tmp_path / "cache.json"
        c1 = DiskCache(path)
        c1.set("k1", {"a": 1})

        c2 = DiskCache(path)
        assert c2.get("k1") == {"a": 1}

    def test_missing_key(self, tmp_path):
        c = DiskCache(tmp_path / "cache.json")
        assert c.get("missing") is None

    def test_corrupt_file(self, tmp_path):
        path = tmp_path / "cache.json"
        path.write_text("not json")
        c = DiskCache(path)
        assert c.get("any") is None
