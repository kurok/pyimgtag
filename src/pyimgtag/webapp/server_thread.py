"""Run the dashboard FastAPI app on a background daemon thread."""

from __future__ import annotations

import threading
import time
from typing import Any


class DashboardServer:
    """Manage a :class:`uvicorn.Server` on a daemon thread.

    Caller owns startup and shutdown lifecycle; failures during import are
    surfaced as ``ImportError`` from the constructor so the CLI can fall
    back to terminal-only mode.
    """

    def __init__(self, app: Any, host: str = "127.0.0.1", port: int = 8770) -> None:
        try:
            import uvicorn
        except ImportError as exc:
            raise ImportError(
                "uvicorn is required for the dashboard. "
                "Install with: pip install 'pyimgtag[review]'"
            ) from exc

        self.host = host
        self.port = port
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="pyimgtag-dashboard",
            daemon=True,
        )

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, ready_timeout: float = 5.0) -> bool:
        """Start the thread and wait until uvicorn reports started.

        Returns True if ready within ``ready_timeout``, False otherwise.
        """
        self._thread.start()
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            if getattr(self._server, "started", False):
                return True
            time.sleep(0.05)
        return False

    def stop(self, timeout: float = 3.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=timeout)
