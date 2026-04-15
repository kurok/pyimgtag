# pyimgtag

[![CI](https://github.com/kurok/pyimgtag/actions/workflows/python-package.yml/badge.svg)](https://github.com/kurok/pyimgtag/actions/workflows/python-package.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Tag macOS Photos library images using a local Gemma model for searchable tags.

## Overview

pyimgtag uses a locally-running Gemma model (via [Ollama](https://ollama.ai)) to analyze images in your macOS Photos library and generate descriptive tags. These tags enable powerful search across your photo collection without relying on cloud services.

**Key features:**

- Runs entirely on-device — no cloud, no data leaves your Mac
- Uses Gemma 3 vision model via Ollama for image understanding
- Reads directly from the macOS Photos library
- Stores tags in a local SQLite database for fast search
- Batch processing with configurable concurrency

## Requirements

- macOS (Photos library access)
- Python 3.11+
- [Ollama](https://ollama.ai) installed and running
- Gemma 3 model pulled: `ollama pull gemma3:4b`

## Quick Start

```bash
# Install
pip install pyimgtag

# Pull the model
ollama pull gemma3:4b

# Tag untagged photos
pyimgtag tag

# Search by tags
pyimgtag search "sunset beach"

# Check status
pyimgtag status
```

## Installation

### From PyPI

```bash
pip install pyimgtag
```

### From source

```bash
git clone https://github.com/kurok/pyimgtag.git
cd pyimgtag
pip install -e ".[dev]"
```

## Usage

### Tag images

```bash
# Tag all untagged images
pyimgtag tag

# Dry run — see what would be tagged
pyimgtag --dry-run tag

# Use a larger model for better accuracy
pyimgtag --model gemma3:12b tag

# Process in larger batches
pyimgtag --batch-size 50 tag
```

### Search images

```bash
# Search by tag
pyimgtag search "mountain landscape"

# Search with verbose output
pyimgtag -v search "dog park"
```

### Status and export

```bash
# Show tagging statistics
pyimgtag status

# Export tag database
pyimgtag export
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev,lint,security]"

# Run tests
pytest

# Lint and format
ruff format src/ tests/
ruff check src/ tests/ --fix

# Type check
python -m mypy src/pyimgtag/ --ignore-missing-imports

# Security scan
python -m bandit -r src/pyimgtag/ -c pyproject.toml
python -m pip_audit

# Pre-commit hooks
pre-commit install
pre-commit run --all-files
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT — see [LICENSE](LICENSE).
