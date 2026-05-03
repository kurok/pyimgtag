# Local screenshot smoke

`test_webapp_screenshots.py` boots the unified pyimgtag webapp in a uvicorn
thread, drives it with a sandboxed Chromium via Playwright, and writes
one PNG per page (and per dropdown / pill / sort option) into
`tests/local/screenshots/<timestamp>/`.

It is **not** run in CI — `pyproject.toml` adds `--ignore=tests/local` to
`addopts`, and the test module also short-circuits when `CI=1` /
`GITHUB_ACTIONS=1` is set.

## One-time setup

```bash
pip install -e '.[review]'
pip install playwright
playwright install chromium
```

## Run

```bash
pytest tests/local/ --override-ini='addopts=' -s
```

`--override-ini='addopts='` is required because the project-wide pytest
config (`pyproject.toml`) sets `addopts = "-n auto --dist=worksteal
--ignore=tests/local"`. Resetting `addopts` to empty:

- skips the auto xdist fan-out (the module-scoped server fixture binds
  a single port; multiple workers would race), and
- drops the directory ignore that hides this folder from `pytest tests/`.

`-s` lets the "[screenshots] writing to …" banner reach your terminal.

## Walk against your real DB

By default the test seeds five fixture images (one judged, one for each
cleanup class, one with text, one error). To screenshot the UI against
your actual progress DB:

```bash
PYIMGTAG_SCREENSHOT_DB=~/.cache/pyimgtag/progress.db \
  pytest tests/local/ -n 0 -p no:xdist -s
```

## What gets captured

| Page | Variants |
| --- | --- |
| `/` (Dashboard) | default |
| `/review/` | default, each of 4 cleanup pills, all 6 Sort options, all 4 per-page options |
| `/faces/` | default |
| `/tags/` | default |
| `/query/` | empty, after Search, all 8 Sort options, every value of every filter select, hover-thumbnail row |
| `/judge/` | default |
| `/about/` | default |

That's roughly 35 PNGs per run. Older runs are kept under
`tests/local/screenshots/` so you can diff successive runs by timestamp.
