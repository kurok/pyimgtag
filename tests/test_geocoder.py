"""Tests for ReverseGeocoder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pyimgtag.geocoder import ReverseGeocoder


class TestResolveNone:
    def test_resolve_none_lat(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        result = geo.resolve(None, 0.0)
        assert result.error is None
        assert result.nearest_place is None

    def test_resolve_none_lon(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        result = geo.resolve(0.0, None)
        assert result.error is None
        assert result.nearest_place is None

    def test_resolve_both_none(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        result = geo.resolve(None, None)
        assert result.error is None
        assert result.nearest_place is None


class TestResolveOutOfRange:
    def test_resolve_out_of_range_lat(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        result = geo.resolve(91.0, 0.0)
        assert result.error is not None
        assert "out of range" in result.error

    def test_resolve_out_of_range_lat_negative(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        result = geo.resolve(-91.0, 0.0)
        assert result.error is not None
        assert "out of range" in result.error

    def test_resolve_out_of_range_lon(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        result = geo.resolve(0.0, 181.0)
        assert result.error is not None
        assert "out of range" in result.error

    def test_resolve_out_of_range_lon_negative(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        result = geo.resolve(0.0, -181.0)
        assert result.error is not None
        assert "out of range" in result.error

    def test_resolve_boundary_lat_valid(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"address": {"country": "Test"}}
        mock_resp.raise_for_status.return_value = None
        with patch.object(geo._session, "get", return_value=mock_resp):
            result = geo.resolve(90.0, 0.0)
        assert result.error is None

    def test_resolve_boundary_lon_valid(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"address": {"country": "Test"}}
        mock_resp.raise_for_status.return_value = None
        with patch.object(geo._session, "get", return_value=mock_resp):
            result = geo.resolve(0.0, 180.0)
        assert result.error is None


class TestFetchErrors:
    def test_request_exception_returns_error_geo_result(self, tmp_path):
        import requests as req

        geo = ReverseGeocoder(cache_dir=tmp_path)
        with patch.object(
            geo._session, "get", side_effect=req.RequestException("connection refused")
        ):
            with patch("time.sleep"):
                result = geo.resolve(48.85, 2.35)
        assert result.error is not None
        assert "Geocoding failed" in result.error
        assert result.nearest_place is None

    def test_close_session(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        geo.close()  # must not raise
