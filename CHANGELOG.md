# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Name auto clusters from a People-view screenshot (`faces capture-names`)**: the "screen OCR" path. Detects + embeds the face under each Apple Photos People tile, reads the caption beneath it with Apple's Vision OCR, pairs them by position, and applies each recognized name to the matching auto cluster (same conservative matcher as `match-references`). Source is an existing `--screenshot FILE` or a fresh `--live` capture (drives Photos + `screencapture`). Dry-run by default (`--apply` to write); `--threshold` tunes match strictness; `--languages ru-RU,en-US` steers Vision for non-Latin names (verified: Cyrillic names like "Юрій Рябіков" read verbatim). macOS-only; install with `pip install 'pyimgtag[ocr]'` (adds the `[ocr]` extra: pyobjc Vision + Quartz). New module `pyimgtag.face_ocr` with a pure, unit-tested face↔caption pairing core. This is the third cluster-naming option alongside `import-photos` (Photos DB via osxphotos) and `match-references` (labeled image folder).

## [0.26.0] - 2026-06-02

### Added
- **Parallel `faces scan` (`--jobs`/`-j`)** (#273): detection + embedding is the CPU-bound bottleneck — especially `--quality accurate` (`num_jitters=10` encodes each face 10×) over a large library — and it ran single-threaded. `faces scan -j 8` now fans detection/encoding across worker processes for a near-linear speedup; `-j 0` auto-uses one worker per core. Workers do no DB I/O — they return the detected faces/embeddings and the main process performs all SQLite writes (single writer), so a bounded backlog keeps memory flat and incremental resume / not-downloaded skips still work. Default stays `-j 1` (serial, unchanged). `pyimgtag.face_embedding.detect_and_encode` is the reusable per-image worker.

## [0.25.1] - 2026-06-02

### Fixed
- **Installing the web extras no longer forces `httpx2`/`h11 0.16` on your environment** (#271): `httpx2` is only used by starlette's `TestClient` (tests), never by the running web app, but it was listed in the runtime `[review]` and `[all]` extras. Installing those upgraded `h11` to 0.16 and broke any co-installed classic `httpx`/`httpcore` (which pins `h11<0.15`). `httpx2` is now a `[dev]`-only dependency, so `pip install 'pyimgtag[review]'` / `[all]` (and `[photos-db]`) install cleanly alongside a classic-httpx environment.

## [0.25.0] - 2026-06-02

### Added
- **`import-photos` reads the Photos library DB (osxphotos)** (#268): when the `[photos-db]` extra is installed, `faces import-photos` now reads each person's exact name and photo UUIDs straight from the Apple Photos library database via osxphotos — no AppleScript, so it works on Photos builds where the `person` class isn't scriptable (the `-2741` "Expected class name" failure that returned 0 people), and names come through verbatim (Cyrillic etc.) instead of via fragile OCR. Preferred over the AppleScript/photoscript paths, which remain as fallbacks. New `--library` flag (default: auto-detect the system library); install with `pip install 'pyimgtag[photos-db]'`.
- **Name auto clusters from labeled reference faces** (#267): new `pyimgtag faces match-references <dir>` matches each auto-clustered person to the nearest *labeled* reference face by embedding and applies the name (merging into an existing trusted person of that name when one exists). Drop one image — or a sub-folder of images — per person into `<dir>` (`Alice.jpg` or `Alice/01.jpg`); dry-run by default, `--apply` to write, `--threshold` to tune. This is the escape hatch for Apple Photos libraries that can't be enumerated via AppleScript (the `-2741` failure that makes `import-photos` return 0), and the foundation for the upcoming screenshot/OCR capture. Backed by `pyimgtag.face_naming`.

### Fixed
- **Faces API edge cases hardened** (#269): assigning faces to a nonexistent person now returns `404` instead of silently creating a dangling assignment, and merging into a nonexistent target returns `404` instead of `500`. Surfaced while expanding web-route test coverage.
- **`faces scan` skips photos not downloaded locally** (#268): on an iCloud-optimized library the originals are evicted, so their paths are absent on disk. The scan now detects that and skips them quietly — they are not detected, not marked scanned (so a later run picks them up once downloaded), and reported as "N not downloaded locally (skipped)" rather than counted as errors.
- **`faces scan` summary read as "detected 0 faces" when images were merely already scanned** (#266): a re-scan skips images detected in a previous run, but those skips were counted toward "Scanned N images, detected 0 faces" — indistinguishable from a real detection failure. The summary now reports newly-scanned images separately and, when any were skipped, says how many were already scanned and points at `faces reset-untrusted --yes` to re-detect.

## [0.24.1] - 2026-06-02

### Fixed
- **`import-photos` left named/Photos people empty after a scan** (#264): the background re-cluster that runs during `faces scan` grabs freshly-detected faces into `Person N` auto clusters, but `import-photos` only linked faces that were still *unassigned*, so a named person's faces — now sitting in an auto cluster — were never linked and the person stayed at 0 faces. `import-photos` now **reclaims** a UUID-matched face from an auto cluster (the Apple Photos tag is authoritative); faces already assigned to another trusted/confirmed person are still never touched. Backed by `ProgressDB.get_auto_person_ids`.

## [0.24.0] - 2026-06-02

### Fixed
- **`reprocess` wiped the whole DB with no confirmation** (#262): `pyimgtag reprocess` with no `--status` deletes every `processed_images` row (clearing all tagging/geocoding progress). It now requires an explicit `--yes`; without it the full reset is refused with a message pointing at `--status error` for a targeted reset. `--status`-scoped resets are unchanged.
- **Photos delete could remove the wrong photo on a filename collision** (#262): when the delete lookup fell back to a filename scan and several Photos items shared that name, it spotlighted and Cmd+Deleted an arbitrary first match. The delete script now errors (`Ambiguous: multiple Photos items…`) instead of guessing; non-destructive lookups (reveal/read/write-back) keep the take-first behaviour.
- **Faces grid lost its sort/filter when returning from a cluster** (#261): the persons-grid sort, filter, and page lived only in JS memory, so opening a person ("see all") and pressing Back — or reloading — reset the grid to the default sort. The view state is now mirrored into the URL query string (`?filter=&sort=&offset=`) and restored on load, so Back and reload keep the chosen sort.
- **`cleanup-drift --prune` could delete almost the entire DB** (#260): the Apple Photos membership probe stores each item's id as the PHAsset localIdentifier `<UUID>/L0/001` plus its *original* filename (e.g. `IMG_1234.HEIC`), but a photo inside the library lives on disk at `originals/X/<UUID>.<ext>` — so every library original matched neither key, was classified `photos_missing`, and got pruned. Fixes: (1) the membership set now also indexes the bare `<UUID>` prefix, so library originals are recognised as present; (2) a "successful" probe that returns **zero** media items is now treated as a degraded probe (disk-only check) instead of marking every on-disk row missing; (3) **`--prune` now deletes only `disk_missing` rows** (file genuinely gone) — the soft, false-positive-prone `photos_missing` category requires the new explicit `--prune-photos-missing` flag, and the web "prune drift" action only removes `disk_missing`. `ProgressDB.get_embeddings_for_faces`/`get_person_embeddings` unaffected.
- **`reset-untrusted` could orphan a trashed face** (#260): deleting an untrusted/auto person did not account for an ignored ("trash") face still assigned to it, leaving the face pointing at a deleted row (SQLite FK enforcement is off). It now keeps any untrusted person that still owns a surviving face.
- **Merging a person into itself orphaned its faces** (#260): `POST /api/persons/{id}/merge/{id}` (and `merge_persons(x, x)`) reassigned faces to the same id then deleted the person, orphaning every face. Self-merge is now a no-op.
- **Auto-clustering silently emptied trusted/named people** (#258): the background re-cluster that runs during every `faces scan` fed *all* face embeddings into DBSCAN — including faces already assigned to trusted, Photos-imported, or manually confirmed people — and reassigned them into fresh `Person N` auto-clusters, leaving the named person with 0 faces. A later `faces reset-untrusted` then deleted those orphaned auto-clusters, making the loss permanent (this is why named people showed "0 faces" after a reset, even though `reset-untrusted` itself correctly preserves trusted faces). Clustering now considers only **unassigned, non-ignored** faces (`ProgressDB.get_clusterable_embeddings`), so trusted people keep their faces and trashed faces are no longer resurrected into clusters.
- **Empty Photos-imported people showed misleading guidance** (#258): a named person with 0 faces (`source='photos'`) said "run faces scan to populate", but a plain scan never links faces to a named person. The faces grid now tells those people to re-run `faces import-photos` (or add faces from the person page) instead.
- **"Open in Photos" failed for library originals** (#259): photos stored inside the Apple Photos library live at `.../originals/X/<UUID>.<ext>`, and the lookup tried `media item id "<UUID>"` then a filename scan for `<UUID>.<ext>`. But Photos' `filename` is the *original* import name (e.g. `IMG_1234.HEIC`), so the scan never matched, and the bare-UUID `media item id` does not resolve on some Photos versions — producing `Photo not found: <UUID>.jpeg (-2700)`. The shared lookup now resolves a UUID stem via the full PHAsset localIdentifier `<UUID>/L0/001` first, then the bare UUID, then an `id begins with <UUID>` scan (for edited/burst renditions), and only then a filename scan. Factored into one `_lookup_block` used by the reveal, write-back, read, and delete scripts.

### Added
- **Group-photo face linking on import** (#258): `faces import-photos` no longer skips every multi-face photo. When a person has at least one reference face (a solo shot in the batch or a prior assignment), the group-photo face whose embedding best matches the person's centroid is linked — but only when it is within a conservative distance and clearly closer than the next candidate, so group shots are left for manual review rather than mis-assigned. Backed by new `ProgressDB.get_embeddings_for_faces` / `get_person_embeddings`.

## [0.23.1] - 2026-06-01

### Changed
- **Internal: resolved CodeQL code-scanning alerts in the test suite** (#256): two tests mixed `import x` with `from x import y` for the same module (`py/import-and-import-from`) — both now obtain the module via `importlib.import_module(...)`. An intentional `except SystemExit: pass` (`py/empty-except`) is now documented. Test-only; no runtime or API change.

## [0.23.0] - 2026-05-31

### Added
- **Face-detection quality controls** (#254): `faces scan` gains a `--quality {fast,balanced,accurate}` preset plus granular `--upsample`, `--num-jitters`, and `--min-face-size` flags (alongside the existing `--detection-model`/`--max-dim`). Detection previously used dlib's weakest defaults (no upsampling, no encoding jitter); upsampling finds smaller/distant faces and jitter produces more robust 128-d encodings (better clustering/matching). `accurate` uses the cnn model. `max_dim` is held at 1280 across presets so faces-UI thumbnail crops stay aligned. Invalid values are rejected before scanning.

### Changed
- **Default scan quality is now `balanced`** (#254): `faces scan` defaults to `hog, upsample 2, jitters 4` — more accurate but several times slower than before. Pass `--quality fast` to restore the previous speed. Note: already-scanned images are skipped, so to re-detect an existing library at a new quality, run `faces reset-untrusted --yes` (or `faces reset`) first.

## [0.22.0] - 2026-05-31

### Added
- **Faces cleanup commands** (#252): three new `faces` sub-actions to reset face data at three levels. `faces reset` wipes everything (all faces + embeddings, all persons including trusted/Photos, and the scan cache). `faces reset-untrusted` deletes non-trusted faces and auto-clusters while keeping trusted/named people and their faces — and the user's ignored "trash" faces — pruning the scan cache only for images that no longer have any face so they are re-detected. `faces recluster` clears auto-clusters and re-clusters from scratch (keeps trusted people; no face deletion). All three preview what would change and require `--yes` to apply, and leave image tagging/geocoding progress untouched. Backed by new `ProgressDB.reset_all_faces` / `reset_untrusted_faces` / `count_auto_persons`.

## [0.21.1] - 2026-05-31

### Fixed
- **Faces UI logged a traceback on Ctrl+C / client disconnect** (#250): stopping the faces server (or navigating away) while a thumbnail-generating request was in flight surfaced `asyncio.CancelledError` as a uvicorn "Exception in ASGI application" traceback. The five endpoints that crop thumbnails off the event loop now share one `_thumbs()` helper that catches the cancellation and returns the page with `thumb=None` (the response is discarded anyway), so a cancelled request shuts down quietly. Also removes ~40 lines of duplicated thumbnail code.

## [0.21.0] - 2026-05-31

### Added
- **Faces detail: "Add faces to this person" section** (#248): a new section under the cluster grid grows a person's cluster in place. A source toggle picks the candidate pool — *Unassigned* (faces not yet assigned to anyone) or *Biggest cluster* (faces from the largest **other** cluster, the common "one person split into two clusters" case; the hint names the source) — with a pager (40/page, highest-confidence first). Clicking a candidate face assigns it to this person (via `POST /api/faces/assign-batch`) and refreshes both the cluster and the candidate list. Backed by a new paginated, thumbnail-on-demand endpoint `GET /api/persons/{id}/candidates?source=unassigned|biggest` returning `{total, items, source_label}`.

### Changed
- **Merge-target dropdown sorted by name** (#248): the "merge into existing trusted person" `<select>` in the rename/merge modal is now sorted A–Z by name on both the grid and detail pages, so a person is easy to find in a long list.

## [0.20.1] - 2026-05-31

### Fixed
- **Faces detail "back" link reloaded the grid** (#246): the "← All Faces" link navigated (full page load) to the grid whenever `document.referrer` was empty — a new tab, a bookmark, or a referrer-stripping context — instead of going back. It now links to the grid as an href fallback and, when `window.history.length > 1`, calls `history.back()` and cancels the navigation, so it restores the grid from bfcache (scroll position, sort, page) instead of refetching.

## [0.20.0] - 2026-05-31

### Added
- **Faces person-detail page is paginated** (#244): `GET /api/persons/{id}/faces` previously returned every face for the person and generated a thumbnail for each, so a person with thousands of faces loaded and base64-encoded them all at once. The endpoint now takes `offset`/`limit` (default 60, clamped to ≤200) and returns `{total, items}` sorted highest-confidence first, thumbnailing only the requested page (matching the unassigned/trash endpoints). The detail page renders one page at a time with a Prev/Next pager and the full count; the hero thumbnail shows only on the first page.

### Fixed
- **Photos-importer test polluted module identity, failing CI on Linux/Windows** (#244): `TestLazyPhotoscriptImport` re-imported `pyimgtag.photos_faces_importer` and restored only `sys.modules`, leaving the parent-package attribute bound to a throwaway module object. A later test's `patch("pyimgtag.photos_faces_importer.…")` then missed and invoked the real `/usr/bin/osascript` — absent on Linux/Windows runners (→ `RuntimeError`), and a multi-minute Photos query on macOS. The test now restores the parent-package attribute and mocks at the `_run_bulk_osascript` seam. Test-only.

## [0.19.1] - 2026-05-31

### Changed
- **Test coverage raised to 100%** (#242): added ~335 tests across the modules with coverage gaps (run/faces/applescript/progress_db/webapp routes/clients/judge/exif/etc.), lifting project coverage from 87% to 100%. Tests mock at the boundary (subprocess, HTTP, fastapi TestClient, and optional deps) so paths execute in CI without the optional extras. No runtime or behavior changes; the only `src/` edits are `# pragma: no cover` comments on optional-dependency registration and core-dep import-fallback branches.

## [0.19.0] - 2026-05-31

### Changed
- **Updated all dependencies and tooling to latest** (#239): raised the `>=` floors for runtime and extras (requests 2.34, Pillow 12, exifread 3.5, pillow-heif 1.3, scikit-learn 1.8, fastapi 0.136, uvicorn 0.48, pydantic 2.13, httpx2 2.2, rawpy 0.27) and dev tooling (pytest-xdist 3.8, pytest-cov 7, mypy 2.1, ruff 0.15, bandit 1.9, pip-audit 2.10, playwright 1.60, pytest-playwright 0.8); refreshed `uv.lock`. The deliberate `setuptools<81` cap is preserved. Bumped pre-commit hooks (ruff v0.15.15, pre-commit-hooks v6.0.0) and the codecov CI action. No code or behavior changes; full suite, mypy, bandit, and `pip-audit` all clean.

## [0.18.2] - 2026-05-31

### Fixed
- **OpenAPI schema generation crashed** (#237): four route handlers were annotated `-> Response` where `Response` is only importable under `TYPE_CHECKING` (`routes_faces` `face_preview`/`person_detail`; `routes_review` `get_thumbnail`/`get_original`). FastAPI resolves return-type hints against module globals when building the schema, so `app.openapi()` raised `PydanticUserError` (`ForwardRef('Response')`). Latent in practice — request handling was unaffected and the app disables `/docs` + `/openapi.json` — but enabling docs would 500. The unresolvable return annotations are dropped; behavior is unchanged.

## [0.18.1] - 2026-05-31

### Fixed
- **Faces UI: "Assign to person" / "New person" / "Dismiss" returned 422** (#235): `POST /api/faces/assign-batch` declared its body as a function-local pydantic model, which does not resolve under this module's `from __future__ import annotations`, so FastAPI treated the body as a query parameter and rejected every request. The fields are now declared with `Body(...)`; the JSON contract and behavior are unchanged.

## [0.18.0] - 2026-05-31

### Added
- **Faces grid: sort by face count** (#233): a sort control on `/faces` (Default / Most faces / Fewest faces / Name A-Z). `GET /api/persons/with-faces` gains a `sort` param (`count_desc` / `count_asc` / `name_asc` / `default`) applied to the whole filtered set before pagination.
- **Faces grid: multi-select confirm/delete** (#233): a checkbox per person card and a bulk-action bar ("Confirm selected" / "Delete selected"), backed by new `POST /api/persons/confirm-batch` and `POST /api/persons/delete-batch` endpoints and the `ProgressDB.confirm_persons` / `delete_persons` methods (one transaction each).

## [0.17.4] - 2026-05-31

### Fixed
- **Error-handling quality sweep — narrower catches and surfaced bad states**:
  - `exif_reader`: replaced bare `except Exception` with typed exception tuples so `KeyboardInterrupt`/`MemoryError` are no longer masked by the EXIF fallback chain.
  - `geocoder`: guard `GeoResult(**cached)` against schema-changed cache entries — a stale cache dict with unknown keys now re-fetches instead of raising `TypeError`.
  - `cache`: broaden the `_save` cleanup so a non-`OSError` write failure still removes the leftover `.tmp` file; write with explicit `encoding="utf-8"`.
  - `dedup`: `hamming_distance` now raises a clear `ValueError` on an invalid hex hash instead of a cryptic imagehash error.
  - `progress_db`: raise an explicit `RuntimeError` if a cursor's `lastrowid` is unexpectedly `None` instead of returning it typed as `int`.
- Added regression tests (invalid hash, stale-cache re-fetch, cache-hit skips network, tmp cleanup on non-`OSError`) and fixed two tests that referenced optional packages not installed in the dev environment.

## [0.17.3] - 2026-05-30

### Security
- **Resolve all open CodeQL code-scanning alerts** (#230):
  - `py/log-injection` (#155, #159): the faces preview and person-detail log calls included request-influenced values. The preview log now strips CR/LF from the image path, face id, and error text; the person-detail breadcrumb is now a static message with no request value.
  - `py/ineffectual-statement` (#156–#158): the `ImageClient` Protocol method bodies used bare `...`, flagged as no-effect statements. Replaced with one-line docstrings (valid Protocol stubs that also document the contract). No behavior change.

## [0.17.2] - 2026-05-30

### Fixed
- **Faces UI: "Person not found" on a deleted/re-clustered person** (#228): opening `/faces/persons/<id>` for a person no longer in the DB dumped a raw `{"detail":"Person not found"}` JSON body. Auto-clustering deletes and recreates persons (`clear_auto_persons` + re-insert), so a grid card can link to an id that was since re-clustered away (the large person ids users see, e.g. 160765, are a symptom of that churn). The page route now redirects (303) back to the faces grid instead of raising a 404, so a stale card link lands the user on a fresh list.

## [0.17.1] - 2026-05-30

### Documentation
- **Per-subcommand `-h` help** (#226): `pyimgtag <command> -h` now shows an overview description and a worked-example block for every top-level subcommand (`run`, `status`, `reprocess`, `preflight`, `cleanup`, `cleanup-drift`, `review`, `faces`, `query`, `judge`, `tags`), instead of just the usage line and flag list. Implemented via a `_SUBCOMMAND_HELP` table and a `_sub()` helper that uses `RawDescriptionHelpFormatter` to preserve example formatting. The top-level `pyimgtag -h` summary listing is unchanged.

## [0.17.0] - 2026-05-30

### Added
- **`run --skip-existing`** (#223): fully skip any unchanged photo already complete in the DB (status ok + non-empty tags) — no EXIF re-read, geocoding, write-back, or DB rewrite. The fast path for resuming a large, mostly-tagged library, where `--resume-from-db` was slow because it re-read EXIF (an exiftool subprocess per photo), re-geocoded, and — with `--write-back` — re-wrote keywords to Apple Photos via an osascript subprocess per photo. The skip decision is one indexed SELECT plus one `stat()` via the new `ProgressDB.is_complete_cached()`. Takes precedence over `--resume-from-db` and forces the linear path. Cached photos are intentionally not (re)written even with `--write-back`/`--write-exif`; a startup notice makes this explicit.

## [0.16.10] - 2026-05-30

### Fixed
- **Trusted persons showed 0 faces after `faces scan`** (#220): `import-photos` was fully idempotent — when a Photos.app person already existed it skipped face assignment entirely. Running `import-photos` before `faces scan` left trusted persons with 0 faces permanently. Re-running `import-photos` after scan now links newly-detected unassigned faces to existing trusted persons. Face assignments also no longer overwrite existing ones.

## [0.16.9] - 2026-05-30

### Fixed
- **`delete_image_rows` non-atomic row count** (#217): the before/after `COUNT(*)` sandwich was non-atomic (another writer between the two counts could return a wrong value) and `executemany` only reports the last statement's `rowcount`. Replaced with a single `DELETE … WHERE file_path IN (…)` whose `.rowcount` is accurate and atomic.
- **`get_cleanup_candidates` silently swallowed exceptions** (#217): a blanket `except sqlite3.Error: return []` masked disk-full, corruption, and schema errors as empty results. Removed — real errors now propagate to the caller.
- **`get_persons` N+1 query pattern** (#217): one `SELECT id FROM faces WHERE person_id = ?` per person in a Python loop caused O(N) DB round-trips. Replaced with a single `SELECT person_id, id FROM faces WHERE person_id IS NOT NULL` + `defaultdict` grouping, eliminating all extra queries.
- **`iter_image_paths` OFFSET pagination** (#217): `LIMIT/OFFSET` caused full-table re-scans per page and could skip or duplicate rows when concurrent deletes occurred in WAL mode. Replaced with a keyset cursor (`WHERE file_path > last_seen ORDER BY file_path`) — O(log N) per page and correct under concurrent writes.
- **`get_stats` SQLite-specific `SUM(bool)` idiom** (#217): `SUM(status='ok')` relies on SQLite boolean-as-integer coercion and is non-standard. Replaced with three explicit `SELECT COUNT(*) WHERE status = ?` queries — portable and unambiguous.
- **`_migrate` partial-migration crash safety** (#217): each version's DDL statements and `PRAGMA user_version` update are now wrapped in `SAVEPOINT … RELEASE / ROLLBACK TO` so a crash mid-version rolls back atomically instead of leaving the DB in a partially-migrated state.

## [0.16.8] - 2026-05-30

### Fixed
- **`faces scan` crashed on per-file errors** (#215): any exception during file processing (e.g. `OSError: [Errno 28] No space left on device` when HEIC conversion runs out of temp space) terminated the entire scan session with a traceback. Per-file processing is now wrapped in try/except: disk-full (`ENOSPC`) prints a clear message and stops cleanly; all other errors print `filename: skipped (reason)` and continue to the next file. The final summary line includes the error count: `Scanned N images, detected M faces, K error(s) skipped.`

## [0.16.7] - 2026-05-30

### Fixed
- **Faces detail page showed "(unlabelled #undefined)"** (#213): `load()` called `.json()` without checking `response.ok`, so a 404 (person deleted or merged) produced `{"detail":"not found"}` with no `id` field, rendered as "(unlabelled #undefined)". Now redirects to the faces list if the person no longer exists.
- **"← All Faces" always returned to page 1** (#213): the back link used a hardcoded `href` to the faces root. Changed to `history.back()` so it returns to the exact page, filter, and offset the user came from. Falls back to the faces root if there is no browser history.
- **NaN guard in rename/merge modal** (#213): added `!isNaN()` check on the parsed target person id to prevent edge cases where a stale select value produces `NaN`.

## [0.16.6] - 2026-05-29

### Added
- **Faces: empty-face placeholder** (#211): trusted persons with 0 scanned faces (Photos-imported before `faces scan` has run) now show *"No faces scanned yet — run faces scan to populate"* instead of a blank card.
- **Faces: Dismiss faces to trash** (#211): in the Unassigned view, select faces and click "Dismiss (move to trash)" — dismissed faces get `ignored=1` and are excluded from the unassigned pool forever. New **Trash** filter tab shows all dismissed faces; select + "Restore selected" returns them to the unassigned pool. DB migration v10: `ALTER TABLE faces ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0`.
- **Faces: Rename modal shows trusted person dropdown for merge** (#211): the Rename button (both main page and detail page) now shows a second control — *"Or merge into existing trusted person"* — populated with all trusted+labelled persons. Selecting one merges the current cluster into that person and navigates to their detail page. Selecting from the list also auto-fills the name input.

### Fixed
- **`faces scan` resume: zero-face images no longer re-processed** (#211): images where no face was detected were re-scanned on every run because nothing was recorded in the `faces` table to skip them. A new `face_scanned_images` table (migration v11) records every scanned path regardless of face count; subsequent `faces scan` runs skip those images immediately. First run after upgrade re-scans zero-face images once to populate the table.

## [0.16.5] - 2026-05-29

### Added
- **Faces filter bar** (#209): `All | Trusted | Auto | Unassigned` toggle buttons. Trusted/Auto filter the person grid server-side; Unassigned switches to a dedicated face selection view.
- **Unassigned faces view** (#209): 40 faces per page with multi-select (click to toggle, blue outline when selected). Actions: **Assign to person…** (dropdown of existing persons) or **New person from selected** (optional name; named persons are immediately trusted). Select all / Clear buttons for the current page. Paginated with prev/next.
- New API: `GET /api/faces/unassigned?offset&limit` → `{total, items}` with thumbnails.
- New API: `POST /api/faces/assign-batch` → assigns faces to an existing person or creates a new one.

## [0.16.4] - 2026-05-29

### Added
- **Faces person detail page** (#207): clicking a person name in the cluster card opens `/faces/persons/{id}` — a dedicated page showing all faces (no 8-limit), sorted by confidence with hero thumbnail. Actions: Confirm cluster, Rename, Delete. Hover preview works the same as the main page.
- **Confirm hidden for large clusters on main page** (#207): Confirm button is only shown when `face_count ≤ 8` (all faces visible). Larger clusters show "View all & confirm" instead, linking to the detail page.
- **"Showing 8 of N — click to see all" hint** (#207): shown on cluster cards with more than 8 faces.

### Fixed
- **About page showed stale PyPI version after upgrade** (#207): the in-process cache held the pre-upgrade PyPI version for up to 1 hour. If the installed version is newer than the cached PyPI value, the cache is now invalidated and a fresh lookup is forced immediately.
- **CodeQL XSS: person_id sanitized through `int()`** (#207): `person_id` from the URL path was substituted into the HTML template via `str(person_id)`. Coercing through `int()` first (`str(int(person_id))`) breaks the taint chain — the result is guaranteed to be digit-only.

## [0.16.3] - 2026-05-29

### Fixed
- **Faces hover preview overlapped cards below** (#205): preview popup `top` was not clamped to the viewport height, so thumbnails near the bottom of the page produced previews that extended below the fold and covered neighbouring cards. Fixed: `top = min(max(rect.top, 8), window.innerHeight - 340)`.

### Added
- **Faces hero thumbnail** (#205): faces in each cluster are sorted by confidence descending; the highest-confidence face is shown at 100×100 px with an accent border. Tooltip includes the confidence score.
- **Faces Confirm button** (#205): each AUTO cluster card has a green "Confirm" button. Clicking it sets `confirmed=1, trusted=1` so the cluster survives the next re-clustering pass. Badge changes AUTO → TRUSTED and the button disappears.
- **Rename also confirms** (#205): giving a person a non-empty name via Rename implicitly sets `confirmed=1, trusted=1` — a named cluster is treated as manually verified and survives re-clustering without requiring a separate Confirm click.

## [0.16.2] - 2026-05-29

### Performance
- **Faces page: N+1 requests → 1 paginated request** (#203): the JS previously made one HTTP request per person sequentially; now a single `GET /api/persons/with-faces?offset=0&limit=10` returns a full page. All 10 persons' thumbnails are generated in parallel on the server (`asyncio.gather` + `asyncio.to_thread`), so page load time equals the slowest single person rather than the sum of all.
- **Faces page: pagination** (#203): 10 persons per page with `← Previous | 1–10 of 31 | Next →` controls. Pager is hidden when total ≤ 10.
- **Thumbnail and preview I/O off event loop** (#203): `get_person_faces` now uses `asyncio.to_thread` so image file I/O no longer blocks FastAPI for concurrent requests.

## [0.16.1] - 2026-05-29

### Fixed
- **Faces dashboard: dark/wrong thumbnail squares** (#199): face bounding boxes are stored in 1280px detection space but were applied directly to the full-resolution image, producing crops from the wrong location. `face_thumbnail_b64` now scales bbox coords by `max(iw, ih) / 1280` before cropping. HEIC images are also handled via `sips` fallback.
- **Faces dashboard: hover preview showed full image with tiny red box** (#199): preview endpoint now crops to the face region with 80% padding and returns a 400px-max zoomed view instead of the full image. Hover overlay hides automatically on image load error.
- **`faces scan` warning: `pkg_resources is deprecated as an API`** (#198): setuptools emits this warning with `stacklevel=2`, attributing it to `face_recognition_models/__init__.py`, not `pkg_resources`. The filter now matches on message text instead of module name.

### CI
- **Upgrade `deploy-pages` v4 → v5 and `upload-pages-artifact` v3 → v5** (#200): Node 24 runtime; suppress MkDocs `not_found` link warnings from root files included via snippets.

## [0.16.0] - 2026-05-29

### CI
- **Restore `push: tags` trigger in publish workflow** (#196): `release: published` does not fire when a release is created by a GitHub Actions workflow (GITHUB_TOKEN cannot trigger further workflows), causing PyPI publish to silently not run. Restored `push: tags: v*` as the primary publish trigger; added `skip-existing: true` so a parallel `release: published` run does not fail with "file already exists".
- **Fix setup-uv cache collisions and upgrade Docker actions to Node 24** (#195): added `cache-suffix: ${{ github.job }}` to all `setup-uv` steps so parallel jobs no longer race on the same cache key; upgraded `docker/setup-buildx-action` v3 → v4.1.0 and `docker/build-push-action` v6 → v7.2.0 (both now use Node 24 runtime).

## [0.15.1] - 2026-05-29

### Fixed
- **Add comment to empty `except` in `ollama_client` to satisfy CodeQL** (#195): CodeQL flagged a bare `except: pass` block; added an explanatory comment to suppress the alert without changing behaviour.

### CI
- **Fix `setup-uv` `enable-cache` input name and upgrade `actions/cache` to v5** (#194): corrects a deprecated input key in `pr-tests.yml` and `python-package.yml`; bumps `actions/cache` from v3/v4 to v5 across both workflows.

## [0.13.7] - 2026-05-18

### Security
- **Upgrade urllib3 2.6.3 → 2.7.0** (#185): fixes two high-severity CVEs — sensitive headers forwarded across origins in proxied redirects, and decompression-bomb safeguards bypassed in the streaming API.

## [0.13.6] - 2026-05-04

### Performance
- **Web dashboard no longer blocks on thumbnail/original requests** (#184): `get_thumbnail` and `get_original` route handlers now run PIL decode, sips fallback, and raw file I/O inside `asyncio.to_thread()` so the FastAPI event loop is never stalled by a slow image. `_serve_original()` extracted as a reusable sync helper.
- **`get_stats()` reduced from 3 SQL round-trips to 1** (#184): `SELECT COUNT(*), SUM(status='ok'), SUM(status='error') FROM processed_images` replaces three separate `SELECT COUNT(*)` queries.
- **DB migration v9: indexes on `status`, `cleanup_class`, `processed_at`** (#184): paginated queries on the Edit/Review pages that filter by status or cleanup class now hit an index instead of a full table scan.

## [0.13.5] - 2026-05-03

### Added
- **`pyimgtag cleanup-drift` subcommand + Edit page "DB drift" panel** (#182): scan the progress DB for rows whose files no longer appear in the Apple Photos library, classify them as `missing_file` (file deleted from disk), `removed_from_library` (file exists but Photos no longer indexes it), or `photo_not_indexed` (Photos accessible but membership check returned empty). `--dry-run` prints the report; `--prune` deletes the stale rows. The Edit page gains a "DB / Photos drift" section that calls `GET /edit/api/drift` (scan) and `POST /edit/api/prune-drift` (batch delete) with a live progress bar.

### Fixed
- **Dashboard Stop button** (#181): a red "Stop" button next to Pause/Unpause sends `POST /api/run/current/stop`; `RunSession.request_stop()` sets a flag and unblocks any paused worker, causing `wait_if_paused()` to raise `KeyboardInterrupt` so the existing graceful-interrupt path fires.
- **HEIC thumbnails missing in Judge/Review pages when `pillow-heif` is not installed** (#181): `_make_thumbnail` now falls back to `sips -s format jpeg -Z <size>` on macOS when PIL can't decode a `.heic`/`.heif` file, so thumbnails render and "Open original" works without the extra package.
- **Dashboard recent list shows assigned tags** (#181): each processed item now records its tags as a `detail` string; the dashboard renders them in a smaller muted line below the filename.

## [0.13.4] - 2026-05-03

### Fixed
- **`pyimgtag faces import-photos` returned 0 persons on installs where Photos.app exposes persons only at the application level** (#179): on some Photos.app builds, `every person of p` and the `persons` property fallback both return empty — persons are only accessible by walking the application-level `people`/`persons`/`every person` collection and querying `photos of p` per person. Added a third-tier bulk AppleScript (`_bulk_applescript_app_people`) that enumerates the application-level people list, walks `photos of p` for each named person, and emits `<uuid>\t<name>` rows. The collect path fires this walker when the first two per-photo approaches return zero persons, then falls back to photoscript as before. An emitted row per `(photo, person)` pair (rather than per photo) means `_parse_bulk_output` now handles both row formats.

### Docs
- **README refreshed for v0.13.3** (#178): subcommands table, extras and install instructions, webapp page descriptions, environment variables, and Apple Photos access notes updated to reflect current behaviour.

## [0.13.3] - 2026-05-03

### Fixed
- **`pyimgtag faces import-photos` couldn't compile its bulk AppleScript on every Photos.app build** (#175): on at least one user install, osascript refused the bulk script with `-2741: Expected class name but found identifier` because Photos.app's dictionary did not terminologise `person` as a scriptable class — `every person of p` then parses as "every <unknown identifier>". Split the bulk script into two variants and drive a fallback at the call site: `_bulk_applescript_every_person()` (default, photoscript-canonical) and `_bulk_applescript_persons_property()` (uses only the `persons` property + `name of (item i of _persons)` index iteration, never naming the class). When osascript returns `-2741`, the importer logs a clear "Photos.app does not expose 'person' as a scriptable class on this install (osascript -2741); retrying with 'persons' property…" line and re-runs with the property-only script before falling back to photoscript.
- **Edit page distinguishes "photo not indexed by Photos.app"** (#176): `delete_from_photos` now reports `photo_not_in_library` (instead of the misleading `photos_unavailable`) when the AppleScript filename-scan raises `Photo not found: <name>` (-2700). This happens when the file sits on disk inside the Photos library bundle but Photos.app no longer indexes it as a media item — orphaned originals after a manual delete in Photos. The category is checked before the generic `applescript`/`osascript` branch because the stderr begins with `AppleScript error …`.

### CI
- **Trim the test matrix from 9 → 5 cells** (#176): drop `macos-latest / 3.11`, `windows-latest / 3.11`, and `windows-latest / 3.12`. Linux still spans all three minors; macOS keeps 3.12 + 3.13 for the AppleScript / Photos-bridge stack; Windows keeps 3.13, which already exercises the Starlette TestClient socket path the dropped cells duplicated.

## [0.13.2] - 2026-05-03

### Fixed
- **Windows TestClient socket exhaustion in `test_routes_query`** (#169): the file built a fresh `TestClient(app)` per test without entering its context-manager lifecycle, so Starlette's in-process event loop never released its sockets between tests. Under xdist's parallel fan-out the windows-latest / 3.13 runner exhausted TCP/IP buffer space (`OSError [WinError 10055]`). Refactored to a `client_factory` fixture that calls `TestClient(app).__enter__()` and tears every client down on fixture exit — same lifecycle pattern `tests/test_webapp_smoke.py` already uses.
- **`face_recognition_models` blocked by setuptools 81+** (#170): `setuptools` 81.0.0 removed the bundled `pkg_resources` module while leaving the install metadata intact — `pip show setuptools` succeeds but `import pkg_resources` raises `ModuleNotFoundError`, breaking `face_recognition_models` at import time. Capped `[face]` and `[all]` extras at `setuptools>=68.0,<81` so a fresh install keeps a working `pkg_resources`. The pre-flight error message in `_face_dep_check.py` now names the version constraint explicitly and recommends `pip install 'setuptools<81'`.
- **Photos delete via UI scripting** (#171): Apple Photos.app's `delete` AppleScript verb has been broken since Catalina — `delete (media item id "X")` consistently returns `-10000 AppleEvent handler failed`, which surfaced as a flood of "edit job: delete_from_photos failed for …" lines on the bulk Edit page. Replaced with the working approach: `activate` Photos, `spotlight theItem` (selects in UI), then send `Cmd+Delete` (ASCII 127 with `using command down`) via `System Events` into the Photos process. Best-effort `Return` to dismiss any "Show Deletion Confirmation" dialog. Photos still routes deletions through *Recently Deleted* (30-day undo). New requirement: the calling terminal/IDE needs Accessibility permission (System Settings → Privacy & Security → Accessibility); without it System Events surfaces `(-1719)` / `(-25204)`, which the Edit page now maps to a dedicated `accessibility_denied` category instead of the generic `photos_unavailable`.
- **About page primary "Open wiki" button rendered blank** (#172): the page-wide `.about a { color:var(--accent) }` rule has class+element specificity (1,1) and beat the unscoped `.wiki-btn { color:#fff }` (1,0), so the white text became the same blue as the button's background. Scoped the rules to `.about .wiki-btn` (1,2,0) so the colour assertion wins; pinned `color:#fff` on the primary hover state explicitly.
- **`pyimgtag faces import-photos` returned 0 persons on a fully populated library** (#174): the bulk AppleScript queried `persons of p` (a plural property Photos.app does **not** expose on a media item — only the `person` element class is exposed and must be traversed via `every person of p`). The surrounding `try` block silently swallowed the AppleScript error on every photo, so a 22 k-photo library with thousands of named faces came back with an empty name list per row and the import wrote zero persons. Switched to the photoscript-canonical `name of every person of p` form; per-photo `try`/`on error → set name_list to {}` keeps a single problem row from killing the traversal. Regression test pins the new form and forbids the bare `(persons of p)` plural-property access.

## [0.13.1] - 2026-05-03

### Fixed
- **PyPI publish blocked by direct-URL dep in `[face]` extras**: v0.13.0's wheel + sdist could not be uploaded — PyPI returned `400 Can't have direct dependency: face_recognition_models @ git+…; extra == "face"` because PEP 503 / core-metadata forbids direct URL deps in *any* metadata field, including extras. Removed the `face_recognition_models @ git+…` line from `[face]` and `[all]`. The pre-flight check in `_face_dep_check.py` (added in 0.13.0) already prints the exact `pip install …` command, so the user gets an actionable error the first time they hit it. README's "Faces" section was rewritten to make the manual install step obvious for both PyPI and source installs.

## [0.13.0] - 2026-05-03

### Added
- **Edit page — bulk delete from Apple Photos** (#162): new top-nav entry `/edit` lists every progress-DB row with `cleanup_class='delete'` and exposes a destructive "Delete N from Photos" action behind an explicit confirm checkbox. The action runs in a background thread, walks the marked rows one by one, asks Photos.app to delete each via a new `applescript_writer.delete_from_photos()` (UUID fast-path + filename-scan fallback, mirroring `reveal_in_photos`) and removes the matching `processed_images` row only on success so a re-scan won't re-process trashed images. Photos.app routes deletions to *Recently Deleted* (30-day undo) — the script never empties that bin. New endpoints: `GET /edit/api/marked` (count + sample), `POST /edit/api/run` (one job at a time, returns 400 + `error="job_already_running"` on overlap), `GET /edit/api/status` (live progress + recent events). AppleScript stderr is logged server-side and only stable category strings (`platform_unsupported` / `photos_timeout` / `photos_unavailable` / `photos_error`) reach the browser.

### Changed
- **Judge page rebuilt in the Review-style grid layout** (#163): `/judge/` now renders the same sticky toolbar + card grid + lightbox + pagination shell as `/review/`, replacing the bar-graph card stack. Each card surfaces a prominent colour-coded rating badge (green ≥8, amber ≥6, red below, muted when missing) over the thumbnail and the model's full natural-language `reason` text in the body so the judgement is the focus. The toolbar gains `Min rating` / `Max rating` number inputs (1–10, clamped silently), a `Sort` dropdown (`rating_desc` default, `rating_asc`, `path_asc`, `path_desc`, `shot_desc`, `shot_asc`) and a `Per page` selector (25/50/100/200), all backed by a new whitelisted ORDER BY in `progress_db.query_judge_results(...)` joined against `processed_images` so the card carries `image_date`, `scene_summary`, `nearest_city/country`, `cleanup_class`. `GET /judge/api/scores` now returns `{items, total}` instead of a bare list and accepts `offset`, `limit` (capped at 200), `sort`, `min_rating`, `max_rating`. Click-through on the thumbnail opens the existing `/review/original` lightbox; the "Open original" link re-uses `/review/api/open-in-photos` for the Photos.app reveal hop.

### Fixed
- **`pyimgtag faces import-photos` no longer hangs silently** (#164): photoscript's `_iterphotos` validates every photo via a separate `photoExists` AppleScript call — on a 20k-photo library that's one osascript round-trip per photo with **zero progress output**, so the user assumes it's hung. Replaced with a single bulk-AppleScript call that returns the entire `(uuid, persons)` map in one subprocess and is parsed in Python; the photoscript path is kept as a fallback when osascript is unavailable. Both paths now emit a startup banner ("Scanning Photos library…"), a single-line progress counter every 200 photos, and a final summary — all unconditionally on stderr (no `--verbose` gate). `KeyboardInterrupt` produces a clean "Aborted at N/total" line + non-zero exit instead of a 12-frame traceback.
- **`face_recognition_models` install UX** (#165): the `face_recognition` PyPI wheel doesn't pull in `face_recognition_models` because that package is git-only — first-time `[face]` users used to hit a wall where `face_recognition` swallowed the `ImportError` and printed its own stderr message before raising, with no Python traceback. New `src/pyimgtag/_face_dep_check.py` probes the dependency first and disambiguates two failure modes: a generic `ImportError` points at the git URL install (`{python} -m pip install …`), while `ModuleNotFoundError(name='pkg_resources')` (Python 3.12+ no longer bundles setuptools) points at `pip install setuptools` instead — re-installing the models package would have been misleading. The `[face]` and `[all]` extras now pin both `face_recognition_models @ git+https://github.com/ageitgey/face_recognition_models` and `setuptools>=68.0` so a fresh source install needs no extra step.
- **`/review/api/open-in-photos` snake_case sentinel** (#166): the DB-row-missing branch returned the human-readable string `"image not found"` (with spaces) while the four AppleScript-failure branches all returned snake_case sentinels (`platform_unsupported`, `photos_timeout`, `photos_unavailable`, `photos_error`). Renamed to `image_not_found` for symmetry; downstream JS that branches on a stable sentinel can now match every category. Endpoint docstring updated to enumerate the full category set.

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
