"""Live run session state shared between CLI workers and the dashboard HTTP server."""

from __future__ import annotations

import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Any

_RECENT_CAPACITY = 25


class RunState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


_TERMINAL: frozenset[RunState] = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.INTERRUPTED}
)


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class RunSession:
    """Process-local state of a single CLI run.

    All state mutations go through ``_lock``; the pause gate uses a separate
    ``threading.Event`` so HTTP handlers never block the lock while a worker
    is paused.
    """

    def __init__(
        self,
        command: str,
        *,
        run_id: str | None = None,
        web_url: str | None = None,
    ) -> None:
        self.run_id = run_id or _new_run_id()
        self.command = command
        self.started_at = _utcnow_iso()
        self.web_url = web_url
        self._lock = threading.Lock()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._state = RunState.STARTING
        self._counters: dict[str, int] = {}
        self._current_item: str | None = None
        self._last_error: str | None = None
        self._recent: deque[dict[str, Any]] = deque(maxlen=_RECENT_CAPACITY)

    # -- state transitions -------------------------------------------------

    def mark_running(self) -> None:
        with self._lock:
            if self._state in _TERMINAL:
                return
            self._state = RunState.RUNNING

    def mark_completed(self) -> None:
        with self._lock:
            if self._state in _TERMINAL:
                return
            self._state = RunState.COMPLETED
        self._resume_event.set()

    def mark_failed(self, err: str) -> None:
        with self._lock:
            if self._state in _TERMINAL:
                return
            self._state = RunState.FAILED
            self._last_error = err
        self._resume_event.set()

    def mark_interrupted(self) -> None:
        with self._lock:
            self._state = RunState.INTERRUPTED
        self._resume_event.set()

    # -- pause gate --------------------------------------------------------

    def request_pause(self) -> None:
        with self._lock:
            if self._state in _TERMINAL or self._state in {
                RunState.PAUSING,
                RunState.PAUSED,
            }:
                return
            self._state = RunState.PAUSING
            self._resume_event.clear()

    def resume(self) -> None:
        with self._lock:
            if self._state in _TERMINAL:
                return
            self._state = RunState.RUNNING
        self._resume_event.set()

    def wait_if_paused(self, timeout: float | None = None) -> None:
        """Block while the session is paused.

        Call before starting any expensive work unit. Returns when the
        session is running, completed, failed, or interrupted. If ``timeout``
        is given and expires while paused, returns without changing state so
        callers can re-check cancellation flags.
        """
        while True:
            with self._lock:
                if self._state == RunState.PAUSING:
                    self._state = RunState.PAUSED
                state = self._state
            if state != RunState.PAUSED:
                return
            if self._resume_event.wait(timeout=timeout):
                return
            if timeout is not None:
                return

    # -- counters & progress ---------------------------------------------

    def set_counter(self, key: str, value: int) -> None:
        with self._lock:
            self._counters[key] = value

    def increment(self, key: str, by: int = 1) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + by

    def set_current(self, path: str | None) -> None:
        with self._lock:
            self._current_item = path

    def record_item(
        self,
        path: str,
        status: str,
        error: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "path": path,
            "status": status,
            "at": _utcnow_iso(),
        }
        if error:
            entry["error"] = error
        with self._lock:
            self._recent.append(entry)
            if error:
                self._last_error = error

    # -- snapshot ---------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": self.run_id,
                "command": self.command,
                "state": self._state.value,
                "counters": dict(self._counters),
                "current_item": self._current_item,
                "started_at": self.started_at,
                "last_error": self._last_error,
                "web_url": self.web_url,
                "recent": list(self._recent),
            }
