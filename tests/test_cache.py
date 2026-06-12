"""Tests for disk cache."""

from __future__ import annotations

import json
import time
from datetime import timedelta
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

    def test_utf8_roundtrip(self, tmp_path):
        # set/get must survive non-ASCII place names through the file.
        path = tmp_path / "cache.json"
        c = DiskCache(path)
        c.set("k1", {"place": "Łódź Óbidos ā"})
        c2 = DiskCache(path)
        assert c2.get("k1") == {"place": "Łódź Óbidos ā"}

    def test_non_utf8_file_treated_as_corrupt(self, tmp_path):
        # A legacy locale-encoded file that is not valid UTF-8 must be
        # discarded as an empty cache, not raise UnicodeDecodeError.
        path = tmp_path / "cache.json"
        path.write_bytes(b'{"k1": {"place": "caf\xe9"}}')  # latin-1 e-acute, invalid UTF-8
        c = DiskCache(path)
        assert c.get("k1") is None

    def test_legacy_entry_without_wrapper_is_miss(self, tmp_path):
        # Entries written by older versions lack the {"v": ..., "ts": ...} wrapper.
        # They must be treated as misses, not raise or return garbage.
        path = tmp_path / "cache.json"
        path.write_text(json.dumps({"k1": {"city": "SF"}}), encoding="utf-8")
        c = DiskCache(path)
        assert c.get("k1") is None

    def test_atomic_write_cleans_up_tmp_on_rename_failure(self, tmp_path):
        path = tmp_path / "cache.json"
        c = DiskCache(path)
        with patch("pathlib.Path.replace", side_effect=OSError("rename failed")):
            with pytest.raises(OSError, match="rename failed"):
                c.set("k", {"v": 1})
        tmp = path.with_suffix(".tmp")
        assert not tmp.exists()

    def test_atomic_write_cleans_up_tmp_on_write_text_failure(self, tmp_path):
        """A failure during write_text (non-OSError) must still clean up the .tmp file."""
        path = tmp_path / "cache.json"
        c = DiskCache(path)
        # Pre-create the .tmp file (before patching write_text) so the cleanup
        # branch has something real to remove — a patched write_text raises
        # without creating the file, which would make the assertion vacuous.
        tmp = path.with_suffix(".tmp")
        tmp.touch()
        with patch("pathlib.Path.write_text", side_effect=RuntimeError("disk quota")):
            with pytest.raises(RuntimeError, match="disk quota"):
                c.set("k", {"v": 1})
        assert not tmp.exists()


class TestDiskCacheTTL:
    def test_fresh_entry_is_returned(self, tmp_path):
        c = DiskCache(tmp_path / "cache.json", ttl=timedelta(hours=1))
        c.set("k", {"city": "NYC"})
        assert c.get("k") == {"city": "NYC"}

    def test_expired_entry_is_miss(self, tmp_path):
        c = DiskCache(tmp_path / "cache.json", ttl=timedelta(seconds=10))
        c.set("k", {"city": "NYC"})
        # Simulate the entry having been written 11 seconds ago
        c._data["k"]["ts"] = time.time() - 11
        assert c.get("k") is None

    def test_no_ttl_never_expires(self, tmp_path):
        c = DiskCache(tmp_path / "cache.json")
        c.set("k", {"city": "NYC"})
        c._data["k"]["ts"] = 0  # far in the past
        assert c.get("k") == {"city": "NYC"}


class TestDiskCacheMaxSize:
    def test_set_respects_max_size(self, tmp_path):
        c = DiskCache(tmp_path / "cache.json", max_size=2)
        c.set("a", {"v": 1})
        c.set("b", {"v": 2})
        c.set("c", {"v": 3})
        assert len(c._data) <= 2

    def test_oldest_entry_evicted_first(self, tmp_path):
        c = DiskCache(tmp_path / "cache.json", max_size=2)
        c.set("a", {"v": 1})
        # Push "a" into the past so it's oldest
        c._data["a"]["ts"] = time.time() - 100
        c.set("b", {"v": 2})
        c.set("c", {"v": 3})  # triggers eviction of "a"
        assert c.get("a") is None
        assert c.get("b") == {"v": 2}
        assert c.get("c") == {"v": 3}

    def test_no_max_size_is_unbounded(self, tmp_path):
        c = DiskCache(tmp_path / "cache.json")
        for i in range(100):
            c.set(str(i), {"n": i})
        assert len(c._data) == 100
