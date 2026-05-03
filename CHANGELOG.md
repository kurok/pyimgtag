# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Edit page — bulk delete from Apple Photos**: new top-nav entry `/edit` lists every progress-DB row with `cleanup_class='delete'` and exposes a destructive "Delete N from Photos" action behind an explicit confirm checkbox. The action runs in a background thread, walks the marked rows one by one, asks Photos.app to delete each via a new `applescript_writer.delete_from_photos()` (UUID fast-path + filename-scan fallback, mirroring `reveal_in_photos`) and removes the matching `processed_images` row only on success so a re-scan won't re-process trashed images. Photos.app routes deletions to *Recently Deleted* (30-day undo) — the script never empties that bin. New endpoints: `GET /edit/api/marked` (count + sample), `POST /edit/api/run` (one job at a time, returns 400 + `error="job_already_running"` on overlap), `GET /edit/api/status` (live progress + recent events). AppleScript stderr is logged server-side and only stable category strings (`platform_unsupported` / `photos_timeout` / `photos_unavailable` / `photos_error`) reach the browser.

### Changed
- **Judge page rebuilt in the Review-style grid layout**: `/judge/` now renders the same sticky toolbar + card grid + lightbox + pagination shell as `/review/`, replacing the bar-graph card stack. Each card surfaces a prominent colour-coded rating badge (green ≥8, amber ≥6, red below, muted when missing) over the thumbnail and the model's full natural-language `reason` text in the body so the judgement is the focus. The toolbar gains `Min rating` / `Max rating` number inputs (1–10, clamped silently), a `Sort` dropdown (`rating_desc` default, `rating_asc`, `path_asc`, `path_desc`, `shot_desc`, `shot_asc`) and a `Per page` selector (25/50/100/200), all backed by a new whitelisted ORDER BY in `progress_db.query_judge_results(...)` joined against `processed_images` so the card carries `image_date`, `scene_summary`, `nearest_city/country`, `cleanup_class`. `GET /judge/api/scores` now returns `{items, total}` instead of a bare list and accepts `offset`, `limit` (capped at 200), `sort`, `min_rating`, `max_rating`. Click-through on the thumbnail opens the existing `/review/original` lightbox; the "Open original" link re-uses `/review/api/open-in-photos` for the Photos.app reveal hop.

## [0.12.0] - 2026-05-03

### Added
- **Photo datetime in Query results** (#157): DB migration v8 adds a `processed_images.image_date` column populated from `read_exif().date_original`. The Query page renders a new Date column next to File and the Sort dropdown gains "Newest taken" / "Oldest taken" — both backed by new whitelisted `_QUERY_SORTS` keys (`shot_desc` / `shot_asc`, NULLs last). Older rows from before v8 surface `image_date=null` and the JS falls back to an em-dash so existing DBs render cleanly.
- **"Open original" reveals the photo in Apple Photos** (#157): new `POST /review/api/open-in-photos` endpoint in the review router calls `applescript_writer.reveal_in_photos`, which activates Photos.app and `spotlight`s the matching media item (UUID-stem fast path, filename-scan fallback). The Review card "Open original" link POSTs to that endpoint on a plain click; modifier-clicks and any failure on the macOS hop fall through to the existing `/review/original` byte stream so non-Photos files and power-user flows still work.
- **Local screenshot smoke** (#157): new `tests/local/test_webapp_screenshots.py` boots the unified webapp in a uvicorn thread, drives it with a sandboxed Chromium via Playwright, and writes ~40 PNGs per run covering every page + every menu / pill / sort option / filter value. Excluded from CI by default — run with `pytest tests/local/ --override-ini='addopts=' -s` after `pip install '.[screenshot]' && playwright install chromium`. Set `PYIMGTAG_SCREENSHOT_DB` to walk the UI against your real DB.
- **`/health` endpoint** (#159): plain JSON `{ok, version, db}` mounted at `/health` on the unified app, used as the readiness signal by the new pre-PR + PR runners.
- **Standalone webapp launcher** (#159): `python -m pyimgtag.webapp` reads `HOST` / `PORT` / `PYIMGTAG_DB` / `PYIMGTAG_LOG_LEVEL` from the environment, so local and CI launches share one surface.
- **End-to-end Playwright smoke + CI workflow** (#159): `tests/e2e/test_smoke.py` auto-discovers every `nav.nav a.nav-link` from the home page and clicks through them — failing the run on **any** HTTP 5xx response, uncaught JS exception, browser console error, blank page, or unreplaced `__TOKEN__` template macro. Adding a new top-level page to `nav.py` is automatically covered without touching the smoke. Playwright tracing is started for every test and only kept on failure (full `trace.zip` + `screenshot.png` under `tests/e2e/artifacts/<test-id>/`).
- **Local pre-PR runner** (#159): `scripts/test-smoke-local.sh` is an idempotent dev runner — installs Playwright + Chromium if missing, starts uvicorn against a tmp DB, waits for `/health` (45 s timeout, dies fast on uvicorn exit), runs unit + smoke suites, and tears the app down on EXIT/INT/TERM.
- **GitHub Actions `pr-tests` workflow** (#159): runs the same flow on `pull_request` + push to main and uploads `tests/e2e/artifacts/` on failure as `pr-tests-artifacts` (screenshots, traces, app.log). New "Pre-PR smoke" section in the README documents the runner, env knobs, artefact paths, and the required CI check.

### Fixed
- **Test cleanup** (#158): closed two CodeQL alerts on `tests/test_cli_args_matrix.py` — dropped the unused `_TAGS_SHARED` global (`py/unused-global-variable`) and the dead `del cache_dir, os` line (`py/unnecessary-delete`).

### Security
- **Stack-trace exposure on `/review/api/open-in-photos`** (#160, [CodeQL alert #147](https://github.com/kurok/pyimgtag/security/code-scanning/147)): the endpoint used to surface the verbose AppleScript stderr (which can include `osascript` line / column references) directly in the JSON response body. The detailed error is now logged server-side and the client receives one of a small set of stable category strings — `image_not_found` / `platform_unsupported` / `photos_timeout` / `photos_unavailable` / `photos_error` — so a script-level trace never reaches the browser.
- **Empty-except annotation** (#160, [CodeQL alert #148](https://github.com/kurok/pyimgtag/security/code-scanning/148)): added an explanatory comment to the artefact-capture `except` in `tests/e2e/conftest.py` documenting why we silently swallow there (a screenshot failure must not mask the real test failure that pytest already records).

## [0.11.2] - 2026-05-03

### Fixed
- **Judge dashboard reads the CLI's progress DB** (#155): `pyimgtag judge` now opens a `ProgressDB` even when `--db` is omitted (it used to silently skip writes), and the bundled webapp passes `args.db` through `create_unified_app(db_path=…)` so the dashboard, Query, and Judge pages all read from the same file the CLI is writing to. Fixes the "0 scored" dashboard while a CLI run was actively producing scores.
- **AppleScript cold-start retry** (#155): `read_keywords_from_photos()` now retries once after a 1.5 s sleep when the Photos.app bridge returns `None`. The first call into a freshly-launched Photos used to fail with "append mode: failed to read existing keywords, write aborted" for one image even when subsequent calls worked.
- **About page wiki section** (#155): GitHub serves the wiki with `X-Frame-Options: DENY`, so the embedded iframe always rendered as a broken-document icon. Replaced it with a CTA panel that links to the wiki landing page and the use-case diagrams in a new tab.

## [0.11.1] - 2026-05-03

### Tests
- **Per-subcommand CLI argument matrix** (#153): new `tests/test_cli_args_matrix.py` adds 181 fast contract tests covering every flag on every subcommand. For each flag we pin the argparse default, the destination attribute name, and a representative round-trip value, plus a top-level dispatch table that verifies `main()` routes the parsed args to the declared handler. Catches silent default changes, flag renames, and dispatch regressions at PR time without running any side effects (no DB, no Ollama, no network).

## [0.11.0] - 2026-05-03

### Added
- **Query page judge filter + sort + Judge column** (#151): three new filter inputs (`Judge ≥`, `Judge ≤`, `Judged`) and a `Sort` dropdown plumbed through `query_images(sort=…)` with a whitelisted ORDER BY (path / newest / oldest / `judge_desc` / `judge_asc`). Results table gains a colour-coded Judge column with the model's reason in the tooltip.
- **Hover thumbnail** on Query result rows (#151): hovering a row renders a 280×280 preview next to the cursor, reusing the `/review/thumbnail` endpoint (which routes user input through the DB-stored path).
- **About page** (`/about`) (#151): installed version, latest PyPI release, up-to-date / update-available status, curated repo + wiki links, and an embedded wiki iframe. New `GET /about/api/version` endpoint returns `{installed, latest, update}` cached for an hour.
- **Version chip on every page** (#151): every nav now carries a clickable `vN.N.N` chip that points at `/about`. When the cached PyPI lookup says a newer release is available the chip turns accent-coloured with an upward arrow.
- **CLI startup banner** (#151): `pyimgtag <subcommand>` runs a best-effort PyPI version check and prints a single-line nag to stderr if a newer release is on PyPI. Suppressed by `PYIMGTAG_NO_UPDATE_CHECK`.

### Fixed
- **`pyimgtag faces import-photos`** (#150): photoscript's `PhotosLibrary` does not expose a `persons()` method; the importer now walks `library.photos()` and reads `Photo.persons` (a list of name strings) to build the `name → uuids` map. Defensive guards skip iCloud-only / AppleScript-broken photos so a single bad row no longer aborts the whole import.

## [0.10.0] - 2026-05-03

### Changed
- **Judge prompt switched to `{score, reason}`** (#148): the model now returns a single integer 1–10 plus a 2–4-sentence reason, rather than 13 per-criterion sub-scores plus a verdict. The internal evaluation axes (Impact / Creativity / Storytelling, Technical Quality, Composition) and the 1-3 / 4-6 / 7-8 / 9 / 10 banding are spelled out in the prompt so scoring is reproducible.
- The legacy 13-criterion shape is still parsed for back-compat with rows already in the DB and any user still issuing the older prompt manually. New rows fan the integer score across every per-criterion field so weighted/core/visible math is a no-op.

### Added
- `JudgeScores.reason` carries the model's natural-language justification. It surfaces in `--verbose` CLI output, in `--output-json`, on the `/judge` web page, and in the `/review` badge tooltip — but is **never** written to image tags or EXIF/sidecar metadata. Apple Photos write-back still emits exactly one keyword (`score:N`).
- DB migration v7 adds a `reason` TEXT column on `judge_scores`. `save_judge_result` writes it; `get_judge_result` / `get_all_judge_results` / the `/review` LEFT JOIN return it (`judge_reason` field on review API items).

### Fixed
- Removed unused `_INTERNAL_PREFIXES` constant in `tests/test_webapp_smoke.py` (closes [CodeQL alert #144](https://github.com/kurok/pyimgtag/security/code-scanning/144), `py/unused-global-variable`).

## [0.9.0] - 2026-05-03

### Added
- **Judge score on review cards** (#145): `progress_db.get_image[s]` LEFT JOIN `judge_scores` so the weighted score and verdict come back next to each image. The review grid renders a corner badge `N/10` colour-coded by tier (green ≥8, amber ≥6, red below) with the verdict in the tooltip.
- **`--skip-judged` flag on `pyimgtag judge`** (#145): images already in `judge_scores` are skipped without invoking the model, so a repeat run picks up where the last one left off instead of rescoring from scratch.
- **End-to-end webapp smoke suite** (#145): 37 in-process FastAPI `TestClient` tests run on every CI matrix entry. They hit every page and JSON endpoint, reject HTML responses with leftover `__FOO__` template tokens, crawl every same-origin link to catch dead routes, and pin API field shapes the JS depends on (the `tags`-as-JSON-string regression that turned every chip into a single character would now fail at PR time).
- **Parser-error log** (#145): when `_parse_response` / `_parse_judge_response` give up, the full raw model reply is appended to `./pyimgtag-parse-errors.log` (override via `PYIMGTAG_PARSE_ERROR_LOG`) so users can post-mortem the actual text the model returned.

### Security
- **CodeQL `py/path-injection` (HIGH)** in `/review/thumbnail` and `/review/original` (#146, alerts [142](https://github.com/kurok/pyimgtag/security/code-scanning/142) + [143](https://github.com/kurok/pyimgtag/security/code-scanning/143)): the request `path` query parameter used to flow into `Path.is_file()` / `Path.read_bytes()` / `Image.open()`. Both endpoints now use the request value purely as a SQL lookup key and read the file using the path the DB returned (DB column was set by pyimgtag itself when it scanned the file).
- **`SECURITY.md` supported-versions table refreshed** to make 0.8.x / 0.9.x the supported line and explicitly mark older lines as superseded.

## [0.8.3] - 2026-05-03

### Fixed
- **Truncated model JSON no longer turns the whole image into an error row** (#143). The Ollama `num_predict` cap is bumped from 512 → 1024 tokens (the full tag-image schema regularly spans 700–900 tokens once the model adds whitespace and a short `text_summary`), and a new `_repair_truncated_json` walks the candidate prefix tracking string/escape/brace state, trims back to the last completed top-level value, and synthesises the missing closers — partial responses now round-trip through `_parse_response` with every field that was actually emitted. After upgrading, run `pyimgtag reprocess --status error` and re-run the same source to retry rows that previously errored.

### Changed
- "Could not parse JSON from model response" errors now embed a short prefix of the raw text (#143) so users can tell truncation from prose-only refusals from outright nonsense without opening a debugger.

## [0.8.2] - 2026-05-03

### Added
- **Tag-click search across the web UI** (#141): three places now route a tag click to `/query?tag=<name>` with the filter pre-filled and auto-applied — the `/tags` page (each tag name is a link), the review grid (each tag chip's label is a link; the × button still removes the tag), and the Query results table (each chip in the Tags column is a link). The Query page reads `tag`, `has_text`, `cleanup`, `scene_category`, `city`, `country`, `status`, and `limit` from `window.location.search` on load and fires the search if any preset is present.

## [0.8.1] - 2026-05-03

### Fixed
- **Review grid tag chips** (#139): `progress_db._image_row_to_dict` returned `tags` as the raw JSON string. The review JS iterated `img.tags`, so `for t of '["bird"]'` rendered each character (`[`, `"`, `b`, …) as its own removable chip. `tags` is now the parsed list; `tags_list` is kept as a back-compat alias.
- **Broken-image grid** (#139): when `/thumbnail` returns 404 (path moved, decode failure), the review grid swaps the broken-image element for a labelled filename placeholder instead of rendering a wall of Chrome icons.
- **Query "Location" column** (#139): the table read non-existent `city` / `country` keys; uses `nearest_city` / `nearest_country` so geocoded locations actually render.

### Added
- **Click-to-zoom lightbox** (#139): clicking any thumbnail (real or fallback) opens a fullscreen lightbox; ESC or backdrop closes.
- **"Open original" link** (#139): each card has a link to a new `GET /review/original?path=<absolute path>` endpoint that streams the actual image bytes (decoding HEIC/RAW to JPEG on the fly).
- **Sort + per-page selectors** (#139) on the review toolbar: path A–Z / Z–A, name A–Z / Z–A, newest / oldest by `processed_at`; per-page 25 / 50 / 100 / 200. Plumbed through a whitelisted `get_images(sort=…)`.
- **Top-of-grid pagination bar** (#139) mirroring the bottom bar.
- **Errors filter** (#139): new "Errors" pill on the review toolbar surfacing `status='error'` rows; each card renders the recorded `error_message`.
- **Status column on the Query page** (#139) plus inline `error_message` for error rows so failures are actually visible.
- **Dashboard click-through** (#139): the current-item path and recent-list paths on the live dashboard are now anchor tags pointing at `/review?file=<path>` for instant drill-down to the worker's output for that file.
- **Single-image deep link** (#139): `GET /review/api/images?file=<absolute path>` returns just that one row (or empty) for the dashboard click-through and any other consumer.

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
