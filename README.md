# pyimgtag

[![CI](https://github.com/kurok/pyimgtag/actions/workflows/python-package.yml/badge.svg)](https://github.com/kurok/pyimgtag/actions/workflows/python-package.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Tag macOS Photos library images using a local Gemma model for searchable tags.

## Overview

pyimgtag uses a locally-running Gemma model (via [Ollama](https://ollama.ai)) to
analyse images and generate 1-5 descriptive tags per photo.  It reads EXIF GPS
coordinates and resolves them to the nearest city/place using OpenStreetMap
Nominatim.  Everything runs on-device -- no cloud, no data leaves your Mac.

**Key features:**

- One local model call per image, compact prompt, low token usage
- EXIF GPS as source of truth for location (never guessed from image content)
- Open reverse geocoding via Nominatim with local disk cache
- Supports exported folders and Apple Photos library originals (best-effort)
- Dry-run mode, date/limit filters, JSON/CSV export
- Never modifies image files (read-only MVP)

## Requirements

- macOS (Apple Silicon recommended)
- Python 3.11+
- [Ollama](https://ollama.ai) installed and running
- Gemma 4 model pulled: `ollama pull gemma4:e4b`
- Optional: `exiftool` for reliable HEIC EXIF (falls back to Pillow)
- Optional: `pillow-heif` for HEIC image loading

## Quick Start

```bash
pip install -e ".[dev]"

# Pull the model
ollama pull gemma4:e4b

# Dry-run on an exported folder, first 20 images
pyimgtag --input-dir ~/Pictures/exported --limit 20 --dry-run

# Single date
pyimgtag --input-dir ~/Pictures/exported --date 2026-04-01 --dry-run

# Date range with JSON output
pyimgtag --input-dir ~/Pictures/exported \
  --date-from 2026-03-01 --date-to 2026-03-31 \
  --output-json results.json

# Photos library (best-effort)
pyimgtag --photos-library ~/Pictures/Photos\ Library.photoslibrary \
  --limit 50 --dry-run
```

## Installation

```bash
# From source
git clone https://github.com/kurok/pyimgtag.git
cd pyimgtag
pip install -e ".[dev]"

# Optional HEIC support
pip install pillow-heif

# Optional exiftool (better EXIF for HEIC)
brew install exiftool
```

## Usage

### Input sources

```bash
# Exported image folder (primary)
pyimgtag --input-dir /path/to/photos

# Apple Photos library package (best-effort, reads originals/)
pyimgtag --photos-library ~/Pictures/Photos\ Library.photoslibrary
```

### Filters

```bash
--limit N              # Max images to process
--date YYYY-MM-DD      # Single date
--date-from YYYY-MM-DD # Start of range
--date-to YYYY-MM-DD   # End of range
--extensions jpg,png    # File types (default: jpg,jpeg,heic,png)
--skip-no-gps          # Skip images without GPS data
```

### Output

```bash
--dry-run              # Verbose per-file output, no writes
--output-json out.json # Write results to JSON
--output-csv out.csv   # Write results to CSV
--jsonl-stdout         # Machine-readable JSONL to stdout
--verbose / -v         # Detailed per-file output
```

### Model options

```bash
--model gemma4:e12b    # Use a different model
--ollama-url http://localhost:11434  # Ollama API URL
--max-dim 1280         # Max image dimension before sending to model
--timeout 120          # Model request timeout (seconds)
```

### Sample dry-run output

```
[1/50] sunset_beach.jpg
  Path:     /Users/me/Pictures/exported/sunset_beach.jpg
  Date:     2026-04-01 14:30:00
  Tags:     sunset, beach, ocean, waves, sand
  Summary:  sunset at the beach
  GPS:      37.7749, -122.4194
  Location: San Francisco, California, United States
  Status:   ok

--- Summary ---
  Scanned:          200
  Processed:        50
  Skipped (date):   0
  Skipped (no GPS): 0
  Skipped (no file):0
  Model failures:   2
  Geocode failures: 0
```

### Output schema

Each result (JSON/CSV) includes:

| Field | Description |
|---|---|
| `file_path` | Full path to image |
| `file_name` | Filename |
| `source_type` | `directory` or `photos_library` |
| `is_local` | Whether file is locally available |
| `image_date` | EXIF or file date |
| `tags` | 1-5 vision model tags |
| `scene_summary` | Optional short summary |
| `gps_lat` / `gps_lon` | EXIF GPS coordinates |
| `nearest_place` | Village/town/suburb |
| `nearest_city` | City |
| `nearest_region` | State/region |
| `nearest_country` | Country |
| `processing_status` | `ok`, `skipped`, or `error` |
| `error_message` | Error details if any |

## Architecture

```
src/pyimgtag/
  main.py           CLI entry point and orchestration
  models.py         Data classes (ExifData, TagResult, GeoResult, ImageResult)
  scanner.py        Directory and Photos library scanning
  exif_reader.py    EXIF GPS + date extraction (exiftool + Pillow)
  ollama_client.py  Ollama vision API client
  geocoder.py       Nominatim reverse geocoder with disk cache
  filters.py        Date/GPS filter logic
  output_writer.py  JSON/CSV/JSONL output
  cache.py          Simple JSON disk cache
```

## Development

```bash
pip install -e ".[dev,lint,security]"

pytest tests/ -v
ruff format src/ tests/ && ruff check src/ tests/ --fix
python -m mypy src/pyimgtag/ --ignore-missing-imports --disable-error-code import-untyped
python -m bandit -r src/pyimgtag/ -c pyproject.toml
pre-commit install && pre-commit run --all-files
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT -- see [LICENSE](LICENSE).
