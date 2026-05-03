# pyimgtag

[![CI](https://github.com/kurok/pyimgtag/actions/workflows/python-package.yml/badge.svg)](https://github.com/kurok/pyimgtag/actions/workflows/python-package.yml)
[![CodeQL](https://github.com/kurok/pyimgtag/actions/workflows/codeql.yml/badge.svg)](https://github.com/kurok/pyimgtag/actions/workflows/codeql.yml)
[![PyPI version](https://img.shields.io/pypi/v/pyimgtag)](https://pypi.org/project/pyimgtag/)
[![Python versions](https://img.shields.io/pypi/pyversions/pyimgtag)](https://pypi.org/project/pyimgtag/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![codecov](https://codecov.io/gh/kurok/pyimgtag/graph/badge.svg)](https://codecov.io/gh/kurok/pyimgtag)

Tag images using a local Gemma model for searchable tags, with optional Apple Photos integration on macOS.

## Overview

pyimgtag uses a vision model to analyse images and generate 1-5 descriptive
tags per photo. By default it calls a locally-running Gemma model via
[Ollama](https://ollama.ai), so image analysis and tagging stay on-device.
You can also point pyimgtag at a **remote Ollama server** or one of three
hosted vision APIs — [Anthropic Claude](https://docs.anthropic.com/en/api/messages),
[OpenAI](https://platform.openai.com/docs/api-reference/chat),
or [Google Gemini](https://ai.google.dev/api/generate-content) — by passing
`--backend`. When a cloud backend is selected, the JPEG bytes leave the
machine; otherwise they don't.

If EXIF GPS is present, only the latitude/longitude is sent to [OpenStreetMap
Nominatim](https://nominatim.openstreetmap.org/) for reverse geocoding to a
city/place; results are cached locally so repeat lookups stay offline.

Works on **macOS, Linux, and Windows**. Apple Photos integration (write-back) is macOS-only.

**Key features:**

- One model call per image, compact prompt, low token usage
- **Pluggable vision backends**: local Ollama (default), remote Ollama via `--ollama-url`, Anthropic Claude, OpenAI, or Google Gemini via `--backend`
- Rich AI metadata: scene category, emotional tone, cleanup classification, text detection, event hints
- EXIF GPS as source of truth for location (never guessed from image content)
- Open reverse geocoding via Nominatim (sends GPS coords to OpenStreetMap; cached locally)
- Supports exported folders and Apple Photos library originals (macOS only)
- Apple Photos write-back: push AI tags and descriptions back as keywords/captions (macOS only)
- Subcommands: `run`, `judge`, `status`, `reprocess`, `cleanup`, `preflight`, `query`, `tags`, `faces`, `review`
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
  --min-score 7 --output-json ranking.json
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
| HEIC conversion (sips / pillow-heif) | ✅ sips + pillow-heif | ✅ pillow-heif | ✅ pillow-heif |
| RAW image support (rawpy) | ✅ | ✅ | ✅ |
| Apple Photos library scanning | ✅ | ❌ | ❌ |
| Apple Photos write-back | ✅ | ❌ | ❌ |
| Face management (Apple Photos) | ✅ | ❌ | ❌ |

**Note:** Most features work cross-platform. Apple Photos integration and face management are macOS-only — they require AppleScript via `osascript`.

### macOS Setup

```bash
# Prerequisites
brew install ollama exiftool
ollama pull gemma4:e4b

# Install
pip install "pyimgtag[all]"   # includes pillow-heif, photoscript, rawpy, face-recognition, fastapi, uvicorn
# or from source
git clone https://github.com/kurok/pyimgtag.git
cd pyimgtag
pip install -e ".[all,dev]"
```

Features available: everything including Apple Photos integration, HEIC, face management, and photo review workflows.

Typical macOS workflow:
```bash
# Tag your Photos library directly
pyimgtag run --photos-library ~/Pictures/Photos\ Library.photoslibrary --write-back --limit 50

# Score photo quality
pyimgtag judge --photos-library ~/Pictures/Photos\ Library.photoslibrary --min-score 8

# Import named faces from Apple Photos
pyimgtag faces import-photos  # reads system default Photos library
```

**Note:** Apple Photos library access requires Full Disk Access permission for your terminal app — grant it in System Settings > Privacy & Security > Full Disk Access.

#### Face features: `face_recognition_models` is git-only

`face_recognition` needs a companion package, `face_recognition_models`,
which only lives on git — it was never published to PyPI. **You always
need to install it as a separate step**, regardless of whether you got
pyimgtag from PyPI or source: PyPI rejects packages whose metadata
declares direct-URL dependencies, so `[face]` / `[all]` extras can't
list it for you.

```bash
pip install 'pyimgtag[face]'      # or .[all]; models package NOT included
python -m pip install \
    "face_recognition_models @ git+https://github.com/ageitgey/face_recognition_models"
```

If `pyimgtag faces scan` exits with a "Please install
`face_recognition_models`" message and no traceback, you skipped that
second command. Verify the install landed in the right venv with:

```bash
python -m pip show face_recognition_models
```

If `pip show` says it's installed but pyimgtag still complains, the
likely culprit is a missing `pkg_resources`. There are two ways this
shows up:

1. Python 3.12+ no longer bundles setuptools by default, so
   `pkg_resources` is just absent.
2. **setuptools 81 removed `pkg_resources` from the package**, so you
   can have setuptools 81+ installed and `pip show` happy, yet
   `import pkg_resources` raises `ModuleNotFoundError`.

Pinning setuptools below 81 fixes both — the `[face]` and `[all]`
extras pin `setuptools>=68.0,<81` automatically. If you installed
without the extra (or your env already had setuptools 81+), run:

```bash
python -m pip install 'setuptools<81'
```

### Linux Setup

```bash
# Ubuntu/Debian
sudo apt-get install exiftool python3.11 python3-pip
# or install exiftool from https://exiftool.org

# Fedora/RHEL
sudo dnf install perl-Image-ExifTool python3.11

# Arch
sudo pacman -S perl-image-exiftool python

# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4:e4b

# Install pyimgtag
pip install "pyimgtag[heic]"   # includes pillow-heif for HEIC
# or from source
git clone https://github.com/kurok/pyimgtag.git
cd pyimgtag
pip install -e ".[heic,dev]"
```

Features available: image tagging, EXIF reading/writing, geocoding, judge, dedup, JSON/CSV export. No Apple Photos integration.

Typical Linux workflow:
```bash
# Tag an exported photo directory
pyimgtag run --input-dir ~/Pictures/exported --output-json results.json

# With EXIF write-back (requires exiftool)
pyimgtag run --input-dir ~/Pictures/exported --write-exif

# Score photo quality
pyimgtag judge --input-dir ~/Pictures/exported --min-score 7 --output-json ranking.json
```

**Note:** `--write-back` (Apple Photos) is silently skipped on Linux with a warning. Use `--write-exif` instead.

### Windows Setup

```powershell
# Install Python 3.11+ from https://python.org
# Install Ollama from https://ollama.com

# Install exiftool — download from https://exiftool.org/
# Or via Chocolatey:
choco install exiftool

# Or via winget:
winget install OliverBetz.ExifTool

ollama pull gemma4:e4b

# Install pyimgtag
pip install "pyimgtag[heic]"
# or from source
git clone https://github.com/kurok/pyimgtag.git
cd pyimgtag
pip install -e ".[heic,dev]"
```

Features available: same as Linux — tagging, EXIF, geocoding, judge, dedup, export. No Apple Photos integration.

Typical Windows workflow (PowerShell):
```powershell
# Tag photos in a folder
pyimgtag run --input-dir C:\Users\Me\Pictures\exported --output-json results.json

# Score photo quality
pyimgtag judge --input-dir C:\Users\Me\Pictures\exported --min-score 7

# Check what was processed
pyimgtag status
```

**Note:** On Windows, use `\` path separators or quote paths with spaces: `"C:\My Photos"`.

### Platform Troubleshooting

**macOS:**
- "Operation not permitted" on Photos library → grant Full Disk Access to Terminal in System Settings > Privacy & Security > Full Disk Access
- `exiftool` not found → `brew install exiftool`
- HEIC files not loading → `pip install pillow-heif`
- Ollama not running → `brew services start ollama` or run `ollama serve`

**Linux:**
- `exiftool` not found → install via package manager (see setup above)
- HEIC files not loading → `pip install pillow-heif`
- Ollama not running → `ollama serve` in a separate terminal
- Permission denied on image folder → check directory permissions with `ls -la`

**Windows:**
- `exiftool` not found → add exiftool directory to PATH, or install via Chocolatey/winget
- Python not found → ensure Python 3.11+ is installed and added to PATH during install
- HEIC files not loading → `pip install pillow-heif`
- Ollama not running → start Ollama from system tray or run `ollama serve`
- Long paths issue → enable long path support: `Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1`

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

##### Choosing a vision backend

By default pyimgtag calls a local Ollama server. Use `--backend` to pick a
different provider; the same prompt and result schema apply across backends.

```bash
# Default: local Ollama
pyimgtag run --input-dir /path/to/photos

# Remote Ollama server (e.g. on another machine in your LAN)
pyimgtag run --input-dir /path/to/photos --ollama-url http://gpu-host:11434

# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-... pyimgtag run --input-dir /path/to/photos \
  --backend anthropic

# OpenAI (override the default model if needed)
OPENAI_API_KEY=sk-... pyimgtag run --input-dir /path/to/photos \
  --backend openai --model gpt-4o

# Google Gemini
GOOGLE_API_KEY=... pyimgtag run --input-dir /path/to/photos \
  --backend gemini
```

Per-backend defaults:

| Backend     | Default model         | Auth env var                          |
|-------------|-----------------------|---------------------------------------|
| `ollama`    | `gemma4:e4b`          | none (uses `--ollama-url`)            |
| `anthropic` | `claude-sonnet-4-6`   | `ANTHROPIC_API_KEY`                   |
| `openai`    | `gpt-4o-mini`         | `OPENAI_API_KEY`                      |
| `gemini`    | `gemini-1.5-flash`    | `GOOGLE_API_KEY` (or `GEMINI_API_KEY`)|

Cloud backends send the JPEG bytes for each image to the provider. Use
`--api-base` to override the base URL (for self-hosted gateways or
proxies) and `--api-key` if you want to pass the secret on the command
line instead of via an environment variable. The `--backend` flag works
identically for `pyimgtag judge`.

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
| `--write-back` | Write tags/description back to Apple Photos *(macOS only; uses osascript by default — set `PYIMGTAG_USE_PHOTOSCRIPT=1` to opt into the faster in-process photoscript path on stable hosts)* |
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

# Search by city / country
pyimgtag query --city "San Francisco"
pyimgtag query --country Italy

# Filter by scene / cleanup / status
pyimgtag query --scene-category outdoor_travel --status ok
pyimgtag query --cleanup delete

# Filter by text-detection state
pyimgtag query --has-text
pyimgtag query --no-text

# Output as JSON or just paths (e.g. for shell pipelines)
pyimgtag query --tag beach --format json
pyimgtag query --tag beach --format paths --limit 50
```

#### `pyimgtag faces` — face detection, clustering, naming *(macOS)*

Six sub-subcommands chain into a typical face workflow:

```bash
# 1. Detect faces and compute embeddings
pyimgtag faces scan --photos-library ~/Pictures/Photos\ Library.photoslibrary

# 2. Cluster embeddings into person groups (DBSCAN)
pyimgtag faces cluster --eps 0.5 --min-samples 2

# 3. Inspect the clusters from the CLI
pyimgtag faces review

# 4. Import named persons from Apple Photos (uses bulk AppleScript)
pyimgtag faces import-photos

# 5. Write person keywords to image metadata (EXIF or XMP sidecar)
pyimgtag faces apply --write-exif
pyimgtag faces apply --sidecar-only --dry-run

# 6. Manage clusters via the web UI (rename, merge, delete)
pyimgtag faces ui  # serves the unified webapp on http://127.0.0.1:8766
```

`scan` accepts `--detection-model {hog,cnn}` (hog = fast CPU, cnn = accurate
GPU), `--max-dim`, `--extensions`, and `--limit`, plus the same dashboard
flags as `run` / `judge` (`--web` / `--no-web` / `--web-host` / `--web-port`
/ `--no-browser`).

The `[face]` extra is required; see the
[`face_recognition_models` install note](#face-features-face_recognition_models-is-git-only)
above.

#### `pyimgtag review` — launch the local review UI

```bash
# Browse the progress DB, edit tags, change cleanup class
pyimgtag review                      # serves on http://127.0.0.1:8765
pyimgtag review --port 9000 --no-browser
```

This serves the **same** unified webapp as `run --web`, just bound to a
different default port. See [Local webapp](#local-webapp) below for the
full page list. Requires the `[review]` extra (`pip install
'pyimgtag[review]'`).

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

Score each image against a 13-criterion professional rubric. Outputs a ranked list with weighted scores as **integers on a 1–10 scale** (no decimal component). Requires Ollama.

```bash
# Score all images in a folder
pyimgtag judge --input-dir ~/Pictures/exported

# Only show photos scoring 3.5 or above
pyimgtag judge --input-dir ~/Pictures/exported --min-score 7

# Verbose breakdown (per-criterion scores)
pyimgtag judge --input-dir ~/Pictures/exported --limit 20 --verbose

# Sort by filename instead of score
pyimgtag judge --input-dir ~/Pictures/exported --sort-by name

# Score Photos library
pyimgtag judge --photos-library ~/Pictures/Photos\ Library.photoslibrary \
  --limit 50 --min-score 8

# Save full ranking to JSON
pyimgtag judge --input-dir ~/Pictures/exported \
  --output-json ranking.json
```

**Sample output (brief mode):**
```
[1/5] golden_hour.jpg → 9/10 outstanding | + impact, composition_center | - edit_integrity, noise_cleanliness
  Golden light over the cityscape; strong composition but slight haloing on edges.
[2/5] portrait.jpg → 7/10 solid | + focus_sharpness, lighting | - creativity_style, color_mood
  Well-lit portrait; technically solid but conventional treatment.
```

**Sample output (--verbose):**
```
[1/5] golden_hour.jpg
  Score:   9/10  (core: 9, visible: 8)
  Best:    impact=10, composition_center=10, lighting=8
  Weakest: edit_integrity=6, noise_cleanliness=6, subject_separation=6
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
| `--backend ollama\|anthropic\|openai\|gemini` | `ollama` | Vision-model backend (same as `pyimgtag run`) |
| `--model NAME` | backend-specific | Model name; defaults `gemma4:e4b` / `claude-sonnet-4-6` / `gpt-4o-mini` / `gemini-1.5-flash` |
| `--ollama-url URL` | `http://localhost:11434` | Ollama API URL (used when `--backend=ollama`) |
| `--api-base URL` | provider default | Override the cloud-API base URL (anthropic / openai / gemini) |
| `--api-key KEY` | env var | Cloud-API key; defaults to the provider's conventional env var |
| `--max-dim N` | `1280` | Max image dimension before resize |
| `--timeout N` | `120` | Request timeout (seconds) |
| `--db PATH` | `~/.cache/pyimgtag/progress.db` | Progress DB path; judge scores share the same DB as `run` |
| `--skip-judged` | false | Skip images that already have a row in `judge_scores` |
| `--write-back` | false | Write the score keyword back to Apple Photos *(macOS + `--photos-library` only)* |
| `--write-back-mode overwrite\|append` | `overwrite` | Whether write-back replaces or merges keywords |
| `--web` / `--no-web` / `--web-host` / `--web-port` / `--no-browser` | — | Same dashboard flags as `pyimgtag run` (default port `8770`) |

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
| `weighted_score` | Overall weighted score (integer 1–10) |
| `core_score` | Artistic criteria average (integer 1–10) |
| `visible_score` | Technical criteria average (integer 1–10) |
| `verdict` | One-sentence summary of key strength and weakness |
| `scores.impact` | Emotional pull and memorability (integer 1–10) |
| `scores.story_subject` | Clear subject and meaning (integer 1–10) |
| `scores.composition_center` | Visual flow, balance, center of interest (integer 1–10) |
| `scores.lighting` | Quality, control, mood support (integer 1–10) |
| `scores.creativity_style` | Originality of treatment (integer 1–10) |
| `scores.color_mood` | Color balance and mood fit (integer 1–10) |
| `scores.presentation_crop` | Crop, framing, aspect ratio (integer 1–10) |
| `scores.technical_excellence` | Exposure, retouching, overall finish (integer 1–10) |
| `scores.focus_sharpness` | Critical detail is sharp (integer 1–10) |
| `scores.exposure_tonal` | Highlights and shadows under control (integer 1–10) |
| `scores.noise_cleanliness` | Clean detail, no distracting grain (integer 1–10) |
| `scores.subject_separation` | Subject stands out from background (integer 1–10) |
| `scores.edit_integrity` | No halos, overprocessing, or clone artefacts (integer 1–10) |

## Architecture

```
src/pyimgtag/
  main.py              CLI entry point and subcommand dispatch (thin)
  models.py            Data classes (ExifData, TagResult, GeoResult, ImageResult)
  scanner.py           Directory and Photos library scanning
  exif_reader.py       EXIF GPS + date extraction (exiftool + Pillow)
  ollama_client.py     Ollama vision API client (rich structured response)
  cloud_clients.py     Anthropic / OpenAI / Gemini vision-API adapters
  geocoder.py          Nominatim reverse geocoder with disk cache
  filters.py           Date/GPS filter logic
  output_writer.py     JSON/CSV/JSONL output
  progress_db.py       SQLite progress DB with versioned migrations
  applescript_writer.py  Apple Photos keyword/description write-back
  _face_dep_check.py   Friendly preflight for face_recognition_models
  dedup.py             Perceptual hash duplicate detection
  heic_converter.py    HEIC to JPEG conversion (macOS sips)
  cache.py             Simple JSON disk cache
  judge_scorer.py      Weighted rubric score computation (13-criterion)
  preflight.py         Shared preflight helpers
  commands/
    run.py             `pyimgtag run` handler
    judge.py           `pyimgtag judge` handler
    db.py              `pyimgtag status/reprocess/cleanup` handlers
    query.py           `pyimgtag query` handler
    tags.py            `pyimgtag tags` handler
    faces.py           `pyimgtag faces` (scan / cluster / review / apply / import-photos / ui)
    preflight_cmd.py   `pyimgtag preflight` handler
    review_cmd.py      `pyimgtag review` handler
  webapp/
    __main__.py        `python -m pyimgtag.webapp` standalone uvicorn launcher
    unified_app.py     FastAPI app composition + `/health` endpoint
    nav.py             Shared nav shell + design system
    routes_review.py   `/review` router (browse / edit / lightbox)
    routes_faces.py    `/faces` router (cluster management UI)
    routes_tags.py     `/tags` router
    routes_query.py    `/query` router
    routes_judge.py    `/judge` router (rating grid + filter/sort)
    routes_edit.py     `/edit` router (bulk-delete from Photos)
    routes_about.py    `/about` router (version / update check / wiki)
    dashboard_server.py / server_thread.py / bootstrap.py
                       In-process dashboard for `run` / `judge` / `faces scan`
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

### Pre-PR smoke (`tests/e2e/`)

The `pr-tests` GitHub Actions workflow runs unit tests **and** a
Playwright Chromium smoke that boots the dashboard on every PR. To run
the same checks locally before pushing:

```bash
# Installs deps + Chromium, starts the app on :8000, waits for /health,
# runs unit tests, runs the Playwright smoke, then stops the app cleanly.
scripts/test-smoke-local.sh

# Custom port / real DB / visible browser:
PORT=8765 scripts/test-smoke-local.sh
PYIMGTAG_DB=~/.cache/pyimgtag/progress.db scripts/test-smoke-local.sh
PYIMGTAG_E2E_HEADLESS=0 scripts/test-smoke-local.sh
```

The smoke test auto-discovers every link in the top nav, clicks each
one, and fails the run on **any** of: HTTP 5xx, an uncaught JS error,
a `console.error`, a blank page, or a heading-less page.

**Inspecting failures.** When a smoke test fails — locally or in CI —
artefacts land under `tests/e2e/artifacts/<test-id>/`:

| File | What it shows |
| --- | --- |
| `screenshot.png` | full-page PNG of the page when the assertion fired |
| `trace.zip` | Playwright trace — open with `playwright show-trace trace.zip` for DOM, network log, and per-step screenshots |
| `app.log` (parent dir) | uvicorn access log + tracebacks from the dashboard process |

CI uploads the same `tests/e2e/artifacts/` directory as a workflow
artifact named `pr-tests-artifacts`. Download it from the failed
run's "Artifacts" panel on GitHub.

**Required checks.** A PR can merge once the `Unit + E2E smoke` job in
the `pr-tests` workflow passes (alongside the existing `Python
package` matrix and CodeQL).

## Resume and Enrichment

Rerunning `pyimgtag run` on an already-processed library normally skips unchanged files.
With `--resume-from-db`, those files are re-hydrated from the database instead of being
silently skipped, so their results still appear in output files and the `--write-back` path
runs again.

Only **local enrichment** is repeated (EXIF, reverse geocoding). The AI model is not called again.

```bash
# Normal run — first pass, all files sent to Ollama
pyimgtag run --photos-library ~/Pictures/Photos\ Library.photoslibrary \
             --db ~/my-progress.db --write-back

# Resume after interruption — unchanged files load from DB, only new files hit Ollama
pyimgtag run --photos-library ~/Pictures/Photos\ Library.photoslibrary \
             --db ~/my-progress.db --write-back --resume-from-db

# Threaded resume — cached-item enrichment runs in a background thread
# while the main thread continues sending new files to Ollama
pyimgtag run --photos-library ~/Pictures/Photos\ Library.photoslibrary \
             --db ~/my-progress.db --write-back --resume-from-db --resume-threaded
```

A file is eligible for DB resume if:
- Its size and modification time have not changed since the last run.
- The cached entry has at least one tag.

Use `pyimgtag reprocess --db ~/my-progress.db` to force a full re-run for all files,
or `pyimgtag reprocess --db ~/my-progress.db --status error` to retry only failed files.

## Local webapp

`pyimgtag run`, `pyimgtag judge`, and `pyimgtag faces scan` auto-start a
local webapp at http://127.0.0.1:8770 by default. The same unified app
hosts a single top-nav with these pages:

- `/` — Dashboard (live progress, status, quick links).
- `/review` — browse DB entries, edit tags, change cleanup class.
- `/faces` — manage person clusters, rename, merge, delete.
- `/tags` — list, rename, merge, delete tags across the DB.
- `/query` — full-text/tag/scene/judge filters with hover thumbnails.
- `/judge` — judge-score grid with rating filter / sort / pager.
- `/edit` — bulk-delete files marked `cleanup_class='delete'` from
  Apple Photos (macOS only; gated behind an explicit confirm).
- `/about` — installed version, latest PyPI release, update check, wiki links.
- `/health` — plain JSON liveness probe (`{ok, version, db}`); used by
  the pre-PR + CI smoke runners.

The standalone commands continue to work and serve the **same** unified app:

- `pyimgtag review` on http://127.0.0.1:8765 (review at `/review`).
- `pyimgtag faces ui` on http://127.0.0.1:8766 (faces at `/faces`).
- `python -m pyimgtag.webapp` for a bare uvicorn launch — reads `HOST`
  / `PORT` / `PYIMGTAG_DB` / `PYIMGTAG_LOG_LEVEL` from the environment.
  This is the same launch surface used by `scripts/test-smoke-local.sh`
  and the `pr-tests` GitHub Actions workflow.

Flags (apply to `run`, `judge`, `faces scan`):

- `--no-web` — terminal-only mode, no server started.
- `--web` — force-enable (overrides `PYIMGTAG_NO_WEB=1`).
- `--web-host HOST` — bind host (default `127.0.0.1`).
- `--web-port PORT` — bind port (default `8770`).
- `--no-browser` — do not auto-open the browser.

Pause semantics are cooperative: the gate is checked before each file so
in-flight Ollama / face-detection requests are never interrupted mid-call.

**Migration note:** the `pyimgtag review` and `pyimgtag faces ui` commands
now serve the unified app, so the URL paths have shifted. Bookmarks that
used `http://localhost:8765/api/stats` should be updated to
`http://localhost:8765/review/api/stats`.

## Environment variables

| Variable | Used by | Effect |
|---|---|---|
| `PYIMGTAG_BACKEND` | `pyimgtag run` / `pyimgtag judge` | Default vision backend (`ollama` / `anthropic` / `openai` / `gemini`). Overridden by `--backend`. |
| `OLLAMA_URL` | `pyimgtag run` / `judge` / `preflight` | Default Ollama base URL (default `http://localhost:11434`). Overridden by `--ollama-url`. |
| `ANTHROPIC_API_KEY` | `--backend anthropic` | Auth for Claude. Overridden by `--api-key`. |
| `OPENAI_API_KEY` | `--backend openai` | Auth for OpenAI. Overridden by `--api-key`. |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | `--backend gemini` | Auth for Gemini (either name accepted). Overridden by `--api-key`. |
| `PYIMGTAG_NO_WEB` | All commands that start the dashboard | `1` / `true` / `yes` disables the dashboard by default (same as `--no-web`). |
| `PYIMGTAG_NO_UPDATE_CHECK` | All `pyimgtag` invocations | Skip the PyPI update check on startup. |
| `PYIMGTAG_USE_PHOTOSCRIPT` | `--write-back` / faces import | `1` / `true` / `yes` opts into the in-process [photoscript](https://pypi.org/project/photoscript/) path instead of the default `osascript` subprocess. |
| `PYIMGTAG_PARSE_ERROR_LOG` | `pyimgtag run` | Path to the Ollama JSON-parse error log (default `pyimgtag-parse-errors.log` in the cwd). |
| `PYIMGTAG_DB` | `python -m pyimgtag.webapp` | Override the progress-DB path the standalone webapp opens. |
| `PYIMGTAG_LOG_LEVEL` | `python -m pyimgtag.webapp` | uvicorn log level (default `info`). |
| `HOST` / `PORT` | `python -m pyimgtag.webapp` | Bind host / port for the standalone launcher (default `127.0.0.1:8000`). |
| `PYIMGTAG_SCREENSHOT_DB` | `tests/local/test_webapp_screenshots.py` | Walk the screenshot suite against an existing DB instead of a sandboxed one. |
| `BASE_URL` / `PYIMGTAG_E2E_HEADLESS` | `tests/e2e/` Playwright suite | Override smoke-test target URL / run with a visible browser. |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

Found a vulnerability? Please follow the disclosure flow in [SECURITY.md](SECURITY.md) -- do not file a public issue.

## License

MIT -- see [LICENSE](LICENSE).
