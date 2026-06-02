"""Handlers for the ``faces`` subcommand group."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from pyimgtag import run_registry
from pyimgtag.progress_db import ProgressDB
from pyimgtag.scanner import scan_directory, scan_photos_library
from pyimgtag.webapp.bootstrap import start_dashboard_for

try:
    from pyimgtag.face_embedding import scan_and_store
except ImportError:
    scan_and_store = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_CLUSTER_INTERVAL_S = 30

# Detection-quality presets bundling the dlib knobs. The granular --max-dim /
# --detection-model / --upsample / --num-jitters flags override any field.
# max_dim is held at 1280 across presets on purpose: stored bboxes live in the
# resized-image space and the faces UI (face_thumb / the preview endpoint)
# assumes a 1280 detection space when scaling crops back. The quality gain comes
# from upsampling (finds smaller faces), jitter (better encodings), and the cnn
# model — not from a larger max_dim, which would misalign every thumbnail.
_FACE_QUALITY_PRESETS = {
    "fast": {"model": "hog", "upsample": 1, "num_jitters": 1, "max_dim": 1280},
    "balanced": {"model": "hog", "upsample": 2, "num_jitters": 4, "max_dim": 1280},
    "accurate": {"model": "cnn", "upsample": 1, "num_jitters": 10, "max_dim": 1280},
}


def _resolve_face_quality(args: argparse.Namespace) -> dict:
    """Resolve a scan's detection settings from its preset plus any overrides."""
    preset = dict(_FACE_QUALITY_PRESETS[getattr(args, "quality", None) or "balanced"])
    if getattr(args, "detection_model", None) is not None:
        preset["model"] = args.detection_model
    if getattr(args, "max_dim", None) is not None:
        preset["max_dim"] = args.max_dim
    if getattr(args, "upsample", None) is not None:
        preset["upsample"] = args.upsample
    if getattr(args, "num_jitters", None) is not None:
        preset["num_jitters"] = args.num_jitters
    preset["min_face_size"] = getattr(args, "min_face_size", 0) or 0
    return preset


def _validate_face_quality(q: dict) -> str | None:
    """Return an error message for out-of-range detection settings, else None."""
    if q["max_dim"] < 1:
        return "--max-dim must be >= 1"
    if q["upsample"] < 0:
        return "--upsample must be >= 0"
    if q["num_jitters"] < 1:
        return "--num-jitters must be >= 1"
    if q["min_face_size"] < 0:
        return "--min-face-size must be >= 0"
    return None


def cmd_faces(args: argparse.Namespace) -> int:
    """Dispatch faces sub-actions."""
    if args.faces_action is None:
        print(
            "Usage: pyimgtag faces "
            "{scan,cluster,review,apply,import-photos,match-references,ui,recluster,"
            "reset-untrusted,reset}",
            file=sys.stderr,
        )
        return 1

    if args.faces_action == "scan":
        return _handle_faces_scan(args)
    if args.faces_action == "match-references":
        return _handle_faces_match_references(args)
    if args.faces_action == "capture-names":
        return _handle_faces_capture_names(args)
    if args.faces_action == "cluster":
        return _handle_faces_cluster(args)
    if args.faces_action == "review":
        return _handle_faces_review(args)
    if args.faces_action == "apply":
        return _handle_faces_apply(args)
    if args.faces_action == "import-photos":
        return _handle_faces_import_photos(args)
    if args.faces_action == "ui":
        return _handle_faces_ui(args)
    if args.faces_action == "reset":
        return _handle_faces_reset(args)
    if args.faces_action == "reset-untrusted":
        return _handle_faces_reset_untrusted(args)
    if args.faces_action == "recluster":
        return _handle_faces_recluster(args)

    return 1


def _print_reset_preview(counts: dict[str, int], applied: bool) -> None:
    """Print the per-table counts for a faces reset, as a preview or a result."""
    verb = "Removed" if applied else "Would remove"
    print(
        f"{verb}: {counts['faces']} face(s), {counts['persons']} person(s), "
        f"{counts['scanned_images']} scan-cache entr"
        f"{'y' if counts['scanned_images'] == 1 else 'ies'}.",
        file=sys.stderr,
    )
    if not applied:
        print("Re-run with --yes to apply.", file=sys.stderr)


def _handle_faces_reset(args: argparse.Namespace) -> int:
    """Delete ALL faces, persons (including trusted), and the scan cache."""
    with ProgressDB(db_path=args.db) as db:
        counts = db.reset_all_faces(dry_run=not args.yes)
    _print_reset_preview(counts, applied=args.yes)
    return 0


def _handle_faces_reset_untrusted(args: argparse.Namespace) -> int:
    """Delete non-trusted faces and clusters, keeping trusted/named people."""
    with ProgressDB(db_path=args.db) as db:
        counts = db.reset_untrusted_faces(dry_run=not args.yes)
    _print_reset_preview(counts, applied=args.yes)
    return 0


def _handle_faces_recluster(args: argparse.Namespace) -> int:
    """Clear auto-clusters and re-cluster from scratch (keeps trusted people)."""
    try:
        from pyimgtag.face_clustering import recluster_auto
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    with ProgressDB(db_path=args.db) as db:
        auto = db.count_auto_persons()
        if not args.yes:
            print(
                f"Would clear {auto} auto-cluster(s) and re-cluster from scratch "
                "(trusted/named people are kept).",
                file=sys.stderr,
            )
            print("Re-run with --yes to apply.", file=sys.stderr)
            return 0
        result = recluster_auto(db, eps=args.eps, min_samples=args.min_samples)

    print(
        f"Cleared {auto} auto-cluster(s); created {len(result)} new cluster(s).",
        file=sys.stderr,
    )
    return 0


_DISK_FULL_MSG = (
    "\nError: disk full — no space left on device. Free up space and re-run; "
    "already-scanned images will be skipped automatically."
)


def _scan_serial(db, files, quality, *, limit, session, stats: dict) -> bool:
    """Detect + store one image at a time. Returns True if interrupted."""
    import errno as _errno

    try:
        for i, file_path in enumerate(files):
            if limit and i >= limit:
                break
            if session is not None:
                session.wait_if_paused()
                session.set_current(str(file_path))

            # Already detected in a previous run — counted separately so the
            # summary doesn't read "0 detected" when nothing was re-detected.
            if db.is_face_scanned(str(file_path)):
                stats["skipped_existing"] += 1
                if session is not None:
                    session.set_counter("skipped_existing", stats["skipped_existing"])
                    session.set_current(None)
                continue
            # iCloud-evicted originals are absent on disk — skip quietly.
            if not file_path.is_file():
                stats["not_downloaded"] += 1
                if session is not None:
                    session.set_counter("not_downloaded", stats["not_downloaded"])
                    session.set_current(None)
                continue

            try:
                count = scan_and_store(
                    file_path,
                    db,
                    max_dim=quality["max_dim"],
                    model=quality["model"],
                    upsample=quality["upsample"],
                    num_jitters=quality["num_jitters"],
                    min_face_size=quality["min_face_size"],
                )
            except OSError as exc:
                if exc.errno == _errno.ENOSPC:
                    print(_DISK_FULL_MSG, file=sys.stderr)
                    return True
                print(f"  {file_path.name}: skipped ({exc})", file=sys.stderr)
                stats["errors"] += 1
                continue
            except Exception as exc:  # noqa: BLE001 — one bad image must not abort batch
                print(f"  {file_path.name}: skipped ({exc})", file=sys.stderr)
                stats["errors"] += 1
                continue

            stats["scanned"] += 1
            if count > 0:
                stats["faces"] += count
                print(f"  {file_path.name}: {count} face(s)", file=sys.stderr)
            if session is not None:
                session.record_item(str(file_path), "ok")
                session.set_counter("scanned", stats["scanned"])
                session.set_counter("faces_detected", stats["faces"])
                session.set_current(None)
    except KeyboardInterrupt:
        if session is not None:
            session.mark_interrupted()
        print("\nInterrupted.", file=sys.stderr)
        return True
    return False


def _scan_parallel(db, files, quality, *, jobs, limit, session, stats: dict) -> bool:
    """Detect + encode across ``jobs`` worker processes; write results in this
    (main) process so SQLite keeps a single writer. Returns True if interrupted.

    Detection + embedding is the CPU-bound bottleneck (especially with high
    ``num_jitters``); fanning it across cores is the large speedup for big
    libraries. Workers do no DB I/O — they return picklable
    ``(FaceDetection, embedding)`` pairs that the main process inserts.
    """
    import errno as _errno

    from pyimgtag.face_embedding import detect_and_encode

    qkw = {k: quality[k] for k in ("max_dim", "model", "upsample", "num_jitters", "min_face_size")}
    file_enum = enumerate(files)

    def _next_eligible():
        for i, fp in file_enum:
            if limit and i >= limit:
                return None
            if db.is_face_scanned(str(fp)):
                stats["skipped_existing"] += 1
                if session is not None:
                    session.set_counter("skipped_existing", stats["skipped_existing"])
                continue
            if not fp.is_file():
                stats["not_downloaded"] += 1
                if session is not None:
                    session.set_counter("not_downloaded", stats["not_downloaded"])
                continue
            return fp
        return None

    interrupted = False
    inflight: dict = {}
    try:
        with ProcessPoolExecutor(max_workers=jobs) as executor:
            # Keep a bounded backlog (a few per worker) so memory stays flat
            # even on a 20k-image library.
            for _ in range(jobs * 4):
                fp = _next_eligible()
                if fp is None:
                    break
                inflight[executor.submit(detect_and_encode, str(fp), **qkw)] = fp

            while inflight and not interrupted:
                if session is not None:
                    session.wait_if_paused()
                done, _pending = wait(list(inflight), return_when=FIRST_COMPLETED)
                for fut in done:
                    fp = inflight.pop(fut)
                    try:
                        results = fut.result()
                    except Exception as exc:  # noqa: BLE001 — one bad image must not abort
                        print(f"  {fp.name}: skipped ({exc})", file=sys.stderr)
                        stats["errors"] += 1
                    else:
                        try:
                            for detection, embedding in results:
                                db.insert_face(str(fp), detection, embedding=embedding)
                            db.mark_face_scanned(str(fp))
                        except OSError as exc:
                            if exc.errno == _errno.ENOSPC:
                                print(_DISK_FULL_MSG, file=sys.stderr)
                                interrupted = True
                                break
                            raise
                        stats["scanned"] += 1
                        if results:
                            stats["faces"] += len(results)
                            print(f"  {fp.name}: {len(results)} face(s)", file=sys.stderr)
                        if session is not None:
                            session.record_item(str(fp), "ok")
                            session.set_counter("scanned", stats["scanned"])
                            session.set_counter("faces_detected", stats["faces"])
                    if not interrupted:
                        nf = _next_eligible()
                        if nf is not None:
                            inflight[executor.submit(detect_and_encode, str(nf), **qkw)] = nf
    except KeyboardInterrupt:
        if session is not None:
            session.mark_interrupted()
        print("\nInterrupted.", file=sys.stderr)
        return True
    return interrupted


def _handle_faces_scan(args: argparse.Namespace) -> int:
    """Detect faces and compute embeddings for all images."""
    if scan_and_store is None:
        print(
            "Error: face_recognition is not installed. "
            "Install the [face] extra: pip install pyimgtag[face]",
            file=sys.stderr,
        )
        return 1

    try:
        from pyimgtag.face_detection import _check_face_recognition

        _check_face_recognition()
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    extensions = {e.strip().lower() for e in args.extensions.split(",")}

    try:
        if args.input_dir:
            files = scan_directory(args.input_dir, extensions)
        else:
            files = scan_photos_library(args.photos_library, extensions)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not files:
        print("No image files found.", file=sys.stderr)
        return 0

    quality = _resolve_face_quality(args)
    err = _validate_face_quality(quality)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    size_note = f", min-face-size={quality['min_face_size']}" if quality["min_face_size"] else ""
    print(
        f"Quality: {getattr(args, 'quality', None) or 'balanced'} "
        f"(model={quality['model']}, max-dim={quality['max_dim']}, "
        f"upsample={quality['upsample']}, jitters={quality['num_jitters']}{size_note}). "
        "Use --quality fast for the previous speed.",
        file=sys.stderr,
    )
    print(
        "Already-scanned images are skipped; run 'faces reset-untrusted --yes' "
        "(or 'faces reset') first to re-detect them at a new quality.",
        file=sys.stderr,
    )

    stats = {"scanned": 0, "faces": 0, "errors": 0, "not_downloaded": 0, "skipped_existing": 0}
    jobs = getattr(args, "jobs", 1)
    if jobs == 0:  # 0 = auto: one worker per CPU
        jobs = os.cpu_count() or 1
    jobs = max(1, jobs)

    session, dashboard = start_dashboard_for(args, command="faces scan")
    if session is not None:
        session.set_counter("scanned_total", len(files))
        session.mark_running()

    interrupted = False
    try:
        with ProgressDB(db_path=args.db) as db:
            stop_event = threading.Event()
            cluster_thread = _start_cluster_thread(db, args, stop_event)

            try:
                if jobs <= 1:
                    interrupted = _scan_serial(
                        db, files, quality, limit=args.limit, session=session, stats=stats
                    )
                else:
                    interrupted = _scan_parallel(
                        db,
                        files,
                        quality,
                        jobs=jobs,
                        limit=args.limit,
                        session=session,
                        stats=stats,
                    )
            finally:
                stop_event.set()
                cluster_thread.join()

        summary = f"\nScanned {stats['scanned']} new image(s), detected {stats['faces']} faces"
        if stats["errors"]:
            summary += f", {stats['errors']} error(s) skipped"
        if stats["not_downloaded"]:
            summary += f", {stats['not_downloaded']} not downloaded locally (skipped)"
        if stats["skipped_existing"]:
            summary += (
                f". {stats['skipped_existing']} image(s) already scanned and skipped — "
                "run 'faces reset-untrusted --yes' (or 'faces reset') first to re-detect them"
            )
        if jobs > 1:
            summary += f" [parallel: {jobs} workers]"
        print(summary + ".", file=sys.stderr)
        if session is not None and not interrupted:
            session.mark_completed()
    finally:
        if dashboard is not None:
            dashboard.stop()
        run_registry.set_current(None)

    return 1 if interrupted else 0


def _start_cluster_thread(
    db: ProgressDB,
    args: argparse.Namespace,
    stop_event: threading.Event,
) -> threading.Thread:
    """Start a daemon thread that periodically re-clusters detected faces.

    Opens its own ProgressDB connection so SQLite writes from the scan
    loop and the cluster loop never share a connection object across threads.
    Runs :func:`~pyimgtag.face_clustering.recluster_auto` every
    ``_CLUSTER_INTERVAL_S`` seconds so the faces UI stays current during
    a long scan.  The thread stops as soon as *stop_event* is set and
    performs one final cluster pass before exiting.
    """
    try:
        from pyimgtag.face_clustering import recluster_auto
    except ImportError:
        # scikit-learn not installed — skip background clustering silently
        t = threading.Thread(target=lambda: None, daemon=True)
        t.start()
        return t

    eps = getattr(args, "eps", 0.5)
    min_samples = getattr(args, "min_samples", 2)
    db_path = db.path

    def _loop() -> None:
        with ProgressDB(db_path=db_path) as thread_db:
            while not stop_event.wait(timeout=_CLUSTER_INTERVAL_S):
                try:
                    recluster_auto(thread_db, eps=eps, min_samples=min_samples)
                except Exception:  # noqa: BLE001 — background clustering must not crash the scan
                    logger.debug("background recluster failed", exc_info=True)
            # Final pass after scan finishes
            try:
                recluster_auto(thread_db, eps=eps, min_samples=min_samples)
            except Exception:  # noqa: BLE001 — background clustering must not crash the scan
                logger.debug("final background recluster failed", exc_info=True)

    t = threading.Thread(target=_loop, daemon=True, name="faces-cluster-bg")
    t.start()
    return t


def _handle_faces_cluster(args: argparse.Namespace) -> int:
    """Cluster detected faces into person groups."""
    try:
        from pyimgtag.face_clustering import cluster_faces
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    with ProgressDB(db_path=args.db) as db:
        result = cluster_faces(db, eps=args.eps, min_samples=args.min_samples)

    if not result:
        print(
            "No clusters formed. Need more faces or adjust --eps/--min-samples.",
            file=sys.stderr,
        )
        return 0

    print(f"Created {len(result)} person cluster(s):", file=sys.stderr)
    for person_id, face_ids in result.items():
        print(f"  Person {person_id}: {len(face_ids)} face(s)", file=sys.stderr)
    return 0


def _handle_faces_review(args: argparse.Namespace) -> int:
    """List detected persons and face counts."""
    with ProgressDB(db_path=args.db) as db:
        persons = db.get_persons()
        total_faces = db.get_face_count()

    if not persons and total_faces == 0:
        print("No faces detected yet. Run 'pyimgtag faces scan' first.", file=sys.stderr)
        return 0

    assigned = sum(len(p.face_ids) for p in persons)
    unassigned = total_faces - assigned

    print(
        f"Faces: {total_faces} total, {assigned} assigned, {unassigned} unassigned", file=sys.stderr
    )
    if persons:
        print(f"\nPersons ({len(persons)}):", file=sys.stderr)
        for p in persons:
            status = "confirmed" if p.confirmed else "auto"
            label = p.label or f"(unlabelled #{p.person_id})"
            print(f"  [{status}] {label}: {len(p.face_ids)} face(s)", file=sys.stderr)
    if unassigned > 0:
        print(f"\n{unassigned} face(s) not assigned to any person.", file=sys.stderr)
        print("Run 'pyimgtag faces cluster' to group them.", file=sys.stderr)
    return 0


def _handle_faces_apply(args: argparse.Namespace) -> int:
    """Write person keywords to image metadata."""
    with ProgressDB(db_path=args.db) as db:
        persons = db.get_persons()
        if not persons:
            print(
                "No persons found. Run 'pyimgtag faces scan' and 'faces cluster' first.",
                file=sys.stderr,
            )
            return 0

        # Build face_id -> person label mapping
        face_to_label: dict[int, str] = {}
        for p in persons:
            label = p.label or f"person_{p.person_id}"
            for fid in p.face_ids:
                face_to_label[fid] = label

        # Build image_path -> list of person keywords
        image_keywords: dict[str, list[str]] = {}
        rows = db.get_assigned_faces()
        for row in rows:
            face_id = row["id"]
            image_path = row["image_path"]
            label = face_to_label.get(face_id, "")
            if label:
                keyword = f"person:{label}"
                image_keywords.setdefault(image_path, [])
                if keyword not in image_keywords[image_path]:
                    image_keywords[image_path].append(keyword)

    if not image_keywords:
        print("No face-to-person assignments to write.", file=sys.stderr)
        return 0

    written = 0
    for image_path, keywords in sorted(image_keywords.items()):
        if args.dry_run:
            print(f"  [dry-run] {Path(image_path).name}: {', '.join(keywords)}", file=sys.stderr)
            continue

        if not (args.write_exif or args.sidecar_only):
            print(f"  {Path(image_path).name}: {', '.join(keywords)}", file=sys.stderr)
            continue

        err = _write_person_keywords(image_path, keywords, args)
        if err:
            print(f"  {Path(image_path).name}: FAILED - {err}", file=sys.stderr)
        else:
            written += 1
            print(f"  {Path(image_path).name}: {', '.join(keywords)}", file=sys.stderr)

    if args.dry_run:
        print(f"\n[dry-run] Would write to {len(image_keywords)} image(s).", file=sys.stderr)
    elif args.write_exif or args.sidecar_only:
        print(
            f"\nWrote person keywords to {written}/{len(image_keywords)} image(s).",
            file=sys.stderr,
        )
    else:
        print(f"\n{len(image_keywords)} image(s) have person keywords.", file=sys.stderr)
        print("Use --write-exif or --sidecar-only to write them to files.", file=sys.stderr)
    return 0


def _handle_faces_import_photos(args: argparse.Namespace) -> int:
    """Import named persons from Apple Photos into the faces DB.

    A library-wide enumeration on a 20k+ photo library can take many
    minutes. Two UX guards live here:

    - The startup banner and periodic counter are *always* emitted
      (regardless of ``--verbose``) so the user can tell the command is
      working rather than hung — silence-by-default was the original
      bug.
    - ``KeyboardInterrupt`` is caught and turned into a single
      ``Aborted…`` message + non-zero exit, replacing the noisy
      photoscript / AppleScript traceback.
    """
    try:
        from pyimgtag.photos_faces_importer import import_photos_persons
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    with ProgressDB(db_path=args.db) as db:
        try:
            imported, skipped = import_photos_persons(
                db, library_path=getattr(args, "library", None)
            )
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\nAborted by user before import completed.", file=sys.stderr)
            return 130

    print(f"Imported {imported} person(s) from Apple Photos.", file=sys.stderr)
    if skipped:
        print(
            f"{skipped} multi-face photo(s) could not be auto-assigned — use 'faces ui' to review.",
            file=sys.stderr,
        )
    return 0


def _handle_faces_match_references(args: argparse.Namespace) -> int:
    """Name auto-clustered people from a folder of labeled reference faces.

    The escape hatch for Photos libraries that can't be enumerated via
    AppleScript: drop one labeled image (or sub-folder) per person into
    ``<dir>`` and match clusters to them by face embedding. Dry-run by
    default; pass ``--apply`` to write the names.
    """
    from pathlib import Path

    from pyimgtag.face_naming import (
        apply_matches,
        load_reference_embeddings,
        match_clusters_to_references,
    )

    ref_dir = Path(args.reference_dir)
    if not ref_dir.is_dir():
        print(f"Error: reference dir not found: {ref_dir}", file=sys.stderr)
        return 1

    try:
        from pyimgtag.face_detection import _check_face_recognition

        _check_face_recognition()
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Loading reference faces from {ref_dir}…", file=sys.stderr)
    references = load_reference_embeddings(ref_dir)
    if not references:
        print(
            "No usable reference faces found. Add one image (or sub-folder) per "
            "person, e.g. 'Alice.jpg' or 'Alice/01.jpg'.",
            file=sys.stderr,
        )
        return 1
    ref_faces = sum(len(v) for v in references.values())
    print(f"Loaded {len(references)} name(s) from {ref_faces} reference face(s).", file=sys.stderr)

    threshold = getattr(args, "threshold", None) or 0.5
    with ProgressDB(db_path=args.db) as db:
        matches = match_clusters_to_references(db, references, threshold=threshold)
        if not matches:
            print("No clusters matched a reference within the threshold.", file=sys.stderr)
            return 0

        for m in matches:
            cur = m.current_label or f"Person {m.person_id}"
            print(
                f"  {cur} ({m.face_count} face(s)) → {m.name} (distance {m.distance:.3f})",
                file=sys.stderr,
            )

        if not getattr(args, "apply", False):
            print(
                f"\n{len(matches)} cluster(s) would be named. Re-run with --apply to write them.",
                file=sys.stderr,
            )
            return 0

        result = apply_matches(db, matches)
    print(
        f"\nNamed {result['renamed'] + result['merged']} cluster(s) "
        f"({result['renamed']} renamed, {result['merged']} merged into existing people).",
        file=sys.stderr,
    )
    return 0


def _handle_faces_capture_names(args: argparse.Namespace) -> int:
    """Name auto clusters from a screenshot of Apple Photos' People view.

    The "screen OCR" path: detect+embed the face under each People tile, read
    the caption with macOS Vision OCR, pair them by position, and match the
    resulting names to auto clusters (same matcher as ``match-references``).
    Source is either an existing ``--screenshot`` image or a fresh ``--live``
    capture. Dry-run by default; ``--apply`` writes the names.
    """
    import tempfile

    from pyimgtag.face_naming import apply_matches, match_clusters_to_references
    from pyimgtag.face_ocr import (
        OcrUnavailableError,
        build_references_from_screenshot,
        capture_people_screenshot,
    )

    screenshot = getattr(args, "screenshot", None)
    live = getattr(args, "live", False)
    if not screenshot and not live:
        print("Error: pass --screenshot PATH or --live.", file=sys.stderr)
        return 1

    # Face detection/encoding must be available before we capture or read.
    try:
        from pyimgtag.face_detection import _check_face_recognition

        _check_face_recognition()
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    tmp_holder: tempfile.TemporaryDirectory | None = None
    try:
        if live:
            save_to = getattr(args, "save_screenshot", None)
            if save_to:
                shot_path = Path(save_to)
            else:
                tmp_holder = tempfile.TemporaryDirectory(prefix="pyimgtag-people-")
                shot_path = Path(tmp_holder.name) / "people.png"
            print(
                "Capturing the Apple Photos window… (have the People album open)",
                file=sys.stderr,
            )
            try:
                capture_people_screenshot(shot_path)
            except OcrUnavailableError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
        else:
            assert screenshot is not None  # guaranteed by the source check above
            shot_path = Path(screenshot)
            if not shot_path.is_file():
                print(f"Error: screenshot not found: {shot_path}", file=sys.stderr)
                return 1

        languages = None
        raw_langs = getattr(args, "languages", None)
        if raw_langs:
            languages = [c.strip() for c in raw_langs.split(",") if c.strip()]

        print(f"Reading names from {shot_path}…", file=sys.stderr)
        try:
            references = build_references_from_screenshot(shot_path, languages=languages)
        except OcrUnavailableError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    finally:
        if tmp_holder is not None:
            tmp_holder.cleanup()

    if not references:
        print(
            "No named faces found in the screenshot. Make sure the People grid "
            "is visible with names under each face.",
            file=sys.stderr,
        )
        return 1
    ref_faces = sum(len(v) for v in references.values())
    print(
        f"Read {len(references)} name(s) from {ref_faces} face(s): {', '.join(sorted(references))}",
        file=sys.stderr,
    )

    threshold = getattr(args, "threshold", None) or 0.5
    with ProgressDB(db_path=args.db) as db:
        matches = match_clusters_to_references(db, references, threshold=threshold)
        if not matches:
            print("No clusters matched a recognized name within the threshold.", file=sys.stderr)
            return 0

        for m in matches:
            cur = m.current_label or f"Person {m.person_id}"
            print(
                f"  {cur} ({m.face_count} face(s)) → {m.name} (distance {m.distance:.3f})",
                file=sys.stderr,
            )

        if not getattr(args, "apply", False):
            print(
                f"\n{len(matches)} cluster(s) would be named. Re-run with --apply to write them.",
                file=sys.stderr,
            )
            return 0

        result = apply_matches(db, matches)
    print(
        f"\nNamed {result['renamed'] + result['merged']} cluster(s) "
        f"({result['renamed']} renamed, {result['merged']} merged into existing people).",
        file=sys.stderr,
    )
    return 0


def _handle_faces_ui(args: argparse.Namespace) -> int:
    """Start the face management web UI."""
    try:
        import uvicorn
    except ImportError:
        print(
            "Error: uvicorn is required for the faces UI. "
            "Install with: pip install 'pyimgtag[review]'",
            file=sys.stderr,
        )
        return 1

    try:
        from pyimgtag.webapp.unified_app import create_unified_app
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    app = create_unified_app(db_path=args.db)
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8766)
    url = f"http://{host}:{port}/faces"
    print(f"pyimgtag faces UI: {url}", flush=True)

    if not getattr(args, "no_browser", False):
        import webbrowser

        def _open() -> None:
            import time

            time.sleep(0.5)
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001  # nosec B110
                pass

        threading.Thread(target=_open, daemon=True).start()

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except OSError as exc:
        # Most commonly the port is already in use (EADDRINUSE); surface an
        # actionable message instead of an unhandled traceback.
        print(f"Error: could not start faces UI on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    return 0


def _write_person_keywords(
    image_path: str,
    keywords: list[str],
    args: argparse.Namespace,
) -> str | None:
    """Write person keywords to one image. Returns error string or None."""
    from pyimgtag.exif_writer import (
        SUPPORTED_DIRECT_WRITE_EXTENSIONS,
        write_exif_description,
        write_xmp_sidecar,
    )

    if args.sidecar_only:
        return write_xmp_sidecar(image_path, keywords=keywords)

    ext = Path(image_path).suffix.lower()
    if ext not in SUPPORTED_DIRECT_WRITE_EXTENSIONS:
        return write_xmp_sidecar(image_path, keywords=keywords)

    return write_exif_description(image_path, keywords=keywords, merge=True)
