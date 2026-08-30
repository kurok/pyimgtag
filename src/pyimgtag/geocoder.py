"""Reverse geocoder using OpenStreetMap Nominatim with disk cache.

Coordinates are rounded to 2 decimal places (~1.1 km at the equator) for
cache keys so that nearby images share a single lookup.

Thread safety: Nominatim's usage policy caps clients at 1 request/second, and
that cap is per *client*, not per object. All network lookups therefore go
through a single module-level lock and a shared monotonic-clock schedule, so
the limit holds process-wide no matter how many :class:`ReverseGeocoder`
instances or ``-j`` worker threads exist. Cache hits never take the lock, and
a double-checked read inside it means threads racing on the same coordinates
issue one request, not N.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path

import requests

from pyimgtag import __version__
from pyimgtag.cache import DiskCache
from pyimgtag.models import GeoResult

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
# Nominatim's usage policy requires an identifying User-Agent; keep the version
# in sync with the package so OSM operators see the real release.
_USER_AGENT = f"pyimgtag/{__version__} (https://github.com/kurok/pyimgtag)"
_CACHE_PRECISION = 2
_MIN_INTERVAL = 1.1  # seconds — Nominatim usage policy
_DEFAULT_CACHE_MAX_SIZE = 10_000  # entries
_DEFAULT_CACHE_TTL_DAYS = 365  # days before a cached result is re-fetched

# Process-wide gate for Nominatim: held for the whole rate-limit + request +
# cache-write sequence so the 1 req/s policy (and the not-thread-safe
# DiskCache) survive any number of geocoder objects and worker threads.
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_TS: float = 0.0


class ReverseGeocoder:
    """Reverse geocoder backed by Nominatim with a JSON disk cache."""

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        max_size: int = _DEFAULT_CACHE_MAX_SIZE,
        ttl_days: int = _DEFAULT_CACHE_TTL_DAYS,
    ) -> None:
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "pyimgtag"
        self._cache = DiskCache(
            Path(cache_dir) / "geocode_cache.json",
            max_size=max_size,
            ttl=timedelta(days=ttl_days),
        )
        self._session = requests.Session()
        self._session.headers["User-Agent"] = _USER_AGENT

    def resolve(self, lat: float | None, lon: float | None) -> GeoResult:
        """Resolve coordinates to a place name.  Returns empty on *None*.

        Safe to call from several threads: cache hits return without blocking,
        while misses queue behind the process-wide 1 req/s Nominatim gate.
        """
        if lat is None or lon is None:
            return GeoResult()

        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            return GeoResult(error=f"GPS coordinates out of range: lat={lat}, lon={lon}")

        key = f"{round(lat, _CACHE_PRECISION)},{round(lon, _CACHE_PRECISION)}"
        hit = self._cached(key)
        if hit is not None:
            return hit

        with _REQUEST_LOCK:
            # Another thread may have fetched these coordinates while we waited
            # for the gate — re-read before spending a request on them.
            hit = self._cached(key)
            if hit is not None:
                return hit
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

    def _cached(self, key: str) -> GeoResult | None:
        cached = self._cache.get(key)
        if cached is None:
            return None
        try:
            return GeoResult(**cached)
        except TypeError:
            return None  # stale cache entry with unexpected keys — re-fetch

    def _fetch(self, lat: float, lon: float) -> GeoResult:
        self._rate_limit()
        params: dict[str, str | float | int] = {
            "lat": lat,
            "lon": lon,
            "format": "json",
            "zoom": 10,
            "addressdetails": 1,
        }
        try:
            resp = self._session.get(_NOMINATIM_URL, params=params, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            return GeoResult(error=f"Geocoding failed: {e}")

        # Decode in a separate block: requests' JSONDecodeError subclasses both
        # RequestException and ValueError, so catching it here (after the network
        # try) gives a distinct, actionable message instead of "Geocoding failed".
        try:
            data = resp.json()
        except ValueError as e:
            return GeoResult(error=f"Geocoding returned invalid JSON for {lat},{lon}: {e}")

        if not isinstance(data, dict):
            # Nominatim normally returns a JSON object; a list or scalar would
            # make the addr.get(...) calls below raise AttributeError and escape
            # resolve()'s documented "always returns a GeoResult" contract.
            return GeoResult(error=f"Geocoding returned unexpected payload for {lat},{lon}")

        if "error" in data:
            # Nominatim reports lookup failures with HTTP 200 and an "error"
            # body; surface it as an error so resolve() never caches it.
            return GeoResult(error=f"Geocoding failed for {lat},{lon}: {data['error']}")

        addr = data.get("address", {})
        return GeoResult(
            nearest_place=addr.get("village") or addr.get("town") or addr.get("suburb"),
            nearest_city=addr.get("city") or addr.get("town") or addr.get("municipality"),
            nearest_region=addr.get("state") or addr.get("region"),
            nearest_country=addr.get("country"),
        )

    def _rate_limit(self) -> None:
        """Sleep until at least ``_MIN_INTERVAL`` has passed since the last request.

        The schedule is a module global, so the spacing is process-wide. Call
        only while holding ``_REQUEST_LOCK``.
        """
        global _LAST_REQUEST_TS
        elapsed = time.monotonic() - _LAST_REQUEST_TS
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _LAST_REQUEST_TS = time.monotonic()

    def close(self) -> None:
        self._session.close()
