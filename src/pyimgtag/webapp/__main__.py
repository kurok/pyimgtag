"""Run the unified pyimgtag webapp standalone via uvicorn.

Used by ``scripts/test-smoke-local.sh`` and the
``.github/workflows/pr-tests.yml`` CI workflow as the dashboard target
for end-to-end Playwright smoke tests.

Configuration is read from environment variables so that local runs
and CI runs use the same launch surface:

- ``HOST`` (default ``127.0.0.1``)
- ``PORT`` (default ``8000``)
- ``PYIMGTAG_DB`` — optional path to an existing progress DB; otherwise
  the default ``~/.cache/pyimgtag/progress.db`` is used.
- ``PYIMGTAG_LOG_LEVEL`` — uvicorn log level (default ``info``).

Run:

    HOST=127.0.0.1 PORT=8000 python -m pyimgtag.webapp
"""

from __future__ import annotations

import os


def main() -> None:
    from pyimgtag.webapp.serve import serve_unified

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    log_level = os.environ.get("PYIMGTAG_LOG_LEVEL", "info")
    db_path = os.environ.get("PYIMGTAG_DB") or None

    code = serve_unified(
        db_path,
        host,
        port,
        log_level=log_level,
        label="dashboard",
        missing_uvicorn=(
            "uvicorn is required to launch the dashboard standalone. "
            "Install with: pip install 'pyimgtag[review]'"
        ),
    )
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
