"""Compatibility entry point for the review UI.

The review pages are part of the unified dashboard; :func:`serve` starts it
and opens ``/review``. The old standalone ``create_app`` is gone.
"""

from __future__ import annotations

from pathlib import Path

from pyimgtag.webapp.serve import serve_unified


def serve(
    db_path: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> int:
    """Serve the dashboard and open the review page; return an exit code.

    Args:
        db_path: Path to the SQLite progress DB.
        host: Bind host (default: 127.0.0.1).
        port: Bind port (default: 8765).
        open_browser: Open the default browser automatically.
    """
    return serve_unified(
        db_path,
        host,
        port,
        open_path="/review",
        open_browser=open_browser,
        banner=f"pyimgtag webapp: http://{host}:{port} (review at /review)",
        label="review server",
        missing_uvicorn=(
            "Error: uvicorn is required for the review UI. "
            "Install with: pip install 'pyimgtag[review]'"
        ),
    )
