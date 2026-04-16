"""Tests for ImageResult.build_description, normalize_tags, and face dataclasses."""

from __future__ import annotations

from pyimgtag.models import FaceDetection, ImageResult, PersonCluster, normalize_tags


class TestBuildDescription:
    def test_summary_only(self):
        r = ImageResult(scene_summary="a cozy cafe with warm lighting")
        desc = r.build_description()
        assert desc == "a cozy cafe with warm lighting."

    def test_summary_with_location(self):
        r = ImageResult(
            scene_summary="sunset over the ocean",
            nearest_city="San Francisco",
            nearest_region="California",
            nearest_country="US",
        )
        desc = r.build_description()
        assert "sunset over the ocean." in desc
        assert "San Francisco, California, US." in desc

    def test_summary_with_date(self):
        r = ImageResult(
            scene_summary="cherry blossoms in a park",
            image_date="2026-04-01 14:30:00",
        )
        desc = r.build_description()
        assert "cherry blossoms in a park." in desc
        assert "April 2026." in desc

    def test_summary_with_location_and_date(self):
        r = ImageResult(
            scene_summary="golden hour at the beach",
            nearest_city="Malibu",
            nearest_country="US",
            image_date="2026-03-15 18:00:00",
        )
        desc = r.build_description()
        assert desc == "golden hour at the beach. Malibu, US. March 2026."

    def test_no_summary_returns_none(self):
        r = ImageResult(nearest_city="Tokyo", image_date="2026-01-01 12:00:00")
        assert r.build_description() is None

    def test_partial_location(self):
        r = ImageResult(
            scene_summary="a busy street market",
            nearest_city="Bangkok",
        )
        desc = r.build_description()
        assert desc == "a busy street market. Bangkok."

    def test_summary_already_has_period(self):
        r = ImageResult(scene_summary="a mountain peak.")
        desc = r.build_description()
        assert desc == "a mountain peak."
        assert ".." not in desc

    def test_invalid_date_ignored(self):
        r = ImageResult(scene_summary="a river", image_date="not-a-date")
        desc = r.build_description()
        assert desc == "a river."

    def test_empty_summary_returns_none(self):
        r = ImageResult(scene_summary="")
        assert r.build_description() is None


class TestNormalizeTags:
    def test_lowercases(self):
        assert normalize_tags(["Sunset", "BEACH"]) == ["sunset", "beach"]

    def test_strips_whitespace(self):
        assert normalize_tags(["  sunset ", " beach"]) == ["sunset", "beach"]

    def test_deduplicates(self):
        assert normalize_tags(["sunset", "Sunset", "SUNSET"]) == ["sunset"]

    def test_caps_at_max(self):
        tags = ["a", "b", "c", "d", "e", "f", "g"]
        assert len(normalize_tags(tags)) == 5

    def test_custom_max(self):
        tags = ["a", "b", "c", "d", "e"]
        assert len(normalize_tags(tags, max_tags=3)) == 3

    def test_skips_empty(self):
        assert normalize_tags(["sunset", "", None, "beach"]) == ["sunset", "beach"]

    def test_preserves_order(self):
        assert normalize_tags(["zebra", "apple", "mango"]) == ["zebra", "apple", "mango"]

    def test_empty_input(self):
        assert normalize_tags([]) == []

    def test_dedup_case_insensitive_keeps_first(self):
        result = normalize_tags(["Beach", "beach", "BEACH"])
        assert result == ["beach"]
        assert len(result) == 1


class TestFaceDetection:
    def test_defaults(self):
        d = FaceDetection()
        assert d.image_path == ""
        assert d.bbox_x == 0
        assert d.confidence == 0.0

    def test_custom_values(self):
        d = FaceDetection(
            image_path="/a.jpg", bbox_x=10, bbox_y=20, bbox_w=50, bbox_h=60, confidence=0.99
        )
        assert d.bbox_w == 50
        assert d.confidence == 0.99


class TestPersonCluster:
    def test_defaults(self):
        p = PersonCluster()
        assert p.label == ""
        assert p.confirmed is False
        assert p.face_ids == []

    def test_face_ids_not_shared(self):
        p1 = PersonCluster()
        p2 = PersonCluster()
        p1.face_ids.append(1)
        assert p2.face_ids == []
