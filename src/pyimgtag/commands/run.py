"""Handler for the ``run`` subcommand (image tagging workhorse)."""

from __future__ import annotations

import argparse
import logging
import queue as _queue
import shutil
import subprocess  # nosec B404
import sys
import threading as _threading
import time
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from platform import system as get_platform_name
from typing import Any

from pyimgtag import run_registry
from pyimgtag.applescript_writer import read_keywords_from_photos
from pyimgtag.cloud_clients import CloudClientError, ImageClient, make_image_client
from pyimgtag.concurrent_pipeline import (
    resolve_jobs,
    resolve_max_rps,
    run_pipeline,
    set_global_rate_limit,
)
from pyimgtag.exif_reader import read_exif
from pyimgtag.exit_codes import run_exit_code
from pyimgtag.filters import passes_date_filter
from pyimgtag.geocoder import ReverseGeocoder
from pyimgtag.models import ExifData, ImageResult
from pyimgtag.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaClient
from pyimgtag.output_writer import result_to_jsonl, write_csv, write_json
from pyimgtag.place_names import distinct_place_parts
from pyimgtag.preflight import check_ollama, check_ollama_model
from pyimgtag.progress_db import ProgressDB
from pyimgtag.prompt_template import (
    DEFAULT_TEMPLATE,
    PromptBuilder,
    PromptTemplateError,
    load_prompt_template,
)
from pyimgtag.scanner import (
    DEFAULT_VIDEO_EXTENSIONS,
    scan_directory,
    scan_photos_library,
)
from pyimgtag.text_fit import shorten as _shorten
from pyimgtag.vocabulary import Vocabulary, VocabularyError, load_vocabulary

logger = logging.getLogger(__name__)

_FDA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"

_FDA_DIALOG_SCRIPT = (
    'tell application "System Events"\n'
    "    set btn to button returned of (display dialog \u00ac\n"
    '        "pyimgtag cannot read your Photos Library." & return & return & \u00ac\n'
    '        "Grant Full Disk Access to Terminal in:" & return & \u00ac\n'
    '        "System Settings \u2192 Privacy & Security \u2192 Full Disk Access" \u00ac\n'
    '        buttons {"Open System Settings", "Cancel"} \u00ac\n'
    '        default button "Open System Settings" \u00ac\n'
    "        with icon caution \u00ac\n"
    '        with title "Photos Library Access Required")\n'
    '    if btn is "Open System Settings" then\n'
    "        open location " + '"' + _FDA_SETTINGS_URL + '"\n'
    "    end if\n"
    "end tell"
)


def _request_photos_access_dialog() -> None:
    """Show a native macOS dialog offering to open Full Disk Access settings."""
    if get_platform_name() != "Darwin" or not shutil.which("osascript"):
        return
    try:
        subprocess.run(  # noqa: S603  # nosec B603 B607
            ["osascript", "-e", _FDA_DIALOG_SCRIPT],  # noqa: S607
            check=False,
            timeout=120,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass  # dialog is best-effort; silently ignore launch and timeout failures


def _compute_dedup_map(files: list[Path], threshold: int) -> tuple[dict[str, str], set[str]]:
    """Build a perceptual-hash dedup map and the set of paths to skip.

    Returns a tuple of:
      - phash_map: dict mapping file path (str) to its perceptual hash
      - skipped_dedup: set of file paths that are duplicates to be skipped
    """
    from pyimgtag.dedup import compute_phash, find_duplicate_groups

    print("Computing perceptual hashes...", file=sys.stderr)
    records: list[tuple[str, str]] = []
    phash_map: dict[str, str] = {}
    for f in files:
        h = compute_phash(f)
        if h is not None:
            records.append((str(f), h))
            phash_map[str(f)] = h
    groups = find_duplicate_groups(records, threshold=threshold)
    skipped_dedup: set[str] = set()
    dup_count = 0
    for group in groups:
        for path in sorted(group)[1:]:
            skipped_dedup.add(path)
            dup_count += 1
    print(
        f"Found {len(groups)} duplicate groups ({dup_count} images skipped, keeping 1 per group)",
        file=sys.stderr,
    )
    return phash_map, skipped_dedup


class _HydrationQueue:
    """Background worker that re-hydrates cached DB results while fresh files are processed."""

    def __init__(
        self,
        cached_files: list[Path],
        source_type: str,
        args: argparse.Namespace,
        stats: dict,
        vocabulary: Vocabulary | None = None,
    ) -> None:
        self._cached_files = cached_files
        self._source_type = source_type
        self._args = args
        self._vocabulary = vocabulary
        self._result_q: _queue.Queue = _queue.Queue()
        self._thread_stats: dict = {k: 0 for k in stats if k != "scanned"}
        self._stop_event = _threading.Event()
        self._worker: _threading.Thread | None = None
        self._sentinel_seen = False

    def start(self) -> None:
        def _worker() -> None:
            thread_db = ProgressDB(db_path=self._args.db)
            thread_geo = ReverseGeocoder(cache_dir=self._args.cache_dir)
            try:
                for fp in self._cached_files:
                    if self._stop_event.is_set():
                        break
                    r = _hydrate_from_db(
                        fp, self._source_type, self._args, thread_geo, self._thread_stats, thread_db
                    )
                    if r is not None:
                        self._result_q.put(r)
            finally:
                thread_db.close()
                thread_geo.close()
                self._result_q.put(None)  # sentinel

        self._worker = _threading.Thread(target=_worker, daemon=True)
        self._worker.start()

    def drain(
        self,
        progress_db: ProgressDB,
        phash_map: dict,
        results: list,
        stats: dict,
        args: argparse.Namespace,
    ) -> None:
        while True:
            try:
                item = self._result_q.get_nowait()
            except _queue.Empty:
                break
            if item is None:
                self._sentinel_seen = True
            else:
                _finalize_result(
                    item,
                    Path(item.file_path),
                    args,
                    progress_db,
                    phash_map,
                    results,
                    stats,
                    vocabulary=self._vocabulary,
                )

    def stop(self) -> None:
        self._stop_event.set()

    def join(
        self,
        progress_db: ProgressDB,
        phash_map: dict,
        results: list,
        stats: dict,
        args: argparse.Namespace,
    ) -> None:
        if self._worker is not None and not self._stop_event.is_set():
            self._worker.join()
            while not self._sentinel_seen:
                self.drain(progress_db, phash_map, results, stats, args)

    @property
    def thread_stats(self) -> dict:
        return self._thread_stats


def _sort_newest_first(files: list[Path]) -> None:
    """Sort files in-place by mtime descending; missing files sort last."""

    def _mtime(f: Path) -> float:
        try:
            return f.stat().st_mtime
        except OSError:
            return 0.0

    files.sort(key=_mtime, reverse=True)


def _vocabulary_report_path(output_json: str) -> Path:
    """``results.json`` -> ``results.vocabulary.json`` (sibling of the JSON output)."""
    p = Path(output_json)
    return p.with_name(f"{p.stem}.vocabulary.json")


def _write_run_outputs(
    results: list[ImageResult],
    args: argparse.Namespace,
    vocabulary: Vocabulary | None = None,
) -> None:
    if args.output_json:
        write_json(results, args.output_json)
        print(f"Wrote {len(results)} results to {args.output_json}", file=sys.stderr)
        if isinstance(vocabulary, Vocabulary):
            import json as _json

            report = _vocabulary_report_path(args.output_json)
            report.write_text(
                _json.dumps(
                    {"vocabulary": vocabulary.to_dict(), "mapping": vocabulary.stats.to_dict()},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            print(f"Wrote vocabulary mapping report to {report}", file=sys.stderr)
    if args.output_csv:
        write_csv(results, args.output_csv)
        print(f"Wrote {len(results)} results to {args.output_csv}", file=sys.stderr)


def _validate_run_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    """Emit warnings and validate args. Returns non-zero exit code on hard error, else None."""
    if not args.input_dir and not args.photos_library:
        parser.error("one of the arguments --input-dir --photos-library is required")

    if args.write_back and args.input_dir:
        print("Warning: --write-back has no effect with --input-dir", file=sys.stderr)
    if args.skip_if_tagged and args.input_dir:
        print("Warning: --skip-if-tagged has no effect with --input-dir", file=sys.stderr)
    if args.write_back and get_platform_name() != "Darwin":
        print(
            "Warning: --write-back requires macOS; feature is disabled on this system",
            file=sys.stderr,
        )
    if (args.write_exif or args.sidecar_only) and args.dry_run:
        print(
            "Info: --write-exif/--sidecar-only disabled in --dry-run mode"
            " (use --verbose to preview proposed metadata)",
            file=sys.stderr,
        )
    skip_existing_arg = getattr(args, "skip_existing", False)
    if skip_existing_arg and (args.write_back or args.write_exif or args.sidecar_only):
        print(
            "Note: --skip-existing skips photos already complete in the DB WITHOUT "
            "writing back to Photos/EXIF/sidecar. To (re)write metadata for cached "
            "photos, run without --skip-existing.",
            file=sys.stderr,
        )
    if skip_existing_arg and args.dry_run:
        print(
            "Info: --skip-existing is inactive in --dry-run mode (the progress DB is not opened)",
            file=sys.stderr,
        )
    return None


def _scan_files(
    args: argparse.Namespace, extensions: set[str], unsupported: list[Path] | None = None
) -> tuple[str, list[Path]] | int:
    """Scan input source and return (source_type, files), or an int exit code on error.

    When *unsupported* is a list, the media files left out because of their
    extension are appended to it.
    """
    try:
        if args.input_dir:
            return "directory", scan_directory(
                args.input_dir, extensions, recursive=not args.no_recursive, unsupported=unsupported
            )
        return "photos_library", scan_photos_library(
            args.photos_library, extensions, unsupported=unsupported
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except PermissionError as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.photos_library:
            _request_photos_access_dialog()
        return 1


def _resolve_prompt_options(
    args: argparse.Namespace,
) -> tuple[PromptBuilder | None, Vocabulary | None]:
    """Build the prompt customisation from flags (flags win over env vars).

    Returns ``(builder, vocabulary)``; ``builder`` is ``None`` when nothing was
    customised so the clients keep using their built-in prompt.

    Raises:
        VocabularyError: The vocabulary file is unreadable or invalid.
        PromptTemplateError: The template file is unreadable or invalid.
    """
    import os

    def _opt(name: str) -> str | None:
        # Only real strings count — tests build ``args`` from MagicMock, whose
        # auto-attributes are truthy non-strings.
        value = getattr(args, name, None)
        return value if isinstance(value, str) and value else None

    vocab_path = _opt("vocabulary") or os.environ.get("PYIMGTAG_VOCABULARY")
    template_path = _opt("prompt_template") or os.environ.get("PYIMGTAG_PROMPT_TEMPLATE")
    language = _opt("tag_language")

    vocabulary = load_vocabulary(vocab_path) if vocab_path else None
    template = load_prompt_template(template_path) if template_path else DEFAULT_TEMPLATE
    if vocabulary is None and template == DEFAULT_TEMPLATE and not language:
        return None, None
    return PromptBuilder(template=template, vocabulary=vocabulary, language=language), vocabulary


def _create_image_client(
    args: argparse.Namespace, backend: str, prompt_builder: PromptBuilder | None = None
) -> ImageClient | None:
    """Create and return the image tagging client, or None on error."""
    client: Any
    try:
        client = make_image_client(
            backend,
            model=args.model,
            max_dim=args.max_dim,
            timeout=args.timeout,
            api_key=getattr(args, "api_key", None),
            api_base=args.ollama_url if backend == "ollama" else getattr(args, "api_base", None),
            # The name imported here, so tests patching it keep working.
            ollama_class=OllamaClient,
        )
    except CloudClientError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None
    if prompt_builder is not None:
        client.prompt_builder = prompt_builder
    return client


def _backend_name(args: argparse.Namespace) -> str:
    backend = getattr(args, "backend", "ollama")
    return backend if isinstance(backend, str) else "ollama"


@dataclass
class _RunContext:
    """Everything one tagging pass needs, built once by its caller.

    The caller (``cmd_run`` or a ``watch`` cycle) creates ``client``,
    ``geocoder`` and ``progress_db`` and is the one that closes them; the
    tagging paths only use them.
    """

    files: list[Path]
    source_type: str
    args: argparse.Namespace
    client: ImageClient
    geocoder: ReverseGeocoder
    progress_db: ProgressDB | None
    phash_map: dict
    skipped_dedup: set[str]
    results: list[ImageResult]
    stats: dict
    session: Any
    use_threaded: bool
    vocabulary: Vocabulary | None = None


def _record_to_session(ctx: _RunContext, file_path: Path, result: ImageResult) -> None:
    """Report one finished image and the current counters to the dashboard."""
    if ctx.session is None:
        return
    status = "ok" if result.processing_status == "ok" else "error"
    tags = ", ".join(result.tags) if result.tags else None
    ctx.session.record_item(str(file_path), status, error=result.error_message, detail=tags)
    for key, value in ctx.stats.items():
        ctx.session.set_counter(key, value)


def _finalize(ctx: _RunContext, result: ImageResult, file_path: Path) -> None:
    _finalize_result(
        result,
        file_path,
        ctx.args,
        ctx.progress_db,
        ctx.phash_map,
        ctx.results,
        ctx.stats,
        vocabulary=ctx.vocabulary,
    )


def _concurrent_tagging(ctx: _RunContext, jobs: int) -> bool:
    """Tag with ``jobs`` concurrent model calls. Returns True if interrupted.

    Worker threads run :func:`_prepare_one` only; this thread keeps every
    SQLite write, output row, and progress line to itself and receives results
    in scan order from the pipeline's reorder buffer. The DB skip/hydrate gate
    also runs here, which is why ``--resume-threaded`` has no extra effect at
    ``-j > 1``: cached rows are hydrated inline, in order, by this thread.
    """
    stats_lock = _threading.Lock()
    args, stats = ctx.args, ctx.stats

    def _items() -> Iterator[tuple[Path, ImageResult | None]]:
        for file_path in ctx.files:
            if str(file_path) in ctx.skipped_dedup:
                stats["skipped_dedup"] += 1
                continue
            proceed, cached = _pre_submit_decision(
                file_path, ctx.source_type, args, ctx.geocoder, stats, ctx.progress_db
            )
            if proceed:
                yield file_path, None
            elif cached is not None:
                yield file_path, cached

    def _prepare(item: tuple[Path, ImageResult | None]) -> ImageResult | None:
        file_path, cached = item
        if cached is not None:
            return cached  # hydrated on the main thread; nothing left to compute
        return _prepare_one(
            file_path,
            ctx.source_type,
            args,
            ctx.client,
            ctx.geocoder,
            stats,
            stats_lock,
            vocabulary=ctx.vocabulary,
        )

    def _finalize_item(
        _seq: int, item: tuple[Path, ImageResult | None], result: ImageResult
    ) -> None:
        _finalize(ctx, result, item[0])
        _record_to_session(ctx, item[0], result)

    def _should_stop() -> bool:
        return bool(args.limit and stats["processed"] >= args.limit)

    def _on_interrupt() -> None:
        if ctx.session is not None:
            ctx.session.mark_interrupted()
        print("\nInterrupted.", file=sys.stderr)

    return run_pipeline(
        _items(),
        _prepare,
        _finalize_item,
        jobs=jobs,
        session=ctx.session,
        on_interrupt=_on_interrupt,
        should_stop=_should_stop,
        describe=lambda item: str(item[0]),
    )


def _resume_threaded_tagging(ctx: _RunContext) -> bool:
    """``--resume-threaded``: hydrate cached rows in the background, tag the rest."""
    assert ctx.progress_db is not None  # nosec B101 — checked by the caller
    args, stats, db = ctx.args, ctx.stats, ctx.progress_db
    cached = [
        f for f in ctx.files if str(f) not in ctx.skipped_dedup and db.has_usable_model_result(f)
    ]
    cached_set = {str(f) for f in cached}
    fresh = [f for f in ctx.files if str(f) not in ctx.skipped_dedup and str(f) not in cached_set]
    stats["skipped_dedup"] = sum(1 for f in ctx.files if str(f) in ctx.skipped_dedup)
    hq = _HydrationQueue(cached, ctx.source_type, args, stats, vocabulary=ctx.vocabulary)
    hq.start()
    interrupted = False
    try:
        for file_path in fresh:
            if args.limit and stats["processed"] >= args.limit:
                break
            if ctx.session is not None:
                ctx.session.wait_if_paused()
                ctx.session.set_current(str(file_path))
            hq.drain(db, ctx.phash_map, ctx.results, stats, args)
            result = _process_one(
                file_path,
                ctx.source_type,
                args,
                ctx.client,
                ctx.geocoder,
                stats,
                db,
                vocabulary=ctx.vocabulary,
            )
            if result is not None:
                _finalize(ctx, result, file_path)
                _record_to_session(ctx, file_path, result)
            if ctx.session is not None:
                ctx.session.set_current(None)
    except KeyboardInterrupt:
        interrupted = True
        hq.stop()
        if ctx.session is not None:
            ctx.session.mark_interrupted()
        print("\nInterrupted.", file=sys.stderr)
    finally:
        hq.join(db, ctx.phash_map, ctx.results, stats, args)
        for k, v in hq.thread_stats.items():
            stats[k] += v
    return interrupted


def _serial_tagging(ctx: _RunContext) -> bool:
    """The default ``-j 1`` loop. Returns True if interrupted."""
    args, stats = ctx.args, ctx.stats
    try:
        for file_path in ctx.files:
            if args.limit and stats["processed"] >= args.limit:
                break
            if ctx.session is not None:
                ctx.session.wait_if_paused()
                ctx.session.set_current(str(file_path))
            if str(file_path) in ctx.skipped_dedup:
                stats["skipped_dedup"] += 1
                continue
            result = _process_one(
                file_path,
                ctx.source_type,
                args,
                ctx.client,
                ctx.geocoder,
                stats,
                ctx.progress_db,
                vocabulary=ctx.vocabulary,
            )
            if result is None:
                if ctx.session is not None:
                    ctx.session.set_current(None)
                continue
            _finalize(ctx, result, file_path)
            _record_to_session(ctx, file_path, result)
            if ctx.session is not None:
                ctx.session.set_current(None)
    except KeyboardInterrupt:
        if ctx.session is not None:
            ctx.session.mark_interrupted()
        print("\nInterrupted.", file=sys.stderr)
        return True
    return False


def _tag(ctx: _RunContext) -> bool:
    """Run one tagging pass over ``ctx.files``. Returns True if interrupted.

    ``-j/--jobs`` (read off ``args``, so ``watch`` inherits it) selects the
    path: anything above 1 goes through :func:`_concurrent_tagging`; at
    ``-j 1``, ``--resume-threaded`` with a DB uses the hydration worker and
    everything else the serial loop. Closes nothing: the caller owns
    ``client``, ``geocoder`` and ``progress_db``.
    """
    set_global_rate_limit(resolve_max_rps(getattr(ctx.args, "max_rps", None)))
    jobs = resolve_jobs(getattr(ctx.args, "jobs", 1), _backend_name(ctx.args))
    if jobs > 1:
        return _concurrent_tagging(ctx, jobs)
    if ctx.use_threaded and ctx.progress_db is not None:
        return _resume_threaded_tagging(ctx)
    return _serial_tagging(ctx)


def _run_tagging(
    files: list[Path],
    source_type: str,
    args: argparse.Namespace,
    ollama: ImageClient,
    geocoder: ReverseGeocoder,
    progress_db: ProgressDB | None,
    phash_map: dict,
    skipped_dedup: set[str],
    results: list[ImageResult],
    stats: dict,
    session: Any,
    use_threaded: bool,
    vocabulary: Vocabulary | None = None,
) -> bool:
    """Compatibility wrapper: build a :class:`_RunContext` and :func:`_tag` it.

    Unlike the old version it closes nothing; whoever created the client,
    geocoder and database closes them.
    """
    return _tag(
        _RunContext(
            files=files,
            source_type=source_type,
            args=args,
            client=ollama,
            geocoder=geocoder,
            progress_db=progress_db,
            phash_map=phash_map,
            skipped_dedup=skipped_dedup,
            results=results,
            stats=stats,
            session=session,
            use_threaded=use_threaded,
            vocabulary=vocabulary,
        )
    )


def cmd_run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Execute the run subcommand (image tagging)."""
    if _validate_run_args(args, parser) is not None:
        return 1
    extensions = {e.strip().lstrip(".").lower() for e in args.extensions.split(",")}
    video_extensions: set[str] = set()
    if getattr(args, "include_video", False):
        from pyimgtag.video import DEFAULT_VIDEO_EXTENSIONS, ffmpeg_available

        raw = getattr(args, "video_extensions", None)
        video_extensions = (
            {e.strip().lstrip(".").lower() for e in raw.split(",") if e.strip()}
            if raw
            else set(DEFAULT_VIDEO_EXTENSIONS)
        )
        if not ffmpeg_available():
            # Counted and reported, never fatal: the images in the same run
            # are still worth tagging.
            print(
                "Warning: --include-video needs ffmpeg and ffprobe on PATH; "
                "videos will be skipped. Install ffmpeg to tag them.",
                file=sys.stderr,
            )
            video_extensions = set()
        extensions |= video_extensions
    backend = getattr(args, "backend", "ollama")
    if not isinstance(backend, str):
        backend = "ollama"
    if backend == "ollama":
        # Checked before any file is scanned or DB row written: without a
        # server or the model every image would fail and be stored as an
        # error. --dry-run only warns, since it writes nothing.
        ok, msg = check_ollama(args.ollama_url)
        if ok:
            ok, msg = check_ollama_model(args.model or DEFAULT_OLLAMA_MODEL, args.ollama_url)
        if not ok:
            if args.dry_run:
                print(f"Warning: {msg}", file=sys.stderr)
            else:
                print(f"Error: {msg}", file=sys.stderr)
                return 1

    unsupported: list[Path] = []
    scan = _scan_files(args, extensions, unsupported)
    if isinstance(scan, int):
        return scan
    source_type, files = scan
    _report_unsupported(unsupported, args, video_extensions)

    if video_extensions:
        from pyimgtag.scanner import partition_media

        images, videos = partition_media(files, video_extensions)
        dropped = len(files) - len(images) - len(videos)
        if dropped:
            print(
                f"Info: skipped {dropped} Live Photo sidecar clip(s) already "
                f"covered by their still image.",
                file=sys.stderr,
            )
        files = images + videos
        print(f"Info: {len(videos)} video(s) in scope.", file=sys.stderr)

    if not files:
        print("No image files found.", file=sys.stderr)
        return 0
    if args.newest_first:
        _sort_newest_first(files)

    # Before dedup, so files outside the date range are neither hashed nor
    # able to mark an in-range photo as their duplicate.
    scanned = len(files)
    try:
        files, pre_skips = _select_by_capture_metadata(files, args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1

    phash_map: dict[str, str] = {}
    skipped_dedup: set[str] = set()
    if args.dedup:
        phash_map, skipped_dedup = _compute_dedup_map(files, args.dedup_threshold)

    try:
        prompt_builder, vocabulary = _resolve_prompt_options(args)
    except (VocabularyError, PromptTemplateError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if vocabulary is not None:
        mode = "strict" if vocabulary.strict else "non-strict"
        print(
            f"Vocabulary: {len(vocabulary.tags)} tags, {len(vocabulary.synonyms)} synonyms "
            f"({mode}) from {vocabulary.source}",
            file=sys.stderr,
        )

    with ExitStack() as owned:
        # This function creates the client, geocoder and database, so it is
        # the one that closes them, also when anything below raises (e.g.
        # the dashboard failing to start).
        client = _create_image_client(args, backend, prompt_builder)
        if client is None:
            return 1
        owned.callback(client.close)
        geocoder = ReverseGeocoder(cache_dir=args.cache_dir)
        owned.callback(geocoder.close)
        progress_db: ProgressDB | None = None
        if not args.no_cache and not args.dry_run:
            progress_db = ProgressDB(db_path=args.db)
            owned.callback(progress_db.close)

        results: list[ImageResult] = []
        stats = _new_stats(scanned)
        stats["skipped_extension"] = len(unsupported)
        for key, count in pre_skips.items():
            stats[key] += count
        from pyimgtag.webapp.bootstrap import start_dashboard_for

        session, dashboard = start_dashboard_for(args, command="run")
        jobs = resolve_jobs(getattr(args, "jobs", 1), backend)
        if jobs > 1:
            print(f"Concurrency: {jobs} in-flight model requests", file=sys.stderr)
        if session is not None:
            session.set_counter("scanned", stats["scanned"])
            session.set_counter("jobs", jobs)
            session.mark_running()

        use_resume = getattr(args, "resume_from_db", False) and not args.no_cache
        skip_existing = getattr(args, "skip_existing", False) and not args.no_cache
        # --skip-existing fully skips complete rows in the linear path, so the
        # threaded re-hydration worker (which exists only to enrich cached
        # rows) must not run — skip wins.
        use_threaded = use_resume and getattr(args, "resume_threaded", False) and not skip_existing
        ctx = _RunContext(
            files=files,
            source_type=source_type,
            args=args,
            client=client,
            geocoder=geocoder,
            progress_db=progress_db,
            phash_map=phash_map,
            skipped_dedup=skipped_dedup,
            results=results,
            stats=stats,
            session=session,
            use_threaded=use_threaded,
            vocabulary=vocabulary,
        )
        try:
            interrupted = _tag(ctx)
            _write_run_outputs(results, args, vocabulary)
            _print_summary(stats, args)
            if vocabulary is not None:
                report = getattr(args, "output_json", None)
                _print_vocabulary_summary(
                    vocabulary,
                    report=_vocabulary_report_path(report) if report else None,
                )
            if session is not None:
                for k, v in stats.items():
                    session.set_counter(k, v)
                if not interrupted:
                    session.mark_completed()
        finally:
            if dashboard is not None:
                dashboard.stop()
            run_registry.set_current(None)
    if interrupted:
        return 1
    return run_exit_code(stats)


def _read_capture_metadata(file_path: Path, args: argparse.Namespace) -> ExifData:
    """EXIF for *file_path*, with a clip's container date when EXIF has none.

    Read before the date filter, so a clip whose only date is in its
    QuickTime/MP4 header is filtered (and stored) by that date rather than
    treated as undated.
    """
    try:
        exif = read_exif(file_path)
    except Exception as exc:  # noqa: BLE001 — EXIF is best-effort; never block tagging
        logger.debug("EXIF read failed for %s: %s", file_path, exc)
        exif = ExifData()
    if exif.date_original is None and _is_video(file_path, args):
        from pyimgtag.video import VideoToolError, probe

        try:
            created = probe(file_path).creation_time
        except VideoToolError:
            created = None
        if created:
            exif = replace(exif, date_original=created)
    return exif


def _is_video(file_path: Path, args: argparse.Namespace) -> bool:
    """True when this file should be treated as a clip rather than a still."""
    if not getattr(args, "include_video", False):
        return False
    from pyimgtag.video import DEFAULT_VIDEO_EXTENSIONS

    raw = getattr(args, "video_extensions", None)
    exts = (
        {e.strip().lstrip(".").lower() for e in raw.split(",") if e.strip()}
        if raw
        else DEFAULT_VIDEO_EXTENSIONS
    )
    return file_path.suffix.lstrip(".").lower() in exts


def _tag_video(
    file_path: Path,
    args: argparse.Namespace,
    ollama: Any,
    context: dict,
    result: ImageResult,
) -> Any:
    """Sample keyframes, tag each, and fold them into one result for the clip.

    A clip that cannot be decoded returns an errored TagResult rather than
    raising: one unreadable video in a library of thousands is a counted
    failure, not the end of the run.
    """
    import shutil as _shutil

    from pyimgtag.models import TagResult
    from pyimgtag.video import VideoToolError, aggregate_frames, extract_frames, probe

    frame_count = int(getattr(args, "video_frames", 0) or 3)
    temp_dir: Path | None = None
    try:
        info = probe(file_path)
        result.media_type = "video"
        result.duration_sec = info.duration_sec
        # QuickTime keeps the capture time in format tags, which is what puts
        # a clip on the timeline and inside the date filters.
        if info.creation_time and not result.image_date:
            result.image_date = info.creation_time
        frames = extract_frames(file_path, count=frame_count)
        temp_dir = frames[0].parent
    except VideoToolError as exc:
        return TagResult(error=str(exc))

    try:
        per_frame = [ollama.tag_image(str(frame), context=context) for frame in frames]
        return aggregate_frames(per_frame)
    finally:
        if temp_dir is not None:
            _shutil.rmtree(temp_dir, ignore_errors=True)


def _process_one(
    file_path: Path,
    source_type: str,
    args: argparse.Namespace,
    ollama: Any,
    geocoder: ReverseGeocoder,
    stats: dict,
    progress_db: ProgressDB | None = None,
    vocabulary: Vocabulary | None = None,
) -> ImageResult | None:
    """Process one image, serially.  Returns ``None`` when filtered out.

    This is the ``-j 1`` path and stays a straight-line composition of the two
    halves the concurrent pipeline drives separately: the DB-touching gate
    (:func:`_pre_submit_decision`) and the worker-safe body
    (:func:`_prepare_one`).
    """
    proceed, cached = _pre_submit_decision(
        file_path, source_type, args, geocoder, stats, progress_db
    )
    if not proceed:
        return cached
    return _prepare_one(
        file_path, source_type, args, ollama, geocoder, stats, vocabulary=vocabulary
    )


def _pre_submit_decision(
    file_path: Path,
    source_type: str,
    args: argparse.Namespace,
    geocoder: ReverseGeocoder,
    stats: dict,
    progress_db: ProgressDB | None,
) -> tuple[bool, ImageResult | None]:
    """Decide, on the main thread, whether an image needs the model at all.

    Everything here reads or writes SQLite, so it must never run in a worker.

    Returns:
        ``(True, None)`` when the image should be handed to
        :func:`_prepare_one`; ``(False, result)`` when it was hydrated from the
        DB (``result`` is ready to finalize); ``(False, None)`` when it is
        skipped outright.
    """
    try:
        if not file_path.exists() or file_path.stat().st_size == 0:
            stats["skipped_no_local"] += 1
            return False, None
    except OSError:
        stats["skipped_no_local"] += 1
        return False, None

    # --skip-existing: fully skip unchanged photos already complete in the DB
    # (status ok + non-empty tags) before any EXIF read, geocoding, or
    # write-back. This is the fast path for resuming a large, mostly-tagged
    # library; it takes precedence over the --resume-from-db re-enrichment pass.
    if (
        getattr(args, "skip_existing", False)
        and not getattr(args, "no_cache", False)
        and progress_db is not None
        and progress_db.is_complete_cached(file_path)
    ):
        stats["skipped_existing"] += 1
        return False, None

    if progress_db is not None and progress_db.is_processed(file_path):
        if not getattr(args, "resume_from_db", False):
            stats["skipped_cached"] += 1
            return False, None
        # --resume-from-db: fall through to the resume check below

    resume = getattr(args, "resume_from_db", False) and not getattr(args, "no_cache", False)
    if resume and progress_db is not None and progress_db.has_usable_model_result(file_path):
        return False, _hydrate_from_db(file_path, source_type, args, geocoder, stats, progress_db)

    return True, None


def _prepare_one(
    file_path: Path,
    source_type: str,
    args: argparse.Namespace,
    ollama: Any,
    geocoder: ReverseGeocoder,
    stats: dict,
    stats_lock: _threading.Lock | None = None,
    vocabulary: Vocabulary | None = None,
) -> ImageResult | None:
    """Worker-safe half of processing: EXIF, filters, geocode, model call.

    Touches no SQLite handle, writes no output file, and prints no progress, so
    several copies can run in a thread pool while the main thread stays the
    single writer. Returns ``None`` when the image is filtered out.

    Args:
        stats_lock: Held around every counter bump when the caller runs this in
            more than one thread; ``None`` for the serial path.
    """

    def bump(key: str) -> None:
        if stats_lock is None:
            stats[key] = stats.get(key, 0) + 1
            return
        with stats_lock:
            stats[key] = stats.get(key, 0) + 1

    if args.skip_if_tagged and source_type == "photos_library":
        existing = read_keywords_from_photos(str(file_path))
        if existing:
            bump("skipped_tagged")
            return None

    result = ImageResult(
        file_path=str(file_path), file_name=file_path.name, source_type=source_type
    )

    exif = _read_capture_metadata(file_path, args)
    result.image_date = exif.date_original
    result.gps_lat = exif.gps_lat
    result.gps_lon = exif.gps_lon

    skip = _capture_skips(exif, file_path, args)
    if skip:
        for key in skip:
            bump(key)
        return None

    # --- reverse geocode (before tagging so context is available) ---
    geo = None
    if exif.has_gps:
        geo = geocoder.resolve(exif.gps_lat, exif.gps_lon)
        if geo.error:
            bump("geocode_failures")
        else:
            result.nearest_place = geo.nearest_place
            result.nearest_city = geo.nearest_city
            result.nearest_region = geo.nearest_region
            result.nearest_country = geo.nearest_country

    # --- build context for model ---
    context: dict = {}
    if exif.date_original:
        context["date"] = exif.date_original
    if exif.has_gps:
        context["lat"] = exif.gps_lat
        context["lon"] = exif.gps_lon
    if geo and not geo.error:
        if geo.nearest_city:
            context["city"] = geo.nearest_city
        if geo.nearest_region:
            context["region"] = geo.nearest_region
        if geo.nearest_country:
            context["country"] = geo.nearest_country

    # --- tag with model ---
    bump("model_calls")
    if _is_video(file_path, args):
        tag_result = _tag_video(file_path, args, ollama, context, result)
    else:
        tag_result = ollama.tag_image(str(file_path), context=context)
    if tag_result.error:
        result.processing_status = "error"
        result.error_message = tag_result.error
        bump("model_failures")
    else:
        if isinstance(vocabulary, Vocabulary):
            # canonicalize() bumps the shared mapping Counters, so it shares
            # the stats lock with the counter bumps above.
            if stats_lock is None:
                result.tags = vocabulary.canonicalize(tag_result.tags)
            else:
                with stats_lock:
                    result.tags = vocabulary.canonicalize(tag_result.tags)
        else:
            result.tags = tag_result.tags
        result.scene_summary = tag_result.summary
        result.scene_category = tag_result.scene_category
        result.emotional_tone = tag_result.emotional_tone
        result.cleanup_class = tag_result.cleanup_class
        result.has_text = tag_result.has_text
        result.text_summary = tag_result.text_summary
        result.event_hint = tag_result.event_hint
        result.significance = tag_result.significance

    return result


def _write_metadata(
    result: ImageResult,
    rich_desc: str | None,
    args: argparse.Namespace,
    vocabulary: Vocabulary | None = None,
) -> str | None:
    """Write metadata for one image: sidecar-only, direct, or auto-fallback.

    Returns:
        ``"exif"`` when written into the file, ``"sidecar"`` when an XMP
        sidecar was written (asked for, or the format cannot be written
        directly), ``None`` when the write failed (the reason is printed).
    """
    from pyimgtag.exif_writer import (
        SUPPORTED_DIRECT_WRITE_EXTENSIONS,
        write_exif_description,
        write_xmp_sidecar,
    )

    hierarchical = _hierarchical_for(result, args, vocabulary)

    if args.sidecar_only:
        err = write_xmp_sidecar(
            result.file_path,
            description=rich_desc,
            keywords=result.tags,
            hierarchical=hierarchical,
        )
        if err:
            print(f"  Sidecar write failed: {err}", file=sys.stderr)
            return None
        return "sidecar"

    # Direct write with auto-fallback for unsupported extensions
    ext = Path(result.file_path).suffix.lower()
    if ext not in SUPPORTED_DIRECT_WRITE_EXTENSIONS:
        print(
            f"  [{ext}] not supported for direct write; falling back to XMP sidecar",
            file=sys.stderr,
        )
        err = write_xmp_sidecar(
            result.file_path,
            description=rich_desc,
            keywords=result.tags,
            hierarchical=hierarchical,
        )
        if err:
            print(f"  Sidecar write failed: {err}", file=sys.stderr)
            return None
        return "sidecar"

    err = write_exif_description(
        result.file_path,
        description=rich_desc,
        keywords=result.tags,
        fmt=args.metadata_format,
        hierarchical=hierarchical,
    )
    if err:
        print(f"  EXIF write failed: {err}", file=sys.stderr)
        return None
    return "exif"


def _hierarchical_for(
    result: ImageResult, args: argparse.Namespace, vocabulary: Vocabulary | None = None
) -> list[str] | None:
    """The hierarchical keyword paths for one result, or None when not asked for.

    The vocabulary hierarchy is used when one is loaded, so a controlled
    vocabulary's own tree is what reaches Lightroom rather than a flat
    ``Tags|`` child per term.
    """
    if not getattr(args, "hierarchical_keywords", False):
        return None
    from pyimgtag.hierarchy import keyword_paths
    from pyimgtag.vocabulary import Vocabulary

    vocabulary_paths: dict[str, str] = {}
    if isinstance(vocabulary, Vocabulary):
        for tag in result.tags:
            # Vocabulary.path() is the root-to-tag chain, inclusive. A tag with
            # no parent comes back as [tag], which needs no mapping.
            chain = vocabulary.path(tag)
            if len(chain) > 1:
                vocabulary_paths[tag] = "|".join(chain)

    return keyword_paths(
        result.tags,
        country=result.nearest_country,
        region=result.nearest_region,
        city=result.nearest_city,
        vocabulary_paths=vocabulary_paths,
    )


def _hydrate_from_db(
    file_path: Path,
    source_type: str,
    args: argparse.Namespace,
    geocoder: ReverseGeocoder,
    stats: dict,
    progress_db: ProgressDB,
) -> ImageResult | None:
    """Load a cached ImageResult from DB and enrich it with fresh EXIF/geocode data."""
    result = progress_db.get_cached_result(file_path)
    if result is None:
        return None

    result.source_type = source_type
    result.is_local = True
    result.file_path = str(file_path)
    result.file_name = file_path.name

    exif = _read_capture_metadata(file_path, args)
    result.image_date = exif.date_original
    result.gps_lat = exif.gps_lat
    result.gps_lon = exif.gps_lon

    skip = _capture_skips(exif, file_path, args)
    if skip:
        for key in skip:
            stats[key] += 1
        return None

    if exif.has_gps:
        geo = geocoder.resolve(exif.gps_lat, exif.gps_lon)
        if not geo.error:
            result.nearest_place = geo.nearest_place
            result.nearest_city = geo.nearest_city
            result.nearest_region = geo.nearest_region
            result.nearest_country = geo.nearest_country
        else:
            stats["geocode_failures"] += 1

    progress_db.update_missing_fields(file_path, result)
    stats["resumed_from_db"] += 1
    return result


def _finalize_result(
    result: ImageResult,
    file_path: Path,
    args: argparse.Namespace,
    progress_db: ProgressDB | None,
    phash_map: dict,
    results: list,
    stats: dict,
    vocabulary: Vocabulary | None = None,
) -> None:
    """Mark done in DB, handle write-back, append to results list, print progress."""
    if progress_db is not None:
        progress_db.mark_done(file_path, result)

    rich_desc = result.build_description()
    written: list[str] = []

    if (
        args.write_back
        and not args.dry_run
        and result.source_type == "photos_library"
        and result.tags
    ):
        from pyimgtag.applescript_writer import write_to_photos

        err = write_to_photos(
            result.file_name,
            result.tags,
            rich_desc,
            title=result.scene_summary,
            mode=args.write_back_mode,
        )
        if err:
            print(f"  Write-back failed: {err}", file=sys.stderr)
            stats["write_back_failures"] += 1
        else:
            stats["written_back"] += 1
            written.append("photos")

    if (getattr(args, "write_exif", False) or getattr(args, "sidecar_only", False)) and result.tags:
        if args.dry_run:
            if args.verbose:
                target = "sidecar" if getattr(args, "sidecar_only", False) else "file"
                print(f"  [dry-run] Would write to {target}:", file=sys.stderr)
                if rich_desc:
                    print(f"    description: {rich_desc[:80]}", file=sys.stderr)
                print(f"    keywords: {', '.join(result.tags)}", file=sys.stderr)
        else:
            wrote = _write_metadata(result, rich_desc, args, vocabulary)
            if wrote == "sidecar":
                stats["sidecars_written"] += 1
                written.append(wrote)
            elif wrote == "exif":
                stats["metadata_written"] += 1
                written.append(wrote)
            else:
                stats["metadata_write_failures"] += 1

    result.phash = phash_map.get(str(file_path))
    results.append(result)
    stats["processed"] += 1

    if args.jsonl_stdout:
        print(result_to_jsonl(result))
    elif args.verbose or args.dry_run:
        _print_verbose(result, stats["processed"], _progress_total(stats, args), written)
    else:
        _print_brief(result, stats["processed"], _progress_total(stats, args), written)


#: Every counter for a scanned file that will not be printed. skipped_no_date
#: is left out: it is a subset of skipped_date, not a separate group.
_SKIP_COUNTERS = (
    "skipped_date",
    "skipped_no_gps",
    "skipped_no_local",
    "skipped_cached",
    "skipped_existing",
    "skipped_dedup",
    "skipped_tagged",
)


def _progress_total(stats: dict, args: argparse.Namespace) -> int:
    """The ``[n/total]`` denominator: files still going to be processed.

    That is everything scanned minus everything already skipped. The date and
    GPS filters are applied before tagging starts (see
    :func:`_select_by_capture_metadata`), so with those the total is exact
    from the first line and the last line reads ``[n/n]``. Skips that can
    only be decided per file (cached, existing, tagged) shrink it as they are
    found. ``--limit`` caps it.
    """
    in_play = stats["scanned"] - sum(stats.get(key, 0) for key in _SKIP_COUNTERS)
    return min(args.limit, in_play) if args.limit else in_play


def _date_filter_active(args: argparse.Namespace) -> bool:
    return any(getattr(args, name, None) for name in ("date", "date_from", "date_to"))


def _capture_skips(exif: ExifData, file_path: Path, args: argparse.Namespace) -> tuple[str, ...]:
    """The skip counters a file's capture metadata earns, or ``()`` to keep it.

    One place for the ``--date``/``--date-from``/``--date-to`` and
    ``--skip-no-gps`` rules, shared by the up-front pass and the per-file
    paths. A date-filter skip with no date at all also earns
    ``skipped_no_date``, the subset the summary breaks out.
    """
    if not passes_date_filter(exif, file_path, args.date, args.date_from, args.date_to):
        return (
            ("skipped_date", "skipped_no_date") if exif.date_original is None else ("skipped_date",)
        )
    if getattr(args, "skip_no_gps", False) and not exif.has_gps:
        return ("skipped_no_gps",)
    return ()


#: Seconds between progress lines while the up-front pass reads EXIF.
_PASS_PROGRESS_EVERY = 5.0


def _select_by_capture_metadata(
    files: list[Path], args: argparse.Namespace
) -> tuple[list[Path], dict[str, int]]:
    """Apply ``--date``/``--date-from``/``--date-to``/``--skip-no-gps`` up front.

    These decide on EXIF alone, so applying them before tagging tells the
    progress counter how many files will be tagged, rather than counting
    against every scanned file, and keeps dedup from hashing files that are
    dropped anyway. With ``--limit`` the pass stops once that many files are
    kept; the rest go through unread and are filtered one by one as before,
    so a small ``--limit`` on a large library does not read every file first.

    Returns:
        ``(kept, skips)``: the files to tag, in their original order, and the
        skip counters to add to the run's stats. *files* and ``{}`` when no
        such filter is active.
    """
    if not (_date_filter_active(args) or getattr(args, "skip_no_gps", False)):
        return files, {}
    print(f"Reading capture dates for {len(files)} file(s) to apply the filters…", file=sys.stderr)
    kept: list[Path] = []
    skips: dict[str, int] = {}
    last_note = time.monotonic()
    for position, file_path in enumerate(files, start=1):
        if args.limit and len(kept) >= args.limit:
            kept.extend(files[position - 1 :])
            break
        skip = _capture_skips(_read_capture_metadata(file_path, args), file_path, args)
        for key in skip:
            skips[key] = skips.get(key, 0) + 1
        if not skip:
            kept.append(file_path)
        if time.monotonic() - last_note >= _PASS_PROGRESS_EVERY:
            last_note = time.monotonic()
            print(f"  read {position}/{len(files)}, {len(kept)} kept", file=sys.stderr)
    return kept, skips


def _report_unsupported(
    unsupported: list[Path], args: argparse.Namespace, video_extensions: set[str]
) -> None:
    """Say how many media files the scan left out because of their extension.

    The first one is named, with the flag that would bring its type in: the
    user's own ``--extensions`` list plus that type, or for a clip
    ``--include-video`` / ``--video-extensions``.
    """
    if not unsupported:
        return
    first = unsupported[0]
    ext = first.suffix.lstrip(".").lower()
    if ext in DEFAULT_VIDEO_EXTENSIONS and not getattr(args, "include_video", False):
        hint = "pass --include-video to tag videos"
    elif ext in DEFAULT_VIDEO_EXTENSIONS and not video_extensions:
        hint = "videos need ffmpeg and ffprobe on PATH"
    elif ext in DEFAULT_VIDEO_EXTENSIONS:
        hint = f"add it with --video-extensions {','.join(sorted(video_extensions))},{ext}"
    else:
        own = ",".join(e.strip().lstrip(".") for e in args.extensions.split(",") if e.strip())
        hint = f"add it with --extensions {own},{ext}"
    print(
        f"Info: skipped {len(unsupported)} file(s) whose extension is not scanned, "
        f"first: {first} — {hint}",
        file=sys.stderr,
    )


def _new_stats(scanned: int) -> dict[str, int]:
    return {
        "scanned": scanned,
        "processed": 0,
        "skipped_date": 0,
        "skipped_no_date": 0,
        "skipped_no_gps": 0,
        "skipped_no_local": 0,
        "skipped_cached": 0,
        "skipped_existing": 0,
        "skipped_dedup": 0,
        "skipped_tagged": 0,
        "model_calls": 0,
        "model_failures": 0,
        "geocode_failures": 0,
        "resumed_from_db": 0,
        "metadata_written": 0,
        "sidecars_written": 0,
        "metadata_write_failures": 0,
        "written_back": 0,
        "write_back_failures": 0,
    }


def _print_brief(
    result: ImageResult, idx: int, total: int, written: list[str] | None = None
) -> None:
    tags = ", ".join(result.tags) if result.tags else "(none)"
    loc = result.nearest_city or result.nearest_place or ""
    if result.nearest_country and loc:
        loc = f"{loc}, {result.nearest_country}"
    status = result.processing_status
    if result.error_message:
        status = f"error: {_shorten(_error_summary(result.error_message), 140)}"
    parts = [f"[{idx}/{total}]", result.file_name, "->", tags]
    if loc:
        parts.append(f"| {loc}")
    parts.append(f"| {status}")
    if written:
        # Where this photo's tags went: exif / sidecar / photos.
        parts.append(f"| {' + '.join(written)}")
    print(" ".join(parts), file=sys.stderr)


def _error_summary(message: str) -> str:
    """The first line of an error: the readable part (details follow it)."""
    return message.partition("\n")[0]


def _print_verbose(
    result: ImageResult, idx: int, total: int, written: list[str] | None = None
) -> None:
    print(f"[{idx}/{total}] {result.file_name}", file=sys.stderr)
    print(f"  Path:     {result.file_path}", file=sys.stderr)
    print(f"  Date:     {result.image_date or '(unknown)'}", file=sys.stderr)
    tags = ", ".join(result.tags) if result.tags else "(none)"
    print(f"  Tags:     {tags}", file=sys.stderr)
    if result.scene_summary:
        print(f"  Summary:  {result.scene_summary}", file=sys.stderr)
    if result.scene_category:
        print(f"  Scene:    {result.scene_category}", file=sys.stderr)
    if result.emotional_tone:
        print(f"  Tone:     {result.emotional_tone}", file=sys.stderr)
    if result.cleanup_class:
        print(f"  Cleanup:  {result.cleanup_class}", file=sys.stderr)
    if result.has_text:
        print("  Has text: yes", file=sys.stderr)
        if result.text_summary:
            print(f"  Text:     {result.text_summary}", file=sys.stderr)
    if result.event_hint:
        print(f"  Event:    {result.event_hint}", file=sys.stderr)
    if result.significance:
        print(f"  Signif.:  {result.significance}", file=sys.stderr)
    if result.gps_lat is not None:
        print(f"  GPS:      {result.gps_lat}, {result.gps_lon}", file=sys.stderr)
    else:
        print("  GPS:      (none)", file=sys.stderr)
    loc_parts = distinct_place_parts(
        result.nearest_place,
        result.nearest_city,
        result.nearest_region,
        result.nearest_country,
    )
    print(f"  Location: {', '.join(loc_parts) if loc_parts else '(none)'}", file=sys.stderr)
    print(f"  Status:   {result.processing_status}", file=sys.stderr)
    if result.error_message:
        summary, _, detail = result.error_message.partition("\n")
        print(f"  Error:    {summary}", file=sys.stderr)
        if detail:
            print(f"  Detail:   {detail}", file=sys.stderr)
    if written:
        print(f"  Written:  {', '.join(written)}", file=sys.stderr)
    print(file=sys.stderr)


def _writes_requested(args: argparse.Namespace) -> bool:
    """True when the run writes metadata anywhere (files, sidecars, Photos)."""
    return any(
        bool(getattr(args, flag, False)) for flag in ("write_exif", "sidecar_only", "write_back")
    )


#: Minimum width of the label column in the run summary: the longest label
#: ("Skipped (no file):") plus one space. Wider when a longer label appears.
SUMMARY_LABEL_WIDTH = 19


def _summary_rows(stats: dict, args: argparse.Namespace | None) -> list[tuple[str, int]]:
    rows = [
        ("Scanned:", stats["scanned"]),
        ("Processed:", stats["processed"]),
        ("Skipped (date):", stats["skipped_date"]),
        ("Skipped (no GPS):", stats["skipped_no_gps"]),
        ("Skipped (no file):", stats["skipped_no_local"]),
        ("Skipped (cached):", stats["skipped_cached"]),
        ("Skipped (exists):", stats["skipped_existing"]),
        ("Skipped (dedup):", stats["skipped_dedup"]),
        ("Skipped (tagged):", stats["skipped_tagged"]),
        ("Model failures:", stats["model_failures"]),
        ("Geocode failures:", stats["geocode_failures"]),
        ("Resumed (DB):", stats["resumed_from_db"]),
    ]
    # Only when the scan counted them: watch scans without collecting the list,
    # and a row reading 0 there would claim nothing was left out.
    if "skipped_extension" in stats:
        rows.insert(9, ("Skipped (ext):", stats["skipped_extension"]))
    # Of the date-filter skips, how many had no capture date at all: those are
    # not "outside the range", their date is unknown. Only under a date filter.
    if args is not None and _date_filter_active(args):
        after = next(i for i, (label, _) in enumerate(rows) if label == "Skipped (date):")
        rows.insert(after + 1, ("  without a date:", stats.get("skipped_no_date", 0)))
    # Only for the writes that were asked for, so the default summary is unchanged.
    if args is not None:
        sidecar_only = bool(getattr(args, "sidecar_only", False))
        write_exif = bool(getattr(args, "write_exif", False))
        if write_exif and not sidecar_only:
            rows.append(("Metadata written:", stats.get("metadata_written", 0)))
        if write_exif or sidecar_only:
            rows.append(("Sidecars written:", stats.get("sidecars_written", 0)))
            rows.append(("Write failures:", stats.get("metadata_write_failures", 0)))
        if getattr(args, "write_back", False):
            rows.append(("Written back:", stats.get("written_back", 0)))
            rows.append(("Photos failures:", stats.get("write_back_failures", 0)))
    return rows


def _print_summary(stats: dict, args: argparse.Namespace | None = None) -> None:
    print("\n--- Summary ---", file=sys.stderr)
    rows = _summary_rows(stats, args)
    width = max(SUMMARY_LABEL_WIDTH, *(len(label) + 1 for label, _ in rows))
    for label, value in rows:
        print(f"  {label:<{width}}{value}", file=sys.stderr)


def _print_vocabulary_summary(
    vocabulary: Vocabulary, top: int = 15, report: Path | None = None
) -> None:
    """Print raw→canonical mapping counts so users can grow their synonym lists.

    Each list shows the *top* most frequent entries; when there are more, a
    "… and N more" line says how many were left out and where the full list
    is (the ``.vocabulary.json`` report when ``--output-json`` was given).
    """
    st = vocabulary.stats
    print("\n--- Vocabulary ---", file=sys.stderr)
    print(f"  Exact matches:    {st.exact}", file=sys.stderr)
    print(f"  Mapped:           {st.total_mapped}", file=sys.stderr)
    if vocabulary.strict:
        print(f"  Dropped (strict): {st.total_dropped}", file=sys.stderr)
    else:
        print(f"  Kept off-vocab:   {st.total_kept}", file=sys.stderr)
    # Same order as the .vocabulary.json report (count, then name), so "the
    # rest" is exactly the tail of the report's list.
    mapped = sorted(st.mapped.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    for (raw, canon), n in mapped[:top]:
        print(f"    {raw} -> {canon}  x{n}", file=sys.stderr)
    _print_hidden(len(mapped) - top, "mapping", report)
    off_counts = st.dropped if vocabulary.strict else st.kept
    off = sorted(off_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    for raw, n in off[:top]:
        label = "dropped" if vocabulary.strict else "kept"
        print(f"    {raw}  ({label} x{n})", file=sys.stderr)
    _print_hidden(len(off) - top, "tag", report)


def _print_hidden(hidden: int, noun: str, report: Path | None) -> None:
    """Say how many list entries the summary left out, and where they are."""
    if hidden <= 0:
        return
    where = f"full list in {report}" if report else "full list: re-run with --output-json"
    plural = "" if hidden == 1 else "s"
    # "distinct": the headers above count every occurrence, this counts entries.
    print(f"    … and {hidden} more distinct {noun}{plural} ({where})", file=sys.stderr)
