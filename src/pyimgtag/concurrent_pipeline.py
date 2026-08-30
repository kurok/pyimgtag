"""Bounded, order-preserving worker pool for the tagging and judging loops.

The pipeline exists so that ``run`` and ``judge`` can issue several model
requests at once without giving up any of the guarantees the serial loop
provides:

* **Single writer.** Worker threads only *compute* (EXIF read, HEIC/resize,
  geocode, model call). Every SQLite write, progress print, and output-file
  append happens on the calling (main) thread, exactly as before.
* **Scan order.** Results are handed to ``finalize`` in submission order via a
  reorder buffer keyed by sequence number, so JSON/CSV/JSONL rows and EXIF
  write-back stay deterministic and diffable at any ``-j``.
* **Bounded memory.** At most ``2 * jobs`` items are in flight; a 50k-image
  library never materialises 50k futures.
* **Cooperative pause / interrupt.** The pause gate is honoured before every
  *new* submission (in-flight calls finish, no new ones start), and
  ``KeyboardInterrupt`` cancels everything still queued, drains what is
  already running, finalizes it, and reports ``interrupted=True``.

The module is deliberately free of pyimgtag domain types so it can be tested
with plain fakes.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, TypeVar

T = TypeVar("T")
R = TypeVar("R")

#: Auto (``-j 0``) concurrency for a local/remote Ollama server. Ollama
#: serialises requests per model unless ``OLLAMA_NUM_PARALLEL`` is raised, so a
#: modest default avoids queueing requests that only add latency.
AUTO_JOBS_OLLAMA = 2

#: Upper bound for auto (``-j 0``) concurrency on hosted APIs.
AUTO_JOBS_CLOUD_CAP = 8


class _Skipped:
    """Sentinel for a sequence slot that must not reach ``finalize``."""

    __slots__ = ()


_SKIPPED = _Skipped()


def resolve_jobs(value: Any, backend: str | None = None) -> int:
    """Turn a raw ``--jobs`` value into an effective worker count.

    Args:
        value: The parsed ``--jobs`` value. ``0`` means auto; anything that is
            not a plain ``int`` (e.g. a ``MagicMock`` from a test namespace)
            falls back to ``1`` so the serial path stays the default.
        backend: Vision backend name; ``"ollama"`` gets a smaller auto default
            than the hosted APIs.

    Returns:
        The number of worker threads to use (always ``>= 1``).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return 1
    if value == 0:
        if backend == "ollama":
            return AUTO_JOBS_OLLAMA
        return min(AUTO_JOBS_CLOUD_CAP, os.cpu_count() or 1)
    return max(1, value)


def resolve_max_rps(value: Any) -> float | None:
    """Return a positive requests-per-second cap, or ``None`` for unlimited."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


class RateLimiter:
    """Thread-safe token bucket used as the process-wide ``--max-rps`` valve."""

    def __init__(self, rate: float, burst: float | None = None) -> None:
        """Create a limiter allowing ``rate`` acquisitions per second.

        Args:
            rate: Sustained requests per second; must be positive.
            burst: Bucket capacity. Defaults to ``max(1.0, rate)``.
        """
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._rate = float(rate)
        self._capacity = float(burst) if burst is not None else max(1.0, float(rate))
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until ``tokens`` are available; return the seconds slept."""
        slept = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return slept
                delay = (tokens - self._tokens) / self._rate
            time.sleep(delay)
            slept += delay


_global_limiter: RateLimiter | None = None
_global_limiter_lock = threading.Lock()


def set_global_rate_limit(rps: float | None) -> None:
    """Install (or clear, with ``None``) the process-wide model-request limiter.

    Called once per ``run``/``judge`` invocation from the parsed ``--max-rps``.
    Every vision client acquires from this bucket before its HTTP call, so the
    cap holds across all worker threads regardless of ``-j``.
    """
    global _global_limiter
    with _global_limiter_lock:
        _global_limiter = RateLimiter(rps) if rps and rps > 0 else None


def get_global_rate_limit() -> RateLimiter | None:
    """Return the installed process-wide limiter, if any."""
    with _global_limiter_lock:
        return _global_limiter


def acquire_global_rate_limit() -> None:
    """Block until the process-wide limiter allows one more request."""
    limiter = get_global_rate_limit()
    if limiter is not None:
        limiter.acquire()


def _publish(session: Any, in_flight: int, jobs: int) -> None:
    if session is None:
        return
    session.set_counter("in_flight", in_flight)
    session.set_counter("jobs", jobs)


def run_pipeline(
    items: Iterable[T],
    prepare: Callable[[T], R | None],
    finalize: Callable[[int, T, R], None],
    *,
    jobs: int,
    session: Any = None,
    on_interrupt: Callable[[], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    describe: Callable[[T], str] | None = None,
) -> bool:
    """Run ``prepare`` over ``items`` concurrently, ``finalize`` them in order.

    Args:
        items: Work items, consumed lazily in scan order.
        prepare: Worker-side callable. Must not touch the progress DB or print
            progress. Returning ``None`` means "filtered out" — the item's
            sequence slot is retired without calling ``finalize``.
        finalize: Main-thread callable invoked as ``finalize(seq, item, result)``
            strictly in submission order for every non-``None`` result.
        jobs: Worker-thread count (values below 1 are clamped to 1).
        session: Optional :class:`~pyimgtag.run_session.RunSession`. Its pause
            gate is consulted before each new submission and the ``in_flight``
            / ``jobs`` counters are published for the dashboard.
        on_interrupt: Called once when ``KeyboardInterrupt`` reaches the loop,
            before pending futures are cancelled.
        should_stop: Consulted before each new submission; when it returns
            ``True`` no further items are submitted (already in-flight items
            still complete and are finalized).
        describe: Maps an item to the label published via ``set_current``.

    Returns:
        ``True`` if the run was interrupted, ``False`` if it drained normally.
    """
    jobs = max(1, jobs)
    max_pending = jobs * 2
    iterator = iter(items)
    pending: dict[Future, int] = {}
    items_by_seq: dict[int, T] = {}
    buffer: dict[int, Any] = {}
    next_seq = 0
    submit_seq = 0
    exhausted = False
    interrupted = False

    def _flush() -> None:
        # ``next_seq`` advances *before* the callback so a KeyboardInterrupt
        # raised inside ``finalize`` can neither re-run it nor wedge the buffer.
        nonlocal next_seq
        while next_seq in buffer:
            result = buffer.pop(next_seq)
            item = items_by_seq.pop(next_seq)
            seq = next_seq
            next_seq += 1
            if result is not _SKIPPED and result is not None:
                finalize(seq, item, result)

    executor = ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="pyimgtag-worker")
    try:
        try:
            while True:
                while not exhausted and len(pending) < max_pending:
                    if should_stop is not None and should_stop():
                        exhausted = True
                        break
                    if session is not None:
                        session.wait_if_paused()
                    try:
                        item = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    if session is not None and describe is not None:
                        session.set_current(describe(item))
                    pending[executor.submit(prepare, item)] = submit_seq
                    items_by_seq[submit_seq] = item
                    submit_seq += 1
                if not pending:
                    break
                _publish(session, len(pending), jobs)
                done, _not_done = wait(list(pending), return_when=FIRST_COMPLETED)
                for future in done:
                    buffer[pending.pop(future)] = future.result()
                _flush()
        except KeyboardInterrupt:
            interrupted = True
            if on_interrupt is not None:
                on_interrupt()
            # Drop everything still queued, let running calls land, then
            # finalize every result that did complete so the DB and the output
            # files agree on exactly what was processed.
            executor.shutdown(wait=True, cancel_futures=True)
            for future, seq in pending.items():
                buffer[seq] = _SKIPPED if future.cancelled() else future.result()
            pending.clear()
            _flush()
    finally:
        executor.shutdown(wait=True)
        _publish(session, 0, jobs)
        if session is not None:
            session.set_current(None)
    return interrupted
