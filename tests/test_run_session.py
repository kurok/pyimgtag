"""Tests for RunSession state machine, pause gate, and snapshot."""

from __future__ import annotations

import threading
import time

from pyimgtag.run_session import RunSession, RunState


class TestStateTransitions:
    def test_new_session_starts_in_starting(self):
        s = RunSession(command="run")
        assert s.snapshot()["state"] == RunState.STARTING.value

    def test_mark_running_moves_to_running(self):
        s = RunSession(command="run")
        s.mark_running()
        assert s.snapshot()["state"] == "running"

    def test_terminal_states_are_sticky(self):
        s = RunSession(command="run")
        s.mark_running()
        s.mark_completed()
        s.mark_running()  # should not override terminal
        assert s.snapshot()["state"] == "completed"

    def test_mark_failed_records_last_error(self):
        s = RunSession(command="run")
        s.mark_failed("boom")
        snap = s.snapshot()
        assert snap["state"] == "failed"
        assert snap["last_error"] == "boom"

    def test_mark_interrupted_is_terminal(self):
        s = RunSession(command="run")
        s.mark_running()
        s.mark_interrupted()
        s.mark_running()
        assert s.snapshot()["state"] == "interrupted"

    def test_run_id_is_stable_across_snapshots(self):
        s = RunSession(command="run")
        assert s.snapshot()["run_id"] == s.snapshot()["run_id"]

    def test_command_propagates_to_snapshot(self):
        s = RunSession(command="judge")
        assert s.snapshot()["command"] == "judge"


class TestPauseGate:
    def test_wait_if_paused_is_noop_when_running(self):
        s = RunSession(command="run")
        s.mark_running()
        start = time.monotonic()
        s.wait_if_paused(timeout=0.05)
        assert time.monotonic() - start < 0.05

    def test_request_pause_sets_pausing_then_paused_on_wait(self):
        s = RunSession(command="run")
        s.mark_running()
        s.request_pause()
        assert s.snapshot()["state"] == "pausing"

        released = threading.Event()

        def worker():
            s.wait_if_paused()
            released.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.05)
        assert s.snapshot()["state"] == "paused"
        assert not released.is_set()

        s.resume()
        assert released.wait(timeout=1.0) is True
        assert s.snapshot()["state"] == "running"

    def test_resume_is_idempotent(self):
        s = RunSession(command="run")
        s.mark_running()
        s.resume()  # no-op
        assert s.snapshot()["state"] == "running"

    def test_pause_is_ignored_in_terminal_state(self):
        s = RunSession(command="run")
        s.mark_completed()
        s.request_pause()
        assert s.snapshot()["state"] == "completed"

    def test_mark_completed_releases_waiters(self):
        s = RunSession(command="run")
        s.mark_running()
        s.request_pause()

        released = threading.Event()

        def worker():
            s.wait_if_paused()
            released.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.05)
        s.mark_completed()
        assert released.wait(timeout=1.0) is True


class TestCountersAndRecent:
    def test_set_counter_overwrites(self):
        s = RunSession(command="run")
        s.set_counter("scanned", 10)
        s.set_counter("scanned", 12)
        assert s.snapshot()["counters"]["scanned"] == 12

    def test_increment_accumulates(self):
        s = RunSession(command="run")
        s.increment("processed")
        s.increment("processed", by=2)
        assert s.snapshot()["counters"]["processed"] == 3

    def test_set_current_and_clear(self):
        s = RunSession(command="run")
        s.set_current("/tmp/a.jpg")
        assert s.snapshot()["current_item"] == "/tmp/a.jpg"
        s.set_current(None)
        assert s.snapshot()["current_item"] is None

    def test_record_item_appends_to_recent(self):
        s = RunSession(command="run")
        s.record_item("/tmp/a.jpg", "ok")
        s.record_item("/tmp/b.jpg", "error", error="io failure")
        snap = s.snapshot()
        assert [e["path"] for e in snap["recent"]] == ["/tmp/a.jpg", "/tmp/b.jpg"]
        assert snap["recent"][1]["status"] == "error"
        assert snap["recent"][1]["error"] == "io failure"
        assert snap["last_error"] == "io failure"

    def test_recent_buffer_is_bounded(self):
        s = RunSession(command="run")
        for i in range(40):
            s.record_item(f"/tmp/{i}.jpg", "ok")
        assert len(s.snapshot()["recent"]) == 25

    def test_concurrent_increment_is_safe(self):
        s = RunSession(command="run")

        def worker():
            for _ in range(200):
                s.increment("k")

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert s.snapshot()["counters"]["k"] == 1000
