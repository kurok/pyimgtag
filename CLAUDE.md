# pyimgtag

macOS image tagger using local Ollama Gemma vision model with EXIF GPS reverse geocoding.

## Tech Stack

- Python 3.11+, requests, Pillow
- Ollama HTTP API (gemma4:e4b default model)
- Nominatim reverse geocoding with disk cache
- Optional: pillow-heif (HEIC), exiftool (reliable HEIC EXIF)
- `pyproject.toml` with src layout
- Optional extras: [heic], [all], [dev], [lint], [security]

## Commands

```bash
# Install (editable + all dev)
pip install -e ".[dev,lint,security]"

# Test
pytest tests/ -v
pytest tests/test_main.py -v                    # specific file
pytest tests/test_main.py::TestBuildParser -v    # specific class
pytest -n 0 -v                                   # sequential (debugging)

# Lint & format
ruff format src/ tests/
ruff check src/ tests/ --fix

# Type check
python -m mypy src/pyimgtag/ --ignore-missing-imports --disable-error-code import-untyped

# Security
python -m bandit -r src/pyimgtag/ -c pyproject.toml
python -m pip_audit

# Pre-commit
pre-commit run --all-files
```

## Architecture

```
src/pyimgtag/
  main.py           CLI entry point and orchestration
  models.py         Data classes (ExifData, TagResult, GeoResult, ImageResult)
  scanner.py        Directory and Photos library scanning
  exif_reader.py    EXIF GPS + date (exiftool primary, Pillow fallback)
  ollama_client.py  Ollama vision API client (1 call/image, compact prompt)
  geocoder.py       Nominatim reverse geocoder with JSON disk cache
  filters.py        Date range, GPS, limit filters
  output_writer.py  JSON/CSV/JSONL output
  cache.py          Simple JSON file cache
tests/              Unit tests (no network, no Ollama required)
```

## Code Style

- ruff for linting & formatting (line length: 100, target py311)
- Type hints on all public functions/methods
- Google-style docstrings
- Import sorting via ruff (isort rules)

## Branch Naming (Enforced)

- `feature/` — new features (**NOT** `feat/`)
- `fix/` — bug fixes
- `refactor/` — code refactoring
- `chore/` — maintenance
- `docs/` — documentation
- `test/` — test additions
- `ci/` — CI/CD changes
- `release/` — release prep
- `hotfix/` — urgent production fixes

## Pull Request Requirements

- 1 approving review required
- All review threads resolved
- Required linear history (squash/rebase merges only)
- Status check: `test (ubuntu-latest, 3.12)` must pass

## CI Pipeline (5 Jobs)

1. lint — ruff format check + lint
2. pre-commit — hooks
3. test — matrix (ubuntu + macos) x (py3.11, 3.12, 3.13) with coverage (85% threshold)
4. typecheck — mypy
5. security — bandit + pip-audit

## Workflow: Creating a Clean PR

1. Sync: `git fetch origin main && git checkout main && git pull`
2. Create: `git checkout -b feature/descriptive-name`
3. Make changes in `src/pyimgtag/`, add tests in `tests/`
4. Run all checks before committing:
   ```bash
   ruff format src/ tests/
   ruff check src/ tests/ --fix
   python -m pytest tests/ -v
   python -m mypy src/pyimgtag/ --ignore-missing-imports
   pre-commit run --all-files
   ```
5. Commit with Conventional Commits: `feat: add X`, `fix: resolve Y`
6. Push: `git push -u origin feature/descriptive-name`
7. Create PR: `gh pr create --title "type: description"`
8. Monitor: `gh run list --branch feature/descriptive-name`

## Important Notes

- MVP is read-only — never writes metadata back to images
- One Ollama call per image with compact JSON prompt (low token usage)
- EXIF GPS is source of truth for location — model does not guess location
- Geocoding results cached at ~/.cache/pyimgtag/ (rounded to ~1km)
- Nominatim rate limit: max 1 request/sec
- exiftool preferred for HEIC EXIF; Pillow is fallback
- Never commit `.env` or secrets
- Release: update version in `pyproject.toml` AND `src/pyimgtag/__init__.py`
- Release automation triggers on `v*` tags
- Tests run parallel — no shared state dependencies
- Bandit skips: B404/B603/B607 (exiftool subprocess with fixed args)
