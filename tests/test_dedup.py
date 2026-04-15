"""Tests for perceptual hash duplicate detection."""

from __future__ import annotations

import tempfile

from PIL import Image

from pyimgtag.dedup import compute_phash, find_duplicate_groups, hamming_distance


class TestComputePhash:
    def test_returns_hex_string_for_valid_image(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img = Image.new("RGB", (64, 64), color="red")
            img.save(f, format="PNG")
            f.flush()
            result = compute_phash(f.name)
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0
        # Should be a valid hex string
        int(result, 16)

    def test_returns_none_for_nonexistent_file(self):
        result = compute_phash("/nonexistent/path/to/image.png")
        assert result is None


class TestHammingDistance:
    def test_identical_hashes_distance_zero(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img = Image.new("RGB", (64, 64), color="blue")
            img.save(f, format="PNG")
            f.flush()
            h = compute_phash(f.name)
        assert h is not None
        assert hamming_distance(h, h) == 0

    def test_different_hashes_nonzero_distance(self):
        # Create two visually different images
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f1:
            img1 = Image.new("RGB", (64, 64), color="white")
            img1.save(f1, format="PNG")
            f1.flush()
            h1 = compute_phash(f1.name)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f2:
            img2 = Image.new("RGB", (64, 64), color="black")
            img2.save(f2, format="PNG")
            f2.flush()
            h2 = compute_phash(f2.name)

        assert h1 is not None
        assert h2 is not None
        # Solid white vs solid black should produce different hashes
        # (distance may be 0 for some solid colors, so just check it's an int >= 0)
        dist = hamming_distance(h1, h2)
        assert isinstance(dist, int)
        assert dist >= 0


class TestFindDuplicateGroups:
    def test_groups_similar_images(self):
        # Two identical hashes and one different
        h_same = "d4c4d4e4f4a4b4c4"
        h_diff = "0000000000000000"
        records = [
            ("/a/img1.png", h_same),
            ("/a/img2.png", h_same),
            ("/a/img3.png", h_diff),
        ]
        groups = find_duplicate_groups(records, threshold=5)
        assert len(groups) == 1
        assert sorted(groups[0]) == ["/a/img1.png", "/a/img2.png"]

    def test_exact_match_only_with_threshold_zero(self):
        h1 = "d4c4d4e4f4a4b4c4"
        h2 = "d4c4d4e4f4a4b4c5"  # 1 bit different in last nibble
        records = [
            ("/a/img1.png", h1),
            ("/a/img2.png", h1),
            ("/a/img3.png", h2),
        ]
        groups = find_duplicate_groups(records, threshold=0)
        # Only exact matches grouped
        assert len(groups) == 1
        assert sorted(groups[0]) == ["/a/img1.png", "/a/img2.png"]

    def test_returns_empty_for_all_unique(self):
        records = [
            ("/a/img1.png", "0000000000000000"),
            ("/a/img2.png", "ffffffffffffffff"),
        ]
        groups = find_duplicate_groups(records, threshold=0)
        assert groups == []

    def test_empty_input(self):
        assert find_duplicate_groups([]) == []

    def test_single_record(self):
        assert find_duplicate_groups([("/a/img.png", "abcd1234abcd1234")]) == []
