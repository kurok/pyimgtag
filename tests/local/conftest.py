"""Local-only pytest config for the screenshot smoke.

These tests boot a real uvicorn server in a thread and drive it with a
sandboxed Chromium via Playwright. They are intentionally **not** run in
CI — `pyproject.toml` adds `--ignore=tests/local` to `addopts`. Run them
with:

    pytest tests/local/ -n 0 -p no:xdist

The `-n 0` (or `-p no:xdist`) is required because the module-scoped
server fixture binds a single port; xdist's worker fan-out would spin up
a server per worker and confuse the test runner.
"""

from __future__ import annotations
