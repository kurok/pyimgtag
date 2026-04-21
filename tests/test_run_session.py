"""Tests for RunSession state machine, pause gate, and snapshot."""

from __future__ import annotations

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
