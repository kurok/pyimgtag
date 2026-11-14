"""HTTP POST with retry on 429 / 503, shared by every model client.

A busy or rate-limited backend (a cloud API, or a remote or overloaded
Ollama) answers 429 or 503; those are retried with exponential backoff
plus jitter, honouring ``Retry-After``. Anything else, including a 500, is
returned to the caller at once. Network errors propagate unchanged.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from pyimgtag.concurrent_pipeline import acquire_global_rate_limit

logger = logging.getLogger(__name__)

# Statuses worth retrying: 429 is the providers' rate-limit signal, 503 their
# "overloaded, come back" signal. Anything else is a real failure.
RETRY_STATUSES = frozenset({429, 503})
MAX_ATTEMPTS = 5
_BACKOFF_BASE = 1.0  # seconds
_BACKOFF_CAP = 30.0  # seconds
_JITTER_FRACTION = 0.25
# SystemRandom keeps bandit's B311 quiet; the jitter only needs to be
# uncorrelated across workers, not unpredictable.
_jitter = random.SystemRandom()


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP-date) to seconds."""
    if not value:
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _retry_after_seconds(resp: Any) -> float | None:
    """Extract the ``Retry-After`` delay from *resp*, or ``None`` if absent."""
    headers: Any = getattr(resp, "headers", None)
    value = headers.get("Retry-After") if headers is not None and hasattr(headers, "get") else None
    return _parse_retry_after(value) if isinstance(value, str) else None


def _backoff_delay(attempt: int, retry_after: float | None = None) -> float:
    """Seconds to wait before retry *attempt* (1-based).

    A server-supplied ``Retry-After`` wins and is used as-is (capped); otherwise
    the delay doubles per attempt from :data:`_BACKOFF_BASE` up to
    :data:`_BACKOFF_CAP`, plus up to 25% jitter so parallel workers do not
    re-collide in lockstep.
    """
    if retry_after is not None:
        return min(retry_after, _BACKOFF_CAP)
    base = min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** (attempt - 1)))
    return base + _jitter.uniform(0.0, base * _JITTER_FRACTION)


def post_with_retry(
    session: Any,
    url: str,
    *,
    json: Any,
    timeout: float,
    attempts: int = MAX_ATTEMPTS,
    label: str = "backend",
) -> Any:
    """POST *json* to *url*, retrying 429 and 503 with backoff.

    Every attempt first takes a token from the process-wide ``--max-rps``
    bucket (a no-op when unset). After the last attempt the final response is
    returned as-is, still 429/503, so the caller can word its own error.

    Args:
        session: A ``requests.Session`` (or anything with a compatible ``post``).
        url: Endpoint to POST to.
        json: Request body.
        timeout: Per-attempt timeout in seconds.
        attempts: Total attempts, including the first.
        label: Backend name for the debug log line.

    Returns:
        The last response received.

    Raises:
        requests.RequestException: On a network error; not retried.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    resp: Any = None
    for attempt in range(1, attempts + 1):
        acquire_global_rate_limit()
        resp = session.post(url, json=json, timeout=timeout)
        status = getattr(resp, "status_code", None)
        if status not in RETRY_STATUSES or attempt == attempts:
            return resp
        delay = _backoff_delay(attempt, _retry_after_seconds(resp))
        logger.debug(
            "%s HTTP %s on attempt %d/%d; retrying in %.2fs",
            label,
            status,
            attempt,
            attempts,
            delay,
        )
        time.sleep(delay)
    return resp
