"""Tests for ReverseGeocoder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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

    def test_non_dict_payload_returns_error(self, tmp_path):
        # Nominatim normally returns an object; a JSON list must not crash
        # resolve() with an AttributeError but degrade to an error GeoResult.
        geo = ReverseGeocoder(cache_dir=tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = ["unexpected", "list"]
        mock_resp.raise_for_status.return_value = None
        with patch.object(geo._session, "get", return_value=mock_resp):
            with patch("time.sleep"):
                result = geo.resolve(48.85, 2.35)
        assert result.error is not None
        assert "unexpected payload" in result.error
        assert result.nearest_place is None

    def test_error_payload_returns_error_and_is_not_cached(self, tmp_path):
        # Nominatim signals lookup failure with HTTP 200 and a body like
        # {"error": "Unable to geocode"}. It must map to an error GeoResult
        # and never be written to the disk cache as a successful lookup.
        geo = ReverseGeocoder(cache_dir=tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "Unable to geocode"}
        mock_resp.raise_for_status.return_value = None
        with patch.object(geo._session, "get", return_value=mock_resp):
            with patch("time.sleep"):
                result = geo.resolve(45.0, -30.0)
        assert result.error is not None
        assert "Unable to geocode" in result.error
        assert result.nearest_place is None
        assert geo._cache.get("45.0,-30.0") is None

    def test_invalid_json_returns_error(self, tmp_path):
        # requests' Response.json() raises requests.exceptions.JSONDecodeError
        # (a subclass of BOTH RequestException and ValueError) on a malformed
        # body. Use the real type so this proves the decode branch is actually
        # reached and not shadowed by the network error handler above it.
        import requests as req

        geo = ReverseGeocoder(cache_dir=tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.side_effect = req.exceptions.JSONDecodeError("Expecting value", "doc", 0)
        mock_resp.raise_for_status.return_value = None
        with patch.object(geo._session, "get", return_value=mock_resp):
            with patch("time.sleep"):
                result = geo.resolve(48.85, 2.35)
        assert result.error is not None
        assert "invalid JSON" in result.error

    def test_close_session(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        geo.close()  # must not raise


class TestInit:
    def test_default_cache_dir_uses_home(self, tmp_path):
        # With no cache_dir, the geocoder falls back to ~/.cache/pyimgtag.
        # Patch Path.home so the test never touches the real home directory.
        with patch("pyimgtag.geocoder.Path.home", return_value=tmp_path):
            geo = ReverseGeocoder()
        expected = tmp_path / ".cache" / "pyimgtag" / "geocode_cache.json"
        assert geo._cache._path == expected
        geo.close()


class TestRateLimit:
    def test_rate_limit_sleeps_when_called_too_soon(self, tmp_path, monkeypatch):
        import time as _time

        geo = ReverseGeocoder(cache_dir=tmp_path)
        # Pretend the last request was just now so the next call must wait.
        # The schedule is process-wide (module global), not per instance.
        monkeypatch.setattr("pyimgtag.geocoder._LAST_REQUEST_TS", _time.monotonic())
        with patch("pyimgtag.geocoder.time.sleep") as mock_sleep:
            geo._rate_limit()
        mock_sleep.assert_called_once()
        # The requested sleep is a positive fraction of the min interval.
        assert mock_sleep.call_args[0][0] > 0
        geo.close()


class TestCacheBehaviour:
    def test_cache_hit_skips_network(self, tmp_path):
        """Second resolve with same coords must not call the network."""
        geo = ReverseGeocoder(cache_dir=tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"address": {"city": "Paris", "country": "France"}}
        mock_resp.raise_for_status.return_value = None

        with patch.object(geo._session, "get", return_value=mock_resp) as mock_get:
            with patch("time.sleep"):
                geo.resolve(48.85, 2.35)
                geo.resolve(48.85, 2.35)  # same coords → same rounded key

        assert mock_get.call_count == 1

    def test_stale_cached_dict_refetches(self, tmp_path):
        """A cached entry with unexpected keys falls back to a network fetch."""
        geo = ReverseGeocoder(cache_dir=tmp_path)
        # Inject a malformed cache entry (unknown field 'city_id' triggers TypeError).
        key = "48.85,2.35"
        geo._cache._data[key] = {"city_id": 999, "nearest_place": "Paris"}

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"address": {"city": "Paris", "country": "France"}}
        mock_resp.raise_for_status.return_value = None

        with patch.object(geo._session, "get", return_value=mock_resp):
            with patch("time.sleep"):
                result = geo.resolve(48.85, 2.35)

        assert result.error is None


class TestConcurrentRateLimit:
    """Nominatim's 1 req/s policy must hold process-wide, whatever ``-j`` is."""

    class _FakeClock:
        """Stand-in for the ``time`` module: sleeping advances a virtual clock."""

        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            # Only ever called while _REQUEST_LOCK is held, so this is safe.
            self.now += seconds

    def test_requests_stay_one_second_apart_under_j8(self, tmp_path, monkeypatch):
        import threading

        import pyimgtag.geocoder as geo_mod

        clock = self._FakeClock()
        monkeypatch.setattr(geo_mod, "time", clock)
        monkeypatch.setattr(geo_mod, "_LAST_REQUEST_TS", 0.0)

        stamps: list[float] = []
        geo = ReverseGeocoder(cache_dir=tmp_path)

        def fake_get(url, params=None, timeout=None):
            stamps.append(clock.now)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"address": {"city": "Somewhere"}}
            return resp

        monkeypatch.setattr(geo._session, "get", fake_get)

        # Eight distinct coordinates so nothing is served from cache.
        coords = [(10.0 + i, 20.0 + i) for i in range(8)]
        threads = [threading.Thread(target=geo.resolve, args=c) for c in coords]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(stamps) == 8
        stamps.sort()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert all(gap >= 1.0 for gap in gaps), gaps
        geo.close()

    def test_cache_hits_do_not_wait_for_the_gate(self, tmp_path, monkeypatch):
        import threading

        import pyimgtag.geocoder as geo_mod

        clock = self._FakeClock()
        monkeypatch.setattr(geo_mod, "time", clock)
        monkeypatch.setattr(geo_mod, "_LAST_REQUEST_TS", 0.0)

        calls: list[float] = []
        geo = ReverseGeocoder(cache_dir=tmp_path)

        def fake_get(url, params=None, timeout=None):
            calls.append(clock.now)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"address": {"city": "Somewhere"}}
            return resp

        monkeypatch.setattr(geo._session, "get", fake_get)

        # 16 threads over the same coordinates: one lookup, no extra spacing.
        threads = [threading.Thread(target=geo.resolve, args=(1.0, 2.0)) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(calls) == 1
        assert clock.now == pytest.approx(1.1)
        geo.close()
