"""Tests for ImageResult.build_description."""

from __future__ import annotations

from pyimgtag.models import ImageResult


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
