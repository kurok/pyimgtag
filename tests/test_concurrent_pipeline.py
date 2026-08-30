"""Unit tests for :mod:`pyimgtag.concurrent_pipeline` (no pyimgtag domain types)."""

from __future__ import annotations

import threading
import time

import pytest

from pyimgtag.concurrent_pipeline import (
    AUTO_JOBS_CLOUD_CAP,
    AUTO_JOBS_OLLAMA,
    RateLimiter,
    acquire_global_rate_limit,
    get_global_rate_limit,
    resolve_jobs,
    resolve_max_rps,
    run_pipeline,
    set_global_rate_limit,
)


class TestResolveJobs:
    def test_default_and_explicit(self):
        assert resolve_jobs(1) == 1
        assert resolve_jobs(6) == 6
        assert resolve_jobs(-3) == 1

    def test_auto_is_backend_aware(self):
        assert resolve_jobs(0, "ollama") == AUTO_JOBS_OLLAMA
        assert 1 <= resolve_jobs(0, "anthropic") <= AUTO_JOBS_CLOUD_CAP

    def test_non_int_falls_back_to_serial(self):
        # argparse Namespaces built from MagicMock in tests must not be
        # interpreted as a concurrency request.
        from unittest.mock import MagicMock

        assert resolve_jobs(MagicMock()) == 1
        assert resolve_jobs(True) == 1
        assert resolve_jobs("4") == 1


class TestResolveMaxRps:
    def test_values(self):
        from unittest.mock import MagicMock

        assert resolve_max_rps(None) is None
        assert resolve_max_rps(0) is None
        assert resolve_max_rps(-1) is None
        assert resolve_max_rps(2.5) == 2.5
        assert resolve_max_rps(MagicMock()) is None
        assert resolve_max_rps(True) is None


class TestRateLimiter:
    def test_rejects_non_positive_rate(self):
        with pytest.raises(ValueError, match="positive"):
            RateLimiter(0)

    def test_spaces_acquisitions(self):
        limiter = RateLimiter(rate=50.0, burst=1.0)
        limiter.acquire()  # drains the burst token
        start = time.monotonic()
        for _ in range(3):
            limiter.acquire()
        # 3 tokens at 50/s = at least 60 ms of waiting.
        assert time.monotonic() - start >= 0.05

    def test_global_limiter_set_and_clear(self):
        assert get_global_rate_limit() is None
        set_global_rate_limit(10.0)
        assert isinstance(get_global_rate_limit(), RateLimiter)
        acquire_global_rate_limit()  # must not raise
        set_global_rate_limit(None)
        assert get_global_rate_limit() is None
        acquire_global_rate_limit()  # no-op when unset


class TestRunPipeline:
    def test_finalizes_in_scan_order_despite_jittered_latency(self):
        # Reverse-ordered sleeps: without a reorder buffer the last item would
        # be finalized first.
        items = list(range(12))
        seen: list[int] = []

        def prepare(i: int) -> str:
            time.sleep((12 - i) * 0.005)
            return f"r{i}"

        def finalize(seq: int, item: int, result: str) -> None:
            assert seq == item
            seen.append(item)

        assert run_pipeline(items, prepare, finalize, jobs=4) is False
        assert seen == items

    def test_none_results_are_dropped_without_stalling_order(self):
        seen: list[int] = []
        run_pipeline(
            range(6),
            lambda i: None if i % 2 else f"r{i}",
            lambda _seq, item, _res: seen.append(item),
            jobs=3,
        )
        assert seen == [0, 2, 4]

    def test_submission_is_bounded(self):
        # The pipeline must never materialise every future up front.
        submitted = 0
        lock = threading.Lock()
        peak = 0
        live = 0

        def prepare(i: int) -> int:
            nonlocal peak, live
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.005)
            with lock:
                live -= 1
            return i

        def items():
            nonlocal submitted
            for i in range(50):
                submitted += 1
                yield i

        run_pipeline(items(), prepare, lambda *_a: None, jobs=3)
        assert submitted == 50
        assert peak <= 3  # never more than `jobs` running at once

    def test_single_worker_still_works(self):
        seen: list[int] = []
        run_pipeline(range(4), lambda i: i * 2, lambda _s, _i, r: seen.append(r), jobs=1)
        assert seen == [0, 2, 4, 6]

    def test_should_stop_halts_new_submissions(self):
        prepared: list[int] = []

        def prepare(i: int) -> int:
            prepared.append(i)
            return i

        finalized: list[int] = []

        run_pipeline(
            range(100),
            prepare,
            lambda _s, _i, r: finalized.append(r),
            jobs=2,
            should_stop=lambda: len(finalized) >= 4,
        )
        # Bounded overshoot only: whatever was already in flight completes.
        assert len(finalized) >= 4
        assert len(prepared) < 100

    def test_pause_gate_blocks_new_submissions_and_lets_inflight_finish(self):
        from pyimgtag.run_session import RunSession

        session = RunSession(command="run")
        session.mark_running()
        started = threading.Event()
        release = threading.Event()
        prepared: list[int] = []

        def prepare(i: int) -> int:
            prepared.append(i)
            if i == 0:
                started.set()
                release.wait(timeout=5)
            return i

        finalized: list[int] = []
        done = threading.Event()

        def runner() -> None:
            run_pipeline(
                range(20),
                prepare,
                lambda _s, _i, r: finalized.append(r),
                jobs=1,
                session=session,
            )
            done.set()

        thread = threading.Thread(target=runner)
        thread.start()
        try:
            assert started.wait(timeout=5)
            session.request_pause()
            release.set()  # the in-flight call finishes
            time.sleep(0.2)
            paused_count = len(prepared)
            time.sleep(0.2)
            # No new submissions while paused (the in-flight one may land).
            assert len(prepared) == paused_count
            assert paused_count < 20
            session.resume()
            assert done.wait(timeout=10)
            assert finalized == list(range(20))
        finally:
            session.resume()
            thread.join(timeout=10)

    def test_publishes_in_flight_and_jobs_counters(self):
        from pyimgtag.run_session import RunSession

        session = RunSession(command="run")
        session.mark_running()
        run_pipeline(
            range(6),
            lambda i: i,
            lambda *_a: None,
            jobs=3,
            session=session,
            describe=str,
        )
        snap = session.snapshot()
        assert snap["counters"]["jobs"] == 3
        assert snap["counters"]["in_flight"] == 0  # reset on exit
        assert snap["current_item"] is None

    def test_interrupt_finalizes_completed_and_cancels_the_rest(self):
        # KeyboardInterrupt is raised on the main thread out of finalize, just
        # as a real Ctrl-C would land there.
        finalized: list[int] = []
        prepared: list[int] = []
        calls = {"n": 0}

        def prepare(i: int) -> int:
            prepared.append(i)
            return i

        def finalize(_seq: int, _item: int, result: int) -> None:
            calls["n"] += 1
            if calls["n"] == 3:
                raise KeyboardInterrupt
            finalized.append(result)

        interrupted_flag = {"seen": False}

        def note_interrupt() -> None:
            interrupted_flag["seen"] = True

        interrupted = run_pipeline(
            range(200),
            prepare,
            finalize,
            jobs=2,
            on_interrupt=note_interrupt,
        )
        assert interrupted is True
        assert interrupted_flag["seen"] is True
        # Everything already computed is still finalized, in order, and the
        # item whose finalize raised is never retried.
        assert finalized[:2] == [0, 1]
        assert 2 not in finalized
        assert finalized == sorted(finalized)
        assert len(prepared) < 200  # the tail was never submitted

    def test_worker_exception_propagates(self):
        def prepare(i: int) -> int:
            if i == 2:
                raise RuntimeError("boom")
            return i

        with pytest.raises(RuntimeError, match="boom"):
            run_pipeline(range(5), prepare, lambda *_a: None, jobs=1)
