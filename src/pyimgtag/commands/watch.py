"""Handler for the ``watch`` subcommand: continuous incremental tagging.

``watch`` is a polling loop around the exact ``run`` pipeline: every
``--interval`` seconds it re-scans the source, waits for files to be
*stable* (same size + mtime across two consecutive polls, so half-copied
imports are never tagged), and feeds only the files that are new or changed
into :func:`pyimgtag.commands.run._run_tagging` with ``--skip-existing``
semantics. Nominatim rate limiting, write-back gating, dedup, and the
dashboard therefore behave exactly as in ``run`` — this module adds the
loop, the stability gate, a single-instance lock, and graceful shutdown.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from pyimgtag import run_registry
from pyimgtag.commands import run as _run
from pyimgtag.geocoder import ReverseGeocoder
from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB
from pyimgtag.prompt_template import PromptTemplateError
from pyimgtag.run_session import RunSession
from pyimgtag.vocabulary import VocabularyError

__all__ = ["WatchLock", "WatchLockHeld", "cmd_watch", "lock_path_for_db"]

Signature = tuple[int, float]


# --- single-instance lock --------------------------------------------------


class WatchLockHeld(RuntimeError):
    """Another live ``watch`` process owns the lock."""

    def __init__(self, path: Path, pid: int) -> None:
        super().__init__(f"another pyimgtag watch (pid {pid}) is already using this DB: {path}")
        self.path = path
        self.pid = pid


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def lock_path_for_db(db_path: str | Path | None) -> Path:
    """``progress.db`` -> ``progress.db.watch.lock`` (default DB when ``None``)."""
    if db_path is None:
        db_path = Path.home() / ".cache" / "pyimgtag" / "progress.db"
    p = Path(db_path)
    return p.with_name(p.name + ".watch.lock")


class WatchLock:
    """PID lockfile; stale locks (dead PID / unreadable content) are reclaimed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                holder = self._read_pid()
                if holder is not None and holder != os.getpid() and _pid_alive(holder):
                    raise WatchLockHeld(self.path, holder) from None
                # Stale (dead pid or garbage) — reclaim and retry the exclusive create.
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass  # already removed by another racing watch process
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
            self._held = True
            return
        raise WatchLockHeld(self.path, self._read_pid() or -1)

    def _read_pid(self) -> int | None:
        try:
            return int(self.path.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        if self._read_pid() == os.getpid():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass  # already removed (e.g. by an external cleanup)

    def __enter__(self) -> WatchLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# --- helpers -----------------------------------------------------------------


def _signature(path: Path) -> Signature | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime)


def _log(msg: str) -> None:
    print(f"[watch] {msg}", file=sys.stderr, flush=True)


def _validate_watch_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if getattr(args, "dry_run", False):
        parser.error("watch needs the progress DB; --dry-run is not supported")
    if getattr(args, "no_cache", False):
        parser.error("watch needs the progress DB; --no-cache is not supported")
    if args.interval <= 0:
        parser.error("--interval must be > 0 seconds")
    if args.max_cycles < 0:
        parser.error("--max-cycles must be >= 0")


class _Watcher:
    """One ``watch`` invocation: owns the poll loop and per-cycle state."""

    def __init__(
        self,
        args: argparse.Namespace,
        session: RunSession,
        stop: threading.Event,
        prompt_builder: Any,
        extensions: set[str],
    ) -> None:
        self.args = args
        self.session = session
        self.stop = stop
        self.prompt_builder = prompt_builder
        self.extensions = extensions
        self.previous: dict[str, Signature] = {}
        # Files that errored, keyed by the signature that errored — skipped
        # until they change so a persistently failing file does not hit the
        # model every cycle.
        self.failed: dict[str, Signature] = {}
        self.cycles = 0
        self.interrupted = False

    def _candidates(self, files: list[Path]) -> tuple[list[Path], int]:
        """Return ``(stable_new_or_changed, pending_unstable_count)``."""
        snapshot: dict[str, Signature] = {}
        stable: list[Path] = []
        pending = 0
        for f in files:
            sig = _signature(f)
            if sig is None:
                continue
            key = str(f)
            snapshot[key] = sig
            if self.previous.get(key) == sig:
                stable.append(f)
            else:
                pending += 1
        self.previous = snapshot
        # Drop failure memory for files that vanished or changed.
        self.failed = {k: s for k, s in self.failed.items() if snapshot.get(k) == s}

        todo: list[Path] = []
        with ProgressDB(db_path=self.args.db) as db:
            for f in stable:
                key = str(f)
                if key in self.failed:
                    continue
                if not db.is_complete_cached(f):
                    todo.append(f)
        return todo, pending

    def _tag(self, todo: list[Path], source_type: str, total: int) -> dict[str, int]:
        client = _run._create_image_client(self.args, self.args.backend, self.prompt_builder)
        stats = _run._new_stats(total)
        if client is None:
            stats["model_failures"] = len(todo)
            return stats
        geocoder = ReverseGeocoder(cache_dir=self.args.cache_dir)
        progress_db = ProgressDB(db_path=self.args.db)
        phash_map: dict[str, str] = {}
        skipped_dedup: set[str] = set()
        if self.args.dedup:
            phash_map, skipped_dedup = _run._compute_dedup_map(todo, self.args.dedup_threshold)
        results: list[ImageResult] = []
        # _run_tagging closes client/geocoder/progress_db itself.
        self.interrupted = _run._run_tagging(
            todo,
            source_type,
            self.args,
            client,
            geocoder,
            progress_db,
            phash_map,
            skipped_dedup,
            results,
            stats,
            self.session,
            False,
        )
        for r in results:
            if r.processing_status != "ok":
                sig = self.previous.get(r.file_path)
                if sig is not None:
                    self.failed[r.file_path] = sig
        return stats

    def cycle(self) -> bool:
        """Run one poll cycle. Returns ``False`` when the loop should end."""
        self.cycles += 1
        scan = _run._scan_files(self.args, self.extensions)
        if isinstance(scan, int):
            _log(f"cycle {self.cycles}: scan failed (see error above); retrying next interval")
            self.session.set_counter("cycles", self.cycles)
            return True
        source_type, files = scan
        todo, pending = self._candidates(files)
        stats = self._tag(todo, source_type, len(files)) if todo else _run._new_stats(len(files))
        summary = {
            "cycles": self.cycles,
            "checked": len(files),
            "pending": pending,
            "new": len(todo),
            "tagged": stats["processed"],
            "errors": stats["model_failures"],
        }
        for k, v in summary.items():
            self.session.set_counter(k, v)
        self.session.set_current(None)
        _log(
            f"cycle {summary['cycles']}: checked {summary['checked']:,} · "
            f"pending {summary['pending']} · new {summary['new']} · "
            f"tagged {summary['tagged']} · errors {summary['errors']}"
        )
        if self.interrupted or self.stop.is_set() or self.session.is_stop_requested():
            return False
        return not (self.args.max_cycles and self.cycles >= self.args.max_cycles)


def cmd_watch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Execute the watch subcommand."""
    from pyimgtag import service_units

    if getattr(args, "uninstall_service", False):
        return service_units.uninstall_service()
    if getattr(args, "install_service", False):
        if not args.input_dir and not args.photos_library:
            parser.error("one of the arguments --input-dir --photos-library is required")
        raw_argv = getattr(args, "_argv", None)
        if not isinstance(raw_argv, list):
            raw_argv = sys.argv[1:]
        return service_units.install_service(raw_argv, force=getattr(args, "force", False))

    _validate_watch_args(args, parser)
    if _run._validate_run_args(args, parser) is not None:
        return 1
    # watch is incremental by definition.
    args.skip_existing = True
    args.no_cache = False
    extensions = {e.strip().lstrip(".").lower() for e in args.extensions.split(",")}
    backend = getattr(args, "backend", "ollama")
    args.backend = backend if isinstance(backend, str) else "ollama"

    try:
        prompt_builder, vocabulary = _run._resolve_prompt_options(args)
    except (VocabularyError, PromptTemplateError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    args._vocabulary = vocabulary

    lock = WatchLock(lock_path_for_db(args.db))
    try:
        lock.acquire()
    except WatchLockHeld as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    from pyimgtag.webapp.bootstrap import start_dashboard_for

    session, dashboard = start_dashboard_for(args, command="watch")
    if session is None:
        # No dashboard: still use a session so SIGTERM/SIGINT and the
        # stop button share one cooperative stop path between images.
        session = RunSession(command="watch")
        run_registry.set_current(session)
    session.mark_running()

    stop = threading.Event()

    def _on_signal(signum: int, _frame: object) -> None:
        _log(f"received signal {signum}; finishing the in-flight image then exiting")
        stop.set()
        session.request_stop()

    previous_handlers: dict[int, Any] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.signal(sig, _on_signal)
        except (ValueError, OSError):  # not on the main thread / unsupported
            pass

    watcher = _Watcher(args, session, stop, prompt_builder, extensions)
    _log(
        f"watching {args.input_dir or args.photos_library} every {args.interval:g}s "
        f"(lock {lock.path})"
    )
    exit_code = 0
    try:
        while True:
            try:
                keep_going = watcher.cycle()
            except KeyboardInterrupt:
                keep_going = False
                watcher.interrupted = True
            if not keep_going:
                break
            if stop.wait(args.interval):
                break
        if watcher.interrupted or stop.is_set() or session.is_stop_requested():
            session.mark_interrupted()
            _log("stopped")
        else:
            session.mark_completed()
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass  # not on the main thread / unsupported; nothing to restore
        if dashboard is not None:
            dashboard.stop()
        run_registry.set_current(None)
        lock.release()
    return exit_code
