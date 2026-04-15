"""Reverse geocoder using OpenStreetMap Nominatim with disk cache.

Coordinates are rounded to 2 decimal places (~1.1 km at the equator) for
cache keys so that nearby images share a single lookup.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

from pyimgtag.cache import DiskCache
from pyimgtag.models import GeoResult

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_USER_AGENT = "pyimgtag/0.1.0 (https://github.com/kurok/pyimgtag)"
_CACHE_PRECISION = 2
_MIN_INTERVAL = 1.1  # seconds — Nominatim usage policy


class ReverseGeocoder:
    """Reverse geocoder backed by Nominatim with a JSON disk cache."""

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "pyimgtag"
        self._cache = DiskCache(Path(cache_dir) / "geocode_cache.json")
        self._session = requests.Session()
        self._session.headers["User-Agent"] = _USER_AGENT
        self._last_ts: float = 0

    def resolve(self, lat: float | None, lon: float | None) -> GeoResult:
        """Resolve coordinates to a place name.  Returns empty on *None*."""
        if lat is None or lon is None:
            return GeoResult()

        key = f"{round(lat, _CACHE_PRECISION)},{round(lon, _CACHE_PRECISION)}"
        cached = self._cache.get(key)
        if cached is not None:
            return GeoResult(**cached)

        result = self._fetch(lat, lon)
        if result.error is None:
            self._cache.set(
                key,
                {
                    "nearest_place": result.nearest_place,
                    "nearest_city": result.nearest_city,
                    "nearest_region": result.nearest_region,
                    "nearest_country": result.nearest_country,
                },
            )
        return result

    def _fetch(self, lat: float, lon: float) -> GeoResult:
        self._rate_limit()
        try:
            resp = self._session.get(
                _NOMINATIM_URL,
                params={"lat": lat, "lon": lon, "format": "json", "zoom": 10, "addressdetails": 1},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return GeoResult(error=f"Geocoding failed: {e}")

        addr = data.get("address", {})
        return GeoResult(
            nearest_place=addr.get("village") or addr.get("town") or addr.get("suburb"),
            nearest_city=addr.get("city") or addr.get("town") or addr.get("municipality"),
            nearest_region=addr.get("state") or addr.get("region"),
            nearest_country=addr.get("country"),
        )

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_ts
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_ts = time.monotonic()

    def close(self) -> None:
        self._session.close()
