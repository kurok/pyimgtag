"""Tests for directory and Photos library scanning."""

from __future__ import annotations

import pytest

from pyimgtag.scanner import scan_directory, scan_photos_library


class TestScanDirectory:
    def test_finds_images(self, tmp_path):
        (tmp_path / "a.jpg").touch()
        (tmp_path / "b.png").touch()
        (tmp_path / "c.txt").touch()
        files = scan_directory(tmp_path)
        names = [f.name for f in files]
        assert "a.jpg" in names
        assert "b.png" in names
        assert "c.txt" not in names

    def test_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.jpeg").touch()
        files = scan_directory(tmp_path)
        assert any(f.name == "deep.jpeg" for f in files)

    def test_custom_extensions(self, tmp_path):
        (tmp_path / "a.jpg").touch()
        (tmp_path / "b.tiff").touch()
        files = scan_directory(tmp_path, extensions={"tiff"})
        assert len(files) == 1
        assert files[0].name == "b.tiff"

    def test_missing_dir(self):
        with pytest.raises(FileNotFoundError):
            scan_directory("/nonexistent/path/12345")

    def test_empty_dir(self, tmp_path):
        assert scan_directory(tmp_path) == []


class TestScanPhotosLibrary:
    def test_originals_dir(self, tmp_path):
        originals = tmp_path / "originals"
        originals.mkdir()
        (originals / "photo.jpg").touch()
        files = scan_photos_library(tmp_path)
        assert len(files) == 1

    def test_masters_fallback(self, tmp_path):
        masters = tmp_path / "Masters"
        masters.mkdir()
        (masters / "photo.jpg").touch()
        files = scan_photos_library(tmp_path)
        assert len(files) == 1

    def test_no_originals(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Cannot find originals"):
            scan_photos_library(tmp_path)

    def test_missing_library(self):
        with pytest.raises(FileNotFoundError):
            scan_photos_library("/nonexistent/12345.photoslibrary")
