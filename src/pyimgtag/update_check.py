"""PyPI update-check helpers shared by the CLI and the web UI.

Looks up the latest released pyimgtag version on PyPI and compares it
against the installed one. The lookup is best-effort (3-second timeout,
no retry) and cached for an hour so callers (the CLI startup banner and
the dashboard nav badge) never pay an HTTP round-trip on every
invocation or page refresh.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_PYPI_URL = "https://pypi.org/pypi/pyimgtag/json"
_CACHE_TTL_SECONDS = 3600.0
_CACHE: dict[str, Any] = {"at": 0.0, "value": None}


def _parse_version(s: str) -> tuple[int, ...]:
    """Tolerant version-tuple parser.

    Handles ``0.10.0`` (3 ints) and ``1.2`` (2 ints). Anything non-numeric
    in a segment short-circuits that segment to 0 so a pre-release suffix
    never crashes the compare.
    """
    parts: list[int] = []
    for raw in s.split("."):
        digits = ""
        for ch in raw:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(latest: str, installed: str) -> bool:
    """True iff ``latest`` > ``installed``.

    Tuples of different lengths are padded with trailing zeros before the
    compare so ``0.18.0`` and ``0.18`` are treated as the same release.
    """
    a = _parse_version(latest)
    b = _parse_version(installed)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def _fetch_latest_pypi(timeout: float = 3.0) -> str | None:
    """Return the current latest released version on PyPI, or None on failure."""
    try:
        import requests
    except ImportError:
        return None
    try:
        resp = requests.get(_PYPI_URL, timeout=timeout)
        resp.raise_for_status()
        info = resp.json().get("info") or {}
        version = info.get("version")
        return version if isinstance(version, str) and version else None
    except (requests.RequestException, ValueError) as exc:
        logger.debug("PyPI version lookup failed: %s", exc)
        return None


def latest_pypi_version(now: float | None = None) -> str | None:
    """Return the latest version, hitting PyPI at most once per hour."""
    if now is None:
        now = time.monotonic()
    if _CACHE["value"] is not None and (now - _CACHE["at"]) < _CACHE_TTL_SECONDS:
        return _CACHE["value"]  # type: ignore[return-value]
    fresh = _fetch_latest_pypi()
    if fresh is not None:
        _CACHE["value"] = fresh
        _CACHE["at"] = now
    return fresh


def reset_cache() -> None:
    """Drop the cached PyPI version so the next lookup re-fetches."""
    _CACHE["value"] = None
    _CACHE["at"] = 0.0
