"""Process-wide registry holding the single active :class:`RunSession`.

Dashboard HTTP handlers read via :func:`get_current`; the CLI loop writes via
:func:`set_current` at startup and clears it at teardown.
"""

from __future__ import annotations

import threading

from pyimgtag.run_session import RunSession

_lock = threading.Lock()
_current: RunSession | None = None


def set_current(session: RunSession | None) -> None:
    global _current
    with _lock:
        _current = session


def get_current() -> RunSession | None:
    with _lock:
        return _current
