# pyimgtag

macOS Photos library image tagger using local Gemma model via Ollama.

## Tech Stack

- Python 3.11+, Ollama (Gemma 3 vision model), Pillow
- `pyproject.toml` with src layout
- Optional extras: [dev], [lint], [security]

## Commands

```bash
# Install (editable + all dev)
pip install -e ".[dev,lint,security]"

# Test
pytest tests/ -v
pytest tests/test_main.py -v                    # specific file
pytest tests/test_main.py::TestMain -v           # specific class
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

- `src/pyimgtag/` — main package (src layout)
- `tests/` — unit + integration tests
- `examples/` — usage examples

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

- Never commit `.env` or secrets
- Release: update version in `pyproject.toml` AND `src/pyimgtag/__init__.py`
- Release automation triggers on `v*` tags
- Tests run parallel — no shared state dependencies
