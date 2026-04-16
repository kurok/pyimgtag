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
  main.py              CLI entry point, subcommand dispatch (run/status/reprocess/preflight/cleanup)
  models.py            Data classes (ExifData, TagResult, GeoResult, ImageResult)
  scanner.py           Directory and Photos library scanning
  exif_reader.py       EXIF GPS + date (exiftool primary, Pillow fallback)
  ollama_client.py     Ollama vision API client (1 call/image, rich structured response)
  geocoder.py          Nominatim reverse geocoder with JSON disk cache
  filters.py           Date range, GPS, limit filters
  output_writer.py     JSON/CSV/JSONL output
  progress_db.py       SQLite progress DB with versioned migrations (PRAGMA user_version)
  applescript_writer.py  Apple Photos keyword/description write-back via osascript
  dedup.py             Perceptual hash duplicate detection
  heic_converter.py    HEIC to JPEG conversion (macOS sips)
  cache.py             Simple JSON file cache
tests/                 Unit tests (no network, no Ollama required)
```

## Code Style

- ruff for linting & formatting (line length: 100, target py311)
- Type hints on all public functions/methods
- Google-style docstrings on public API; not required on private helpers where the name is self-explanatory
- Import sorting via ruff (isort rules)
- `from __future__ import annotations` for modern type syntax
- Use `X | None` instead of `Optional[X]`

## Branch Naming (Enforced by GitHub Ruleset)

- `feature/` — new features (**NOT** `feat/`)
- `fix/` — bug fixes
- `refactor/` — code refactoring
- `chore/` — maintenance
- `docs/` — documentation
- `test/` — test additions
- `ci/` — CI/CD changes
- `release/` — release prep
- `hotfix/` — urgent production fixes

## Commit Messages (Conventional Commits)

Format: `type(optional-scope): description`

| Type | Semver | Use for |
|---|---|---|
| `feat:` | MINOR | New feature |
| `fix:` | PATCH | Bug fix |
| `perf:` | PATCH | Performance improvement |
| `refactor:` | PATCH | Code change, no new feature/fix |
| `docs:` | — | Documentation only |
| `test:` | — | Tests only |
| `chore:` | — | Maintenance, tooling |
| `ci:` | — | CI/CD changes |
| `style:` | — | Formatting, whitespace |

Rules:
- First line under **72 characters**
- Imperative mood: "add", not "added" or "adds"
- Start description with **lowercase**
- **No period** at end of first line
- Breaking changes: append `!` after type (`feat!:`) or add `BREAKING CHANGE:` footer

## Pull Request Requirements

Enforced by GitHub ruleset:
- 1 approving review required
- Code owner review required for `src/pyimgtag/` and `.github/` (`@kurok`)
- Stale reviews dismissed on new push — re-approval needed
- All review threads resolved before merge
- Required linear history — squash or rebase only, no merge commits
- Status check: `test (ubuntu-latest, 3.12)` must pass
- CodeQL analysis must pass

PR description must use the repo template (`.github/pull_request_template.md`):

```markdown
## Summary
<!-- what and why -->

## Changes
- bullet list of changes

## Related Issues
<!-- Closes #123 -->

## Testing
- [ ] All existing tests pass (`pytest`)
- [ ] New tests added for new functionality
- [ ] Tested manually (describe what you tested)

## Checklist
- [ ] Commit message follows Conventional Commits
- [ ] Code formatted and linted (`ruff format` + `ruff check`)
- [ ] Type checking passes (`mypy`)
- [ ] Pre-commit hooks pass (`pre-commit run --all-files`)
- [ ] No unnecessary files or debug code included
- [ ] Documentation updated if needed
- [ ] No secrets, credentials, or personal paths in code
```

## Testing Rules

- Tests must not require network access, Ollama, or external services
- Tests run in parallel (`pytest-xdist`) — no shared state between tests
- Use `tmp_path` fixture for all filesystem operations
- 85% coverage threshold enforced in CI

## Dependencies

- Keep runtime dependencies minimal (currently: `requests`, `Pillow`, `imagehash`)
- Optional features go in `[project.optional-dependencies]` in `pyproject.toml`
- Guard optional imports with `try/except ImportError`
- New dependencies require justification in the PR description

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
   python -m mypy src/pyimgtag/ --ignore-missing-imports --disable-error-code import-untyped
   python -m bandit -r src/pyimgtag/ -c pyproject.toml
   pre-commit run --all-files
   ```
5. Commit with Conventional Commits: `feat: add x`, `fix: resolve y`
6. Push: `git push -u origin feature/descriptive-name`
7. Create PR using the repo template: `gh pr create --title "type: description"`
8. Monitor CI: `gh run list --branch feature/descriptive-name`

## Important Notes

- CLI uses subcommands: `pyimgtag run`, `pyimgtag status`, `pyimgtag reprocess`, `pyimgtag cleanup`, `pyimgtag preflight`, `pyimgtag query`, `pyimgtag tags`
- `--write-back` flag enables writing tags/description back to Apple Photos via AppleScript
- One Ollama call per image with structured JSON prompt (rich metadata)
- EXIF GPS is source of truth for location — model does not guess location
- Geocoding results cached at ~/.cache/pyimgtag/ (rounded to ~1km)
- Nominatim rate limit: max 1 request/sec
- exiftool preferred for HEIC EXIF; Pillow is fallback
- Never commit `.env` or secrets
- Release: update version in `pyproject.toml` AND `src/pyimgtag/__init__.py`
- Release automation triggers on `v*` tags
- Bandit skips: B404/B603/B607 (exiftool subprocess with fixed args)
