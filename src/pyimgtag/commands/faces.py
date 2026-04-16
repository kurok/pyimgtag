"""Handlers for the ``faces`` subcommand group."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pyimgtag.progress_db import ProgressDB
from pyimgtag.scanner import scan_directory, scan_photos_library


def cmd_faces(args: argparse.Namespace) -> int:
    """Dispatch faces sub-actions."""
    if args.faces_action is None:
        print("Usage: pyimgtag faces {scan,cluster,review,apply}", file=sys.stderr)
        return 1

    if args.faces_action == "scan":
        return _handle_faces_scan(args)
    if args.faces_action == "cluster":
        return _handle_faces_cluster(args)
    if args.faces_action == "review":
        return _handle_faces_review(args)
    if args.faces_action == "apply":
        return _handle_faces_apply(args)

    return 1


def _handle_faces_scan(args: argparse.Namespace) -> int:
    """Detect faces and compute embeddings for all images."""
    try:
        from pyimgtag.face_embedding import scan_and_store
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

    total_faces = 0
    scanned = 0
    with ProgressDB(db_path=args.db) as db:
        try:
            for i, file_path in enumerate(files):
                if args.limit and i >= args.limit:
                    break
                count = scan_and_store(
                    file_path, db, max_dim=args.max_dim, model=args.detection_model
                )
                scanned += 1
                if count > 0:
                    total_faces += count
                    print(f"  {file_path.name}: {count} face(s)", file=sys.stderr)
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)

    print(f"\nScanned {scanned} images, detected {total_faces} faces.", file=sys.stderr)
    return 0


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
        rows = db._conn.execute(
            "SELECT id, image_path FROM faces WHERE person_id IS NOT NULL"
        ).fetchall()
        for face_id, image_path in rows:
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
