"""Helpers for end-to-end tests that drive the real dashboard.

These run ``python -m pyimgtag.webapp`` as a child process against a database
in ``tmp_path``, with ``HOME`` pointed at ``tmp_path`` so nothing touches the
developer's real cache. The dashboard binds to a free loopback port; no test
reaches the network or Ollama.
"""

from __future__ import annotations

import os
import socket
import subprocess  # nosec B404 — the tests run the package's own entry points
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import requests

_SRC = Path(__file__).resolve().parent.parent / "src"


def child_env(home: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment for a child process isolated under *home*."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(_SRC), env.get("PYTHONPATH")]))
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYIMGTAG_DB", None)
    if extra:
        env.update(extra)
    return env


def free_port() -> int:
    """Return a TCP port on 127.0.0.1 that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def dashboard(db_path: Path, home: Path, timeout: float = 60.0, attempts: int = 3) -> Iterator[str]:
    """Serve the dashboard for *db_path*; yield its base URL until the block exits.

    ``free_port`` can race with another xdist worker grabbing the same port
    before the child binds it, so a child that exits during startup is
    retried on a fresh port.
    """
    for attempt in range(attempts):
        try:
            proc, base = _start_dashboard(db_path, home, timeout)
        except _ExitedEarly:
            if attempt == attempts - 1:
                raise
            continue
        try:
            yield base
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(10)
        return


class _ExitedEarly(RuntimeError):
    """The dashboard child exited before it answered ``/health``."""


def _start_dashboard(
    db_path: Path, home: Path, timeout: float
) -> tuple[subprocess.Popen[bytes], str]:
    port = free_port()
    env = child_env(
        home,
        {
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "PYIMGTAG_DB": str(db_path),
            "PYIMGTAG_LOG_LEVEL": "warning",
        },
    )
    log = home / f"dashboard-{port}.log"
    with open(log, "w", encoding="utf-8") as out:
        proc = subprocess.Popen(  # nosec B603 — fixed interpreter and module
            [sys.executable, "-m", "pyimgtag.webapp"],
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout
    while True:
        if proc.poll() is not None:
            raise _ExitedEarly(f"dashboard exited early:\n{log.read_text('utf-8')}")
        try:
            if requests.get(f"{base}/health", timeout=1).status_code == 200:
                return proc, base
        except requests.RequestException:
            pass
        if time.monotonic() > deadline:
            proc.kill()
            proc.wait(10)
            raise RuntimeError(f"dashboard did not start:\n{log.read_text('utf-8')}")
        time.sleep(0.1)
