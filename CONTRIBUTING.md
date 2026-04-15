# Contributing to pyimgtag

Thank you for your interest in contributing to pyimgtag! This guide covers everything you need to know to get started.

## Getting Started

### Prerequisites

- macOS (primary platform) or Linux
- Python 3.11 or newer
- Git
- [Ollama](https://ollama.ai) (for running the tool, not required for tests)
- Optional: `exiftool` (`brew install exiftool` on macOS)

### Development Setup

```bash
# Clone the repository
git clone https://github.com/kurok/pyimgtag.git
cd pyimgtag

# Create a virtual environment (recommended)
python3 -m venv .venv && source .venv/bin/activate

# Install in development mode with all extras
pip install -e ".[dev,lint,security]"

# Install pre-commit hooks
pre-commit install

# Verify everything works
pytest
```

### Project Structure

```
pyimgtag/
  src/pyimgtag/
    main.py           CLI entry point and orchestration
    models.py         Data classes
    scanner.py        Directory and Photos library scanning
    exif_reader.py    EXIF GPS + date extraction
    ollama_client.py  Ollama vision API client
    geocoder.py       Nominatim reverse geocoder with disk cache
    filters.py        Date/GPS filter logic
    output_writer.py  JSON/CSV/JSONL output
    cache.py          Simple JSON disk cache
  tests/              Unit tests (no network, no Ollama required)
  .github/workflows/  CI/CD pipelines
```

## How to Contribute

### Reporting Bugs

1. Check existing [issues](https://github.com/kurok/pyimgtag/issues) to avoid duplicates
2. Open a new issue using the **Bug Report** template
3. Include your Python version, macOS version, and steps to reproduce
4. If it involves a specific image format, mention the camera/device

### Suggesting Features

1. Check existing [issues](https://github.com/kurok/pyimgtag/issues) for similar ideas
2. Open a new issue using the **Feature Request** template
3. Describe the use case and expected behavior
4. Explain how it fits with the tool's scope (local-first, privacy-preserving image tagging)

### Submitting Code

1. **Create a branch** from `main` (see [Branch Naming](#branch-naming) below)
2. **Make your changes** -- keep commits focused and atomic
3. **Add tests** for new functionality
4. **Run the full check suite** before pushing (see [Pre-push Checklist](#pre-push-checklist))
5. **Push** and open a **Pull Request**
6. **Wait for CI** and code review

## Branch Naming

Branch names are **enforced by a GitHub ruleset**. You must use one of these prefixes:

| Prefix | Use for |
|---|---|
| `feature/` | New features |
| `fix/` | Bug fixes |
| `refactor/` | Code refactoring |
| `chore/` | Maintenance, dependency updates |
| `docs/` | Documentation changes |
| `test/` | Test additions or changes |
| `ci/` | CI/CD changes |
| `release/` | Release preparation |
| `hotfix/` | Urgent production fixes |

**Important:** Do NOT use `feat/` -- only `feature/` is accepted.

Examples:
```bash
git checkout -b feature/add-tiff-support
git checkout -b fix/heic-exif-parsing
git checkout -b docs/update-readme-examples
```

## Commit Messages

This project follows the [Conventional Commits](https://www.conventionalcommits.org/) specification (semver 2.0). Commit messages determine automated version bumps.

### Format

```
type(optional-scope): description

optional body

optional footer
```

### Types and Semver Mapping

| Type | Semver | Description |
|---|---|---|
| `feat:` | MINOR | New feature |
| `fix:` | PATCH | Bug fix |
| `perf:` | PATCH | Performance improvement |
| `refactor:` | PATCH | Code change that neither fixes a bug nor adds a feature |
| `docs:` | -- | Documentation only |
| `test:` | -- | Adding or updating tests |
| `chore:` | -- | Maintenance, tooling, dependency updates |
| `ci:` | -- | CI/CD changes |
| `build:` | -- | Build system changes |
| `style:` | -- | Formatting, whitespace (no code change) |
| `revert:` | -- | Revert a previous commit |

### Breaking Changes

Append `!` after the type to indicate a breaking change. This triggers a MAJOR version bump.

```
feat!: redesign output schema for v2
fix(exif)!: change GPS coordinate precision to 6 decimal places
```

Or include `BREAKING CHANGE:` in the commit footer:

```
feat: add new output format

BREAKING CHANGE: removed legacy CSV column "location_string"
```

### Examples

```
feat: add TIFF format support
fix: handle missing EXIF date in HEIC files
perf: reduce image resize overhead by caching dimensions
docs: add HEIC troubleshooting to README
chore(deps): update Pillow to 11.0
ci: add Python 3.14 to test matrix
refactor(geocoder): extract rate limiter into separate class
```

### Rules

- First line under 72 characters
- Use imperative mood ("add", not "added" or "adds")
- Start description with lowercase
- No period at the end of the first line

## Pull Request Process

### Requirements (Enforced by Ruleset)

- **1 approving review** required
- **Stale reviews dismissed** on new push -- re-approval needed after changes
- **Code owner review** required for core paths (`src/pyimgtag/`, `.github/`)
- **Last push approval** required -- the person who pushes last cannot self-approve
- **All review threads** must be resolved before merge
- **Required linear history** -- squash or rebase merges only (no merge commits)
- **CI must be green** -- the `test (ubuntu-latest, 3.12)` status check must pass
- **CodeQL** analysis must pass (high or higher severity threshold)
- No force pushes or branch deletion on `main`

### PR Guidelines

- Reference related issues (e.g., `Closes #42`)
- Describe **what** changed and **why**
- Keep PRs focused -- one feature or fix per PR
- Fill in the PR template completely
- Make sure CI is green before requesting review
- Respond to review feedback promptly

## Pre-push Checklist

Run all checks locally before pushing:

```bash
# Format and lint
ruff format src/ tests/
ruff check src/ tests/ --fix

# Tests
pytest -v

# Type checking
python -m mypy src/pyimgtag/ --ignore-missing-imports --disable-error-code import-untyped

# Security scan
python -m bandit -r src/pyimgtag/ -c pyproject.toml

# Pre-commit hooks (catches trailing whitespace, YAML issues, etc.)
pre-commit run --all-files
```

All of these run in CI, so catching issues locally saves round-trips.

## CI Pipeline

Every push and PR triggers 5 parallel CI jobs:

| Job | What it checks |
|---|---|
| **lint** | `ruff format --check` + `ruff check` |
| **pre-commit** | Trailing whitespace, EOF, YAML, JSON, merge conflicts, ruff |
| **test** | Pytest matrix: (Ubuntu + macOS) x (Python 3.11, 3.12, 3.13), 85% coverage threshold |
| **typecheck** | mypy strict checking |
| **security** | bandit + pip-audit (dependency vulnerabilities) |

## Code Guidelines

### Style

- **Formatter/linter:** ruff (line length: 100, target Python 3.11)
- Follow existing patterns in the codebase
- Keep functions focused -- one function, one job
- No unnecessary abstractions; simple and direct is better
- No over-engineering for hypothetical future requirements

### Type Hints

- Type hints required on all public functions and methods
- Use `from __future__ import annotations` for modern syntax
- Use `X | None` instead of `Optional[X]`

### Docstrings

- Google-style docstrings on public API
- Not required on private helpers where the name is self-explanatory
- Do not add docstrings to code you did not change

### Testing

- **Tests required** for all new features and bug fixes
- Tests run in parallel (`pytest-xdist`) -- ensure no shared state between tests
- Tests must not require network access, Ollama, or external services
- Use `tmp_path` fixture for file system tests
- Run specific tests during development:
  ```bash
  pytest tests/test_scanner.py -v             # specific file
  pytest tests/test_scanner.py::TestScanDirectory -v  # specific class
  pytest -n 0 -v                              # sequential (for debugging)
  ```

### Dependencies

- Keep runtime dependencies minimal (currently: `requests`, `Pillow`, `imagehash`)
- Optional dependencies go in `[project.optional-dependencies]`
- Guard optional imports with `try/except ImportError`
- New dependencies need justification in the PR description

## Release Process

Releases are managed by the maintainers:

1. Version is bumped in `pyproject.toml` and `src/pyimgtag/__init__.py`
2. A git tag is created: `git tag v0.2.0`
3. Tag push triggers automated GitHub Release + PyPI publish via GitHub Actions
4. The release workflow verifies the tag version matches `pyproject.toml`

Contributors do not need to bump versions -- maintainers handle this during the release cycle.

## Response Times

This project is maintained on a best-effort basis. You can generally expect:

- **Issue triage:** within 7 days
- **Pull request review:** within 7 days
- **Security reports:** acknowledgment within 48 hours (see [SECURITY.md](SECURITY.md))

If a PR or issue hasn't received a response after 7 days, feel free to leave a polite ping.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior via GitHub Issues or by contacting the maintainers directly.

## Questions?

Open an [issue](https://github.com/kurok/pyimgtag/issues) with the question label. We are happy to help!

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
