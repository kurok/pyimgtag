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

    def test_utf8_file_read_back_correctly(self, tmp_path):
        # _save writes UTF-8 with ensure_ascii=False; _load must decode UTF-8
        # explicitly so non-ASCII place names survive regardless of the locale
        # codec (cp1252 on Windows would otherwise crash or mojibake).
        path = tmp_path / "cache.json"
        path.write_bytes('{"k1": {"place": "Łódź Óbidos ā"}}'.encode("utf-8"))
        c = DiskCache(path)
        assert c.get("k1") == {"place": "Łódź Óbidos ā"}

    def test_non_utf8_file_treated_as_corrupt(self, tmp_path):
        # A legacy locale-encoded file that is not valid UTF-8 must be
        # discarded as an empty cache, not raise UnicodeDecodeError.
        path = tmp_path / "cache.json"
        path.write_bytes(b'{"k1": {"place": "caf\xe9"}}')  # latin-1 e-acute, invalid UTF-8
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
