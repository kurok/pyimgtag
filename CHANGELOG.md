# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0] - 2026-05-02

### Added
- **Pluggable vision backends**: `pyimgtag run` and `pyimgtag judge` accept `--backend ollama|anthropic|openai|gemini`. The default `ollama` backend is unchanged (and still supports remote Ollama via `--ollama-url`); the three new backends call hosted vision APIs. API keys are read from `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `GOOGLE_API_KEY`/`GEMINI_API_KEY` respectively, or via `--api-key`. `--api-base` overrides the cloud-API base URL for self-hosted gateways. Per-backend default models: `gemma4:e4b` (ollama), `claude-sonnet-4-6` (anthropic), `gpt-4o-mini` (openai), `gemini-1.5-flash` (gemini). (#137)
- New module `pyimgtag.cloud_clients` exposes `AnthropicClient`, `OpenAIClient`, `GeminiClient`, `make_image_client`, and `CloudClientError` for programmatic use; image preprocessing (HEIC, RAW, resize, JPEG, base64) is now factored into `pyimgtag.ollama_client.prepare_image_b64` and shared across all four clients. (#137)
- New wiki page **Choosing a Backend** with the provider matrix, request lifecycle, failure modes, and a Mermaid backend-dispatch diagram.

### Fixed
- `pyimgtag run`, `pyimgtag judge`, and `pyimgtag faces scan` now exit with status `1` after `Ctrl+C`. They previously caught `KeyboardInterrupt`, printed `Interrupted.`, and returned `0`, so user-cancelled runs were indistinguishable from successful ones in CI / scripts. (#136)

## [0.7.0] - 2026-05-02

### Changed
- **Breaking**: judge rating system now uses **integer scores from 1 to 10** instead of decimal 1–5. Per-criterion scores, `weighted_score`, `core_score`, and `visible_score` are all integers. Display formats `7/10` instead of `4.20/5`; the `score:N` write-back keyword is now a whole number. Old judge results stored on the previous scale are still readable but will round to integers. (#134)
- Judge label thresholds adjusted for the new scale: ≥9 outstanding, ≥8 strong, ≥7 solid, ≥5 acceptable, otherwise weak. (#134)
- README, mkdocs `platform-setup`, and the GitHub Wiki `Scoring Photos` page updated for the new scale; new wiki page **Use-Case Diagrams** documents the algorithm flow of every subcommand with Mermaid diagrams.

### Fixed
- `--resume-threaded` mode silently dropped the dedup-skipped count so the run summary under-reported activity; the threaded path now records `stats["skipped_dedup"]` consistently with the sequential path. (#133)
- `face_thumbnail_b64` chained `Image.open(p).convert("RGB")` and left the source file handle open until garbage collection; it now uses a context manager. (#132)
- `pyimgtag judge --extensions "jpg, jpeg"` silently dropped the second extension because the parser did not strip whitespace; it now matches `commands/faces.py` (`.strip().lstrip(".").lower()`). (#132)
- Review UI's `removeTag`, `addTag`, and `setCleanup` PATCH calls did not check the response status, so failed updates left the UI showing modified state. They now revert local state and surface an alert on non-OK responses. (#132)

### Security
- Smoke scripts `scripts/test_faces_ui.sh` and `scripts/test_faces_import_photos.sh` no longer use hardcoded `/tmp/*.db` and `/tmp/resp.json` paths; they use `mktemp -t` so parallel runs cannot collide and the trap reliably cleans up. (#133)
- `.dockerignore` now excludes `.git/`, `.worktrees/`, `.env*`, `*.pem`, `*.key`, `.aws/`, `.ssh/`, `.kube/` so repo history and dev credentials cannot be baked into images by accident. (#133)

## [0.6.1] - 2026-04-27

### Security
- Upgraded `pip` to 26.1 in `uv.lock` to fix CVE-2026-3219 (GHSA-58qw-9mgm-455v): pip incorrectly handled concatenated tar+ZIP files as ZIP-only, which could result in wrong files being installed. (#130)

### CI
- Added `configure-pages` step to GitHub Pages docs deploy to prevent first-run 404. (#129)

## [0.5.2] - 2026-04-20

### Security
- Review UI `/thumbnail` endpoint now returns 404 for any path not indexed in the progress DB, closing an arbitrary local file read if the server is bound to a non-loopback host. (#98)
- Faces review UI disables `/docs`, `/redoc`, and `/openapi.json` by default; review UI now also disables `/openapi.json` (it previously left the schema endpoint exposed even with `/docs` off). (#98)

### Fixed
- `pyimgtag faces ui` ImportError messages now direct users to the correct `[review]` extra (\`pip install 'pyimgtag[review]'\`) instead of the `[dev]` extra, which contains only test tooling. (#97)

### Documentation
- Overview and feature-list in README now correctly qualify the "runs on-device" claim: image analysis and tagging are local, but EXIF GPS coordinates are sent to OpenStreetMap Nominatim for reverse geocoding (with local cache). (#95)
- `faces import-photos` examples no longer show a non-existent `--photos-library` flag; the command uses the default system Photos library. (#96)

## [0.5.1] - 2026-04-20

### Fixed
- `pyimgtag run --dry-run` no longer creates or writes to the SQLite progress DB. Under dry-run, `ProgressDB` is not constructed, so `mark_done` and `is_processed` become no-ops. (#88)
- `pyimgtag faces scan` now surfaces a friendly CLI error ("install the `[face]` extra") and returns exit code 1 when the optional `face_recognition` dependency is missing, instead of raising a raw traceback from deep inside the scan loop. (#89)
- `examples/mock_ollama.py` is now safe to import. Module-level `sys.argv[1]` parsing has been moved into `main()` and replaced with a `DEFAULT_PORT = 11435` constant, fixing `pytest` collection in environments that import the examples directory. (#90)

## [0.5.0] - 2026-04-20

### Added
- `--resume-from-db` flag: reuse cached model results for unchanged files and re-run only local enrichment (EXIF, geocoding). Interrupted runs can continue without re-sending already-processed images to Ollama.
- `--resume-threaded` flag: enrich cached items in a background thread while the main thread keeps sending new files to Ollama.
- `--skip-if-tagged` flag: skip Ollama processing for photos that already have keywords in Apple Photos.
- `PYIMGTAG_USE_PHOTOSCRIPT` env var to opt into the faster in-process photoscript path for Photos write-back; default is the safer osascript subprocess path.
- Mock Ollama server (`examples/mock_ollama.py`) now implements `GET /api/tags` so `pyimgtag preflight` and `pyimgtag judge` work against it for demos.

### Changed
- `ProgressDB.is_processed()` now treats only `status='ok'` rows as already-processed. Transient Ollama failures are retried on the next run instead of being silently skipped as cached.
- Apple Photos read/write paths prefer osascript by default. The in-process photoscript path is opt-in via `PYIMGTAG_USE_PHOTOSCRIPT=1`, avoiding macOS hiservices crashes on unstable hosts.
- `[all]` extra in `pyproject.toml` now includes face-recognition and review dependencies — `pip install pyimgtag[all]` truly installs every optional feature.

### Fixed
- `photos_faces_importer` no longer imports `photoscript` at module load time; the import is deferred to inside `import_photos_persons()`.
- `applescript_writer._has_photoscript()` probes availability via `importlib.util.find_spec()` instead of importing the module, preventing the macOS hiservices crash during availability checks.
- Documentation: README CLI examples and platform-setup guide updated to match current subcommand structure.

## [0.4.2] - 2026-04-18

### Fixed
- Fallback to filename search when media item ID lookup fails in Apple Photos

## [0.4.1] - 2026-04-18

### Added
- Platform-specific setup guides for macOS, Linux, and Windows

## [0.4.0] - 2026-04-18

### Added
- Faces management: import named persons from Apple Photos and face management UI
- Write-back append mode for non-destructive keyword updates
- Judge score storage in SQLite progress DB

## [0.3.0] - 2026-04-17

### Added
- `judge` subcommand for AI-based photo quality scoring with 13-criterion rubric
- Weighted score output (1–5 scale), per-criterion breakdown, `--min-score` filter
- Judge results exportable to JSON with full per-criterion scores
- `pyimgtag faces` subcommand for face recognition and management (macOS)

### Fixed
- `--dry-run` correctly skips write-back operations
- Apple Photos applescript lookup uses media item ID for O(1) performance

## [0.2.3] - 2026-04-17

### Fixed
- Apple Photos: fall back to osascript when photoscript UUID lookup fails
- Apple Photos: replace `search()` with `photo(uuid)` lookup for reliable retrieval
- Show macOS dialog to open Full Disk Access settings on Photos Library permission error
- Surface `PermissionError` when Photos library is TCC-blocked
- Add comment to empty `except` clause to satisfy linter

## [0.2.2] - 2026-04-17

### Fixed
- Ollama: switch to `format: json` with prompt-described fields for structured output
- Resolved four real-world tagging failure cases (empty response, missing keys, malformed JSON, Unicode errors)

### Changed
- Increased code coverage from 78% to 84%

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

[0.4.2]: https://github.com/kurok/pyimgtag/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/kurok/pyimgtag/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/kurok/pyimgtag/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/kurok/pyimgtag/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/kurok/pyimgtag/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/kurok/pyimgtag/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/kurok/pyimgtag/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/kurok/pyimgtag/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kurok/pyimgtag/releases/tag/v0.1.0
