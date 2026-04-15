# Contributing to pyimgtag

## Development Setup

```bash
git clone https://github.com/kurok/pyimgtag.git
cd pyimgtag
pip install -e ".[dev,lint,security]"
pre-commit install
```

## Workflow

1. Sync with main: `git fetch origin main && git checkout main && git pull`
2. Create a branch: `git checkout -b feature/descriptive-name`
3. Make changes in `src/pyimgtag/`, add tests in `tests/`
4. Run checks before committing:
   ```bash
   ruff format src/ tests/
   ruff check src/ tests/ --fix
   python -m pytest tests/ -v
   python -m mypy src/pyimgtag/ --ignore-missing-imports
   pre-commit run --all-files
   ```
5. Commit with conventional commits: `feat: add X`, `fix: resolve Y`
6. Push: `git push -u origin feature/descriptive-name`
7. Create PR: `gh pr create --title "type: description"`

## Branch Naming

- `feature/` — new features
- `fix/` — bug fixes
- `refactor/` — code refactoring
- `chore/` — maintenance
- `docs/` — documentation
- `test/` — test additions
- `ci/` — CI/CD changes

## Code Style

- Use ruff for linting and formatting (line length: 100)
- Type hints on public functions
- Google-style docstrings
- Tests required for new functionality
