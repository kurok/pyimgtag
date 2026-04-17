"""Tests for disk cache."""

from __future__ import annotations

from unittest.mock import patch

import pytest

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

    def test_atomic_write_cleans_up_tmp_on_rename_failure(self, tmp_path):
        path = tmp_path / "cache.json"
        c = DiskCache(path)
        with patch("pathlib.Path.replace", side_effect=OSError("rename failed")):
            with pytest.raises(OSError, match="rename failed"):
                c.set("k", {"v": 1})
        tmp = path.with_suffix(".tmp")
        assert not tmp.exists()
