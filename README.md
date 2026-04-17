# pyimgtag

[![CI](https://github.com/kurok/pyimgtag/actions/workflows/python-package.yml/badge.svg)](https://github.com/kurok/pyimgtag/actions/workflows/python-package.yml)
[![CodeQL](https://github.com/kurok/pyimgtag/actions/workflows/codeql.yml/badge.svg)](https://github.com/kurok/pyimgtag/actions/workflows/codeql.yml)
[![PyPI version](https://img.shields.io/pypi/v/pyimgtag)](https://pypi.org/project/pyimgtag/)
[![Python versions](https://img.shields.io/pypi/pyversions/pyimgtag)](https://pypi.org/project/pyimgtag/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![codecov](https://codecov.io/gh/kurok/pyimgtag/graph/badge.svg)](https://codecov.io/gh/kurok/pyimgtag)

Tag images using a local Gemma model for searchable tags, with optional Apple Photos integration on macOS.

## Overview

pyimgtag uses a locally-running Gemma model (via [Ollama](https://ollama.ai)) to
analyse images and generate 1-5 descriptive tags per photo.  It reads EXIF GPS
coordinates and resolves them to the nearest city/place using OpenStreetMap
Nominatim.  Everything runs on-device -- no cloud, no data leaves your computer.

Works on **macOS, Linux, and Windows**. Apple Photos integration (write-back) is macOS-only.

**Key features:**

- One local model call per image, compact prompt, low token usage
- Rich AI metadata: scene category, emotional tone, cleanup classification, text detection, event hints
- EXIF GPS as source of truth for location (never guessed from image content)
- Open reverse geocoding via Nominatim with local disk cache
- Supports exported folders and Apple Photos library originals (macOS only)
- Apple Photos write-back: push AI tags and descriptions back as keywords/captions (macOS only)
- Subcommands: `run`, `judge`, `status`, `reprocess`, `cleanup`, `preflight`, `query`, `tags`
- Photo quality scoring with professional 13-criterion rubric (new: `judge` subcommand)
- Dry-run mode, date/limit filters, JSON/CSV export
- SQLite progress DB with schema versioning for incremental re-runs

## Requirements

- Python 3.11+
- [Ollama](https://ollama.ai) installed and running
- Gemma 4 model pulled: `ollama pull gemma4:e4b`

**macOS-specific:**
- Apple Silicon or Intel Mac
- Optional: `exiftool` for reliable HEIC EXIF (falls back to Pillow)
- Optional: `pillow-heif` for HEIC image loading

**All platforms:**
- Works on macOS, Linux, and Windows
- EXIF writing via `exiftool` (if installed) works across platforms
- Apple Photos write-back requires macOS

## Quick Start

```bash
pip install -e ".[dev]"

# Pull the model
ollama pull gemma4:e4b

# Dry-run on an exported folder, first 20 images
pyimgtag run --input-dir ~/Pictures/exported --limit 20 --dry-run

# Single date
pyimgtag run --input-dir ~/Pictures/exported --date 2026-04-01 --dry-run

# Date range with JSON output
pyimgtag run --input-dir ~/Pictures/exported \
  --date-from 2026-03-01 --date-to 2026-03-31 \
  --output-json results.json

# Photos library
pyimgtag run --photos-library ~/Pictures/Photos\ Library.photoslibrary \
  --limit 50 --dry-run

# Check processing progress
pyimgtag status

# Re-tag all photos (e.g. after prompt improvements)
pyimgtag reprocess

# List photos flagged for deletion
pyimgtag cleanup

# Score photos by quality (judge)
pyimgtag judge --input-dir ~/Pictures/exported --limit 20 --verbose

# Filter to only strong photos, save ranking to JSON
pyimgtag judge --input-dir ~/Pictures/exported \
  --min-score 3.5 --output-json ranking.json
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

## Platform Support

| Feature | macOS | Linux | Windows |
|---------|-------|-------|---------|
| Image tagging via Ollama | ✅ | ✅ | ✅ |
| EXIF reading (GPS, dates) | ✅ | ✅ | ✅ |
| Reverse geocoding (Nominatim) | ✅ | ✅ | ✅ |
| EXIF writing via `exiftool` | ✅ | ✅ | ✅ |
| Apple Photos library scanning | ✅ | ❌ | ❌ |
| Apple Photos write-back | ✅ | ❌ | ❌ |

**Note:** Most features work cross-platform. Apple Photos integration is macOS-only since it requires macOS-specific AppleScript functionality.

### Cross-Platform Examples

**Linux/Windows (export folders only):**
```bash
# Tag exported images with EXIF writing
pyimgtag run --input-dir /mnt/photos \
  --output-json results.json \
  --write-exif  # If exiftool is installed

# Tags and descriptions stored in results.json and EXIF
```

**macOS (both export folders and Photos library):**
```bash
# Tag Photos library with direct write-back to Photos app
pyimgtag run --photos-library ~/Pictures/Photos\ Library.photoslibrary \
  --write-back  # Push tags/descriptions to Apple Photos

# Or export folder with both EXIF and JSON output
pyimgtag run --input-dir ~/Downloads/exported \
  --write-exif --output-json results.json
```

The tool gracefully handles missing features—if you use `--write-back` on Linux/Windows, it will warn you and proceed without it.

## Usage

### Subcommands

pyimgtag uses subcommands. Run `pyimgtag --help` for the full list.

#### `pyimgtag run` — tag images

```bash
# Exported image folder
pyimgtag run --input-dir /path/to/photos

# Apple Photos library
pyimgtag run --photos-library ~/Pictures/Photos\ Library.photoslibrary

# With filters
pyimgtag run --input-dir /path/to/photos \
  --limit 100 --date-from 2026-03-01 --date-to 2026-03-31

# Write tags back to Apple Photos as keywords
pyimgtag run --photos-library ~/Pictures/Photos\ Library.photoslibrary \
  --write-back --limit 10

# Deduplicate by perceptual hash
pyimgtag run --input-dir /path/to/photos --dedup

# Export to JSON
pyimgtag run --input-dir /path/to/photos --output-json results.json
```

**Run flags:**

| Flag | Description |
|---|---|
| `--input-dir PATH` | Exported image folder |
| `--photos-library PATH` | Apple Photos library package *(macOS only)* |
| `--limit N` | Max images to process |
| `--date YYYY-MM-DD` | Single date filter |
| `--date-from` / `--date-to` | Date range filter |
| `--extensions jpg,png` | File types (default: jpg,jpeg,heic,png) |
| `--skip-no-gps` | Skip images without GPS data |
| `--dry-run` | Verbose output, no DB writes |
| `--verbose` / `-v` | Detailed per-file output |
| `--output-json FILE` | Write results to JSON |
| `--output-csv FILE` | Write results to CSV |
| `--jsonl-stdout` | JSONL output to stdout |
| `--write-back` | Write tags/description back to Apple Photos *(macOS only)* |
| `--write-exif` | Write description and keywords to image EXIF |
| `--dedup` | Skip duplicates via perceptual hash |
| `--dedup-threshold N` | Hamming distance threshold (default: 5) |
| `--model NAME` | Ollama model (default: gemma4:e4b) |
| `--ollama-url URL` | Ollama API URL |
| `--max-dim N` | Max image dimension (default: 1280) |
| `--timeout N` | Model request timeout in seconds |
| `--db PATH` | Progress database path |
| `--no-cache` | Skip progress DB, reprocess all |

#### `pyimgtag status` — check progress

```bash
# Show processing stats
pyimgtag status

# Output:
# Progress: 142 / 200 (71%)
#   ok:      140
#   error:   2
#   pending: 58
```

#### `pyimgtag reprocess` — reset for re-tagging

```bash
# Reset everything (e.g. after prompt improvements)
pyimgtag reprocess

# Reset only failed entries
pyimgtag reprocess --status error
```

#### `pyimgtag cleanup` — find photos to delete

```bash
# List photos the AI flagged as "delete"
pyimgtag cleanup

# Also include "review" (uncertain) candidates
pyimgtag cleanup --include-review

# Output:
# Cleanup candidates (delete): 12
#
#   [delete]  /path/to/blurry_photo.jpg  | 2026-03-15  | tags: blurry, dark
#   [delete]  /path/to/screenshot.png    | 2026-04-01  | tags: screenshot, text
```

#### `pyimgtag query` — search tagged images

```bash
# Search by tag
pyimgtag query --tag sunset

# Search by location
pyimgtag query --location "San Francisco"

# Output as JSON
pyimgtag query --tag beach --output-json matches.json
```

#### `pyimgtag tags` — manage tags

```bash
# List all tags with image counts
pyimgtag tags list

# Rename a tag across all images
pyimgtag tags rename old-name new-name

# Delete a tag from all images
pyimgtag tags delete unwanted-tag --dry-run

# Merge one tag into another
pyimgtag tags merge source-tag target-tag
```

#### `pyimgtag preflight` — check prerequisites

```bash
# Verify Ollama, model, and source path
pyimgtag preflight --input-dir ~/Pictures/exported
```

#### `pyimgtag judge` — score photo quality

Score each image against a 13-criterion professional rubric. Outputs a ranked list with weighted scores on a 1–5 scale. Requires Ollama.

```bash
# Score all images in a folder
pyimgtag judge --input-dir ~/Pictures/exported

# Only show photos scoring 3.5 or above
pyimgtag judge --input-dir ~/Pictures/exported --min-score 3.5

# Verbose breakdown (per-criterion scores)
pyimgtag judge --input-dir ~/Pictures/exported --limit 20 --verbose

# Sort by filename instead of score
pyimgtag judge --input-dir ~/Pictures/exported --sort-by name

# Score Photos library
pyimgtag judge --photos-library ~/Pictures/Photos\ Library.photoslibrary \
  --limit 50 --min-score 4.0

# Save full ranking to JSON
pyimgtag judge --input-dir ~/Pictures/exported \
  --output-json ranking.json
```

**Sample output (brief mode):**
```
[1/5] golden_hour.jpg → 4.32/5 strong | + impact, composition_center | - edit_integrity, noise_cleanliness
  Golden light over the cityscape; strong composition but slight haloing on edges.
[2/5] portrait.jpg → 3.87/5 solid | + focus_sharpness, lighting | - creativity_style, color_mood
  Well-lit portrait; technically solid but conventional treatment.
```

**Sample output (--verbose):**
```
[1/5] golden_hour.jpg
  Score:   4.32/5  (core: 4.55, visible: 3.90)
  Best:    impact=5, composition_center=5, lighting=4
  Weakest: edit_integrity=3, noise_cleanliness=3, subject_separation=3
  Verdict: Golden light over the cityscape; strong composition but slight haloing on edges.
```

**Judge flags:**

| Flag | Default | Description |
|---|---|---|
| `--input-dir PATH` | — | Exported image folder |
| `--photos-library PATH` | — | Apple Photos library *(macOS only)* |
| `--limit N` | unlimited | Max images to score |
| `--extensions EXT,...` | `jpg,jpeg,heic,png,tiff,webp` | File types |
| `--min-score SCORE` | — | Only show images scoring ≥ SCORE |
| `--sort-by score\|name` | `score` | Final sort order |
| `--output-json FILE` | — | Write ranked results to JSON |
| `--verbose` | false | Per-criterion breakdown |
| `--no-recursive` | false | Do not scan subdirectories |
| `--model NAME` | `gemma4:e4b` | Ollama model |
| `--ollama-url URL` | `http://localhost:11434` | Ollama API URL |
| `--max-dim N` | `1280` | Max image dimension before resize |
| `--timeout N` | `120` | Request timeout (seconds) |

### Sample verbose output

```
[1/50] sunset_beach.jpg
  Path:     /Users/me/Pictures/exported/sunset_beach.jpg
  Date:     2026-04-01 14:30:00
  Tags:     sunset, beach, ocean, waves, sand
  Summary:  golden hour sunset over the Pacific
  Scene:    outdoor_leisure
  Tone:     positive
  Cleanup:  keep
  Event:    outing
  Signif.:  high
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
| `image_date` | EXIF or file date |
| `tags` | 1-5 vision model tags |
| `scene_summary` | Short scene description |
| `scene_category` | `indoor_home`, `indoor_work`, `outdoor_leisure`, `outdoor_travel`, `transport`, `other` |
| `emotional_tone` | `positive`, `neutral`, `negative`, `mixed` |
| `cleanup_class` | `keep`, `review`, `delete` |
| `has_text` | Whether image contains readable text |
| `text_summary` | Extracted text summary (if `has_text`) |
| `event_hint` | `outing`, `gathering`, `work`, `travel`, `daily`, `other` |
| `significance` | `high`, `medium`, `low` |
| `gps_lat` / `gps_lon` | EXIF GPS coordinates |
| `nearest_place` | Village/town/suburb |
| `nearest_city` | City |
| `nearest_region` | State/region |
| `nearest_country` | Country |
| `processing_status` | `ok` or `error` |
| `error_message` | Error details if any |
| `phash` | Perceptual hash (when `--dedup` used) |

### Judge output schema

Results from `pyimgtag judge --output-json` use a different structure:

| Field | Description |
|---|---|
| `file_path` | Full path to image |
| `file_name` | Filename |
| `weighted_score` | Overall weighted score (1.0–5.0) |
| `core_score` | Artistic criteria average (impact, composition, lighting, etc.) |
| `visible_score` | Technical criteria average (focus, exposure, noise, etc.) |
| `verdict` | One-sentence summary of key strength and weakness |
| `scores.impact` | Emotional pull and memorability (1–5) |
| `scores.story_subject` | Clear subject and meaning (1–5) |
| `scores.composition_center` | Visual flow, balance, center of interest (1–5) |
| `scores.lighting` | Quality, control, mood support (1–5) |
| `scores.creativity_style` | Originality of treatment (1–5) |
| `scores.color_mood` | Color balance and mood fit (1–5) |
| `scores.presentation_crop` | Crop, framing, aspect ratio (1–5) |
| `scores.technical_excellence` | Exposure, retouching, overall finish (1–5) |
| `scores.focus_sharpness` | Critical detail is sharp (1–5) |
| `scores.exposure_tonal` | Highlights and shadows under control (1–5) |
| `scores.noise_cleanliness` | Clean detail, no distracting grain (1–5) |
| `scores.subject_separation` | Subject stands out from background (1–5) |
| `scores.edit_integrity` | No halos, overprocessing, or clone artefacts (1–5) |

## Architecture

```
src/pyimgtag/
  main.py              CLI entry point and subcommand dispatch (thin)
  models.py            Data classes (ExifData, TagResult, GeoResult, ImageResult)
  scanner.py           Directory and Photos library scanning
  exif_reader.py       EXIF GPS + date extraction (exiftool + Pillow)
  ollama_client.py     Ollama vision API client (rich structured response)
  geocoder.py          Nominatim reverse geocoder with disk cache
  filters.py           Date/GPS filter logic
  output_writer.py     JSON/CSV/JSONL output
  progress_db.py       SQLite progress DB with versioned migrations
  applescript_writer.py  Apple Photos keyword/description write-back
  dedup.py             Perceptual hash duplicate detection
  heic_converter.py    HEIC to JPEG conversion (macOS sips)
  cache.py             Simple JSON disk cache
  judge_scorer.py        Weighted rubric score computation (13-criterion)
  commands/
    run.py             `pyimgtag run` handler
    judge.py           `pyimgtag judge` handler
    db.py              `pyimgtag status/reprocess/cleanup` handlers
    query.py           `pyimgtag query` handler
    tags.py            `pyimgtag tags` handler
    faces.py           `pyimgtag faces` handler
    preflight_cmd.py   `pyimgtag preflight` handler
    review_cmd.py      `pyimgtag review` handler
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
