"""The one way to serve the unified dashboard in the foreground.

``pyimgtag review``, ``pyimgtag faces ui`` and ``python -m pyimgtag.webapp``
all start the same app; this module holds the uvicorn launch, the
open-in-browser delay and the "port in use" handling they share.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

#: Seconds to wait before opening the browser, so uvicorn is listening first.
BROWSER_OPEN_DELAY = 1.0


def serve_unified(
    db_path: str | Path | None,
    host: str,
    port: int,
    *,
    open_path: str = "/",
    open_browser: bool = False,
    log_level: str = "info",
    banner: str | None = None,
    label: str = "webapp",
    missing_uvicorn: str | None = None,
) -> int:
    """Serve the unified dashboard until interrupted; return a process exit code.

    Args:
        db_path: Progress database to serve (``None``: the default location).
        host: Bind address.
        port: TCP port.
        open_path: Page to open in the browser, e.g. ``"/review"``.
        open_browser: Open ``http://host:port<open_path>`` once the server
            has had :data:`BROWSER_OPEN_DELAY` seconds to start.
        log_level: uvicorn log level.
        banner: Line printed (to stdout) before the server starts, if any.
        label: What to call the server in the "could not start" message,
            and in the default "uvicorn is required" message.
        missing_uvicorn: The whole message printed (to stderr) when uvicorn
            is not installed, if a caller needs its own wording.

    Returns:
        ``0`` after a normal shutdown; ``1`` when uvicorn or FastAPI is not
        installed, or the server could not start (most often the port is
        already in use, or the database cannot be opened).
    """
    try:
        import uvicorn
    except ImportError:
        print(
            missing_uvicorn
            or f"Error: uvicorn is required for the {label}. "
            "Install with: pip install 'pyimgtag[review]'",
            file=sys.stderr,
        )
        return 1
    try:
        from pyimgtag.webapp.unified_app import create_unified_app

        app = create_unified_app(db_path=db_path)
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # The database could not be opened (bad path, permissions, ...).
        print(f"Error: could not start {label} on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    if banner:
        print(banner, flush=True)
    if open_browser:
        url = f"http://{host}:{port}{open_path}"

        def _open() -> None:
            import webbrowser

            time.sleep(BROWSER_OPEN_DELAY)
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001  # nosec B110 — a missing browser is not an error
                pass

        threading.Thread(target=_open, daemon=True).start()

    try:
        uvicorn.run(app, host=host, port=port, log_level=log_level)
    except OSError as exc:
        # Most commonly the port is already in use (EADDRINUSE).
        print(f"Error: could not start {label} on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    return 0
