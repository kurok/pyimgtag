# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-04-17

### Fixed
- Resolved all CodeQL code scanning alerts (`py/empty-except`, `py/unused-global-variable`)
- Expanded CI test matrix to include Python 3.11 and 3.13
- Added MkDocs-based documentation site with GitHub Pages deployment
- Upgraded CI actions (codeql-action v4, upload-artifact v7, codecov-action v6)
- Switched CI to `uv` for faster installs; merged quality job
- Removed `--strict` from `mkdocs build` to allow expected cross-repo links
- Added Codecov patch/project checks as informational (non-blocking)

## [0.2.0] - 2026-04-16

### Added
- GPS coordinate range validation in geocoder — out-of-range lat/lon returns a `GeoResult` error instead of hitting Nominatim (#29)
- `ProgressDB` context manager support (`with ProgressDB(...) as db`) (#quality-pass)
- `_compute_dedup_map` helper function for cleaner dedup logic in `cmd_run` (#27)

### Changed
- Refactored `main.py` (1161 → 315 lines) by extracting subcommand handlers into focused `commands/` modules: `run`, `db`, `faces`, `query`, `tags`, `preflight_cmd`, `review_cmd`
- All informational/confirmation messages redirected to `stderr`; data output (paths, JSON, tables) remains on `stdout` (#31)
- Replaced magic numbers in `ollama_client.py` with named module constants (`_MODEL_TEMPERATURE`, `_MODEL_MAX_TOKENS`) (#30)
- Added `dict[str, int]` return type annotation to `_new_stats` in `cmd_status` (#28)

### Fixed
- Narrowed bare `except Exception` clauses in `dedup.py`, `preflight.py`, `progress_db.py`, `heic_converter.py`, `raw_converter.py`, `ollama_client.py` to specific exception types
- Temp directory leak on exception in `heic_converter.py` and `raw_converter.py`
- Orphaned temp file cleanup on write failure in `cache.py`
- Error propagation in `output_writer.py` — `OSError` now re-raised with descriptive message

## [0.1.0] - 2026-04-01

### Added
- Initial release
- CLI with subcommands: `run`, `status`, `reprocess`, `cleanup`, `preflight`, `query`, `tags`
- Local Gemma model tagging via Ollama HTTP API
- Rich AI metadata: scene category, emotional tone, cleanup classification, text detection, event hints
- EXIF GPS reverse geocoding via Nominatim with local disk cache
- Apple Photos library scanning and write-back via AppleScript (macOS only)
- Duplicate detection via perceptual hash
- HEIC/RAW image conversion support
- JSON, CSV, and JSONL export
- SQLite progress DB with schema versioning for incremental re-runs
- CI pipeline: lint, pre-commit, test (matrix), typecheck, security
- PyPI publish workflow with Trusted Publishing
- GitHub Release automation on `v*` tags

[0.2.0]: https://github.com/kurok/pyimgtag/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kurok/pyimgtag/releases/tag/v0.1.0
