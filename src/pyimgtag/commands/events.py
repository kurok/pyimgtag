"""Handler for the ``events`` subcommand: detect, list, show, name, rename, apply."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from pyimgtag.progress_db import ProgressDB

#: Naming sends metadata only -- never image bytes -- so it is cheap, works
#: against a local text model, and cannot leak a photo to a cloud backend.
_NAME_PROMPT = """You are naming a photo album. Below is metadata about one \
event: when it happened, where, and what the photos contain. No images are \
included.

{facts}

Reply with ONLY the album name: 2-5 words, no quotes, no punctuation at the \
end, no explanation. Prefer a name a person would recognise, like "Anna's \
birthday dinner" or "Lisbon weekend"."""


def _parse_date(raw: str | None) -> datetime | None:
    """Read the DB's capture timestamp in either stored spelling.

    ``image_date`` holds an ISO 8601 string from the Pillow path or the raw
    ``YYYY:MM:DD HH:MM:SS`` exiftool form; both have to become a datetime here
    or every photo tagged through one of the two paths would be unassignable.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if len(text) >= 10 and text[4] == ":" and text[7] == ":":
        text = text[:4] + "-" + text[5:7] + "-" + text[8:]
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _points(db: ProgressDB) -> list:
    """Every processed photo as a :class:`~pyimgtag.events.PhotoPoint`."""
    from pyimgtag.events import PhotoPoint

    rows = db.query_images(status="ok")
    points = []
    for row in rows:
        place = row.get("nearest_city") or row.get("nearest_region") or row.get("nearest_country")
        points.append(
            PhotoPoint(
                file_path=row["file_path"],
                taken_at=_parse_date(row.get("image_date")),
                lat=row.get("gps_lat"),
                lon=row.get("gps_lon"),
                place=place,
                event_hint=row.get("event_hint"),
                tags=tuple(row.get("tags_list") or ()),
            )
        )
    return points


def cmd_events(args: argparse.Namespace) -> int:
    """Dispatch to the selected ``events`` action."""
    actions = {
        "detect": _detect,
        "list": _list,
        "show": _show,
        "name": _name,
        "rename": _rename,
        "apply": _apply,
    }
    handler = actions.get(getattr(args, "events_action", None) or "")
    if handler is None:
        print("Usage: pyimgtag events {detect|list|show|name|rename|apply}", file=sys.stderr)
        return 1
    with ProgressDB(db_path=args.db) as db:
        return handler(args, db)


def _detect(args: argparse.Namespace, db: ProgressDB) -> int:
    from pyimgtag.events import detect_events, fallback_name

    points = _points(db)
    if not points:
        print("No processed photos to cluster. Run 'pyimgtag run' first.", file=sys.stderr)
        return 0

    events, unassigned = detect_events(
        points,
        gap_hours=args.gap_hours,
        distance_km=args.distance_km,
    )
    clusters = [
        {
            "members": [p.file_path for p in event.members],
            "started_at": event.started_at.isoformat(timespec="seconds"),
            "ended_at": event.ended_at.isoformat(timespec="seconds"),
            "place": event.places[0] if event.places else None,
            "trip": event.trip,
            "fallback_name": fallback_name(event),
        }
        for event in events
    ]
    counts = db.reconcile_events(clusters)

    trips = len({e.trip for e in events if e.trip is not None})
    print(
        f"{len(events)} event(s), {trips} trip(s) from {len(points) - len(unassigned)} photo(s).",
        file=sys.stderr,
    )
    print(
        f"  {counts['created']} new, {counts['updated']} updated, {counts['removed']} removed.",
        file=sys.stderr,
    )
    if unassigned:
        print(
            f"  {len(unassigned)} photo(s) have no capture date "
            f"(see 'pyimgtag events list --unassigned').",
            file=sys.stderr,
        )
    return 0


def _list(args: argparse.Namespace, db: ProgressDB) -> int:
    import json as _json

    if args.unassigned:
        # Deliberately recomputed rather than stored: "no capture date" is a
        # property of the photo, and storing it would need invalidating every
        # time a row is re-tagged.
        undated = [p.file_path for p in _points(db) if p.taken_at is None]
        if args.format == "json":
            print(_json.dumps(undated, indent=2))
        else:
            for path in undated:
                print(path)
        print(f"\n{len(undated)} photo(s) with no capture date.", file=sys.stderr)
        return 0

    events = db.list_events(limit=args.limit)
    if not events:
        print("No events yet. Run 'pyimgtag events detect'.", file=sys.stderr)
        return 0

    if args.format == "json":
        print(_json.dumps(events, indent=2))
        return 0

    name_w = 34
    print(f"{'ID':>5}  {'NAME':<{name_w}}  {'DATES':<23}  {'TRIP':>4}  {'PHOTOS':>6}")
    print("-" * (5 + 2 + name_w + 2 + 23 + 2 + 4 + 2 + 6))
    for event in events:
        name = (event["name"] or "")[:name_w]
        dates = f"{(event['started_at'] or '')[:10]} .. {(event['ended_at'] or '')[:10]}"
        trip = "" if event["trip_id"] is None else str(event["trip_id"])
        print(
            f"{event['id']:>5}  {name:<{name_w}}  {dates:<23}  {trip:>4}  {event['photo_count']:>6}"
        )
    print(f"\n{len(events)} event(s).", file=sys.stderr)
    return 0


def _show(args: argparse.Namespace, db: ProgressDB) -> int:
    import json as _json

    event = db.get_event(args.event_id)
    if event is None:
        print(f"Error: no event with id {args.event_id}.", file=sys.stderr)
        return 1
    if args.format == "json":
        print(_json.dumps(event, indent=2))
        return 0
    print(f"{event['name']}  (id {event['id']})")
    print(f"  {event['started_at']} .. {event['ended_at']}")
    if event["place"]:
        print(f"  {event['place']}")
    print(f"  {len(event['members'])} photo(s)")
    for path in event["members"]:
        print(f"    {path}")
    return 0


def _event_facts(db: ProgressDB, event: dict) -> str:
    """The metadata-only description a naming model is given.

    Image bytes are never part of this. Naming reads what the tagger already
    extracted, which is what keeps it cheap enough to run against a local text
    model and stops a photo reaching a cloud backend that never saw one.
    """
    paths = db.event_paths(event["id"])
    rows = [row for row in db.query_images(status="ok") if row["file_path"] in paths]

    tags: dict[str, int] = {}
    hints: dict[str, int] = {}
    places: dict[str, int] = {}
    for row in rows:
        for tag in row.get("tags_list") or ():
            tags[tag] = tags.get(tag, 0) + 1
        if row.get("event_hint"):
            hints[row["event_hint"]] = hints.get(row["event_hint"], 0) + 1
        for key in ("nearest_city", "nearest_region", "nearest_country"):
            if row.get(key):
                places[row[key]] = places.get(row[key], 0) + 1

    def _top(counter: dict[str, int], n: int) -> list[str]:
        return [k for k, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]

    people = sorted(
        {
            person.label
            for person in db.get_persons()
            if person.label and db.paths_for_person_label(person.label) & paths
        }
    )

    lines = [
        f"Dates: {event['started_at']} to {event['ended_at']}",
        f"Photos: {len(paths)}",
    ]
    if places:
        lines.append(f"Places: {', '.join(_top(places, 4))}")
    if tags:
        lines.append(f"Common tags: {', '.join(_top(tags, 10))}")
    if hints:
        lines.append(f"Event hints: {', '.join(_top(hints, 5))}")
    if people:
        lines.append(f"People: {', '.join(people[:8])}")
    return "\n".join(lines)


def _name(args: argparse.Namespace, db: ProgressDB) -> int:
    pending = db.unnamed_events()
    if not pending:
        print("Every event already has a name.", file=sys.stderr)
        return 0

    client = None
    if not args.fallback_only:
        try:
            client = _naming_client(args)
        except Exception as exc:  # noqa: BLE001 — falls back rather than failing
            print(
                f"Warning: no naming backend available ({type(exc).__name__}: {exc}). "
                f"Keeping the generated names.",
                file=sys.stderr,
            )

    named = 0
    for event in pending:
        if client is None:
            continue
        facts = _event_facts(db, event)
        try:
            suggestion = client(_NAME_PROMPT.format(facts=facts))
        except Exception as exc:  # noqa: BLE001 — one failure must not end the run
            print(f"  event {event['id']}: naming failed ({exc})", file=sys.stderr)
            continue
        cleaned = " ".join(str(suggestion).strip().splitlines()[:1].__iter__()).strip(" .\"'")
        if cleaned:
            db.set_event_name(event["id"], cleaned[:120], "model")
            named += 1
            if args.verbose:
                print(f"  {event['id']}: {cleaned}", file=sys.stderr)

    print(
        f"{named} event(s) named by the model; {len(pending) - named} kept their generated name.",
        file=sys.stderr,
    )
    return 0


def _naming_client(args: argparse.Namespace) -> Any:
    """A ``str -> str`` callable over the configured text backend.

    Ollama only, for now. The cloud clients in :mod:`pyimgtag.cloud_clients`
    are vision clients: their payload builders are shaped around an image
    part, and naming needs a text-only call. Rather than guess at three
    provider payloads that cannot be exercised here, this says so and leaves
    the deterministic names in place -- which is why ``events name`` is
    optional rather than a prerequisite.
    """
    import os

    backend = args.backend or os.environ.get("PYIMGTAG_BACKEND") or "ollama"
    if backend != "ollama":
        raise RuntimeError(
            f"event naming currently supports --backend ollama only "
            f"(got {backend!r}); the generated names are kept"
        )

    from pyimgtag.ollama_client import OllamaClient

    kwargs: dict[str, Any] = {}
    if getattr(args, "ollama_url", None):
        kwargs["base_url"] = args.ollama_url
    if getattr(args, "model", None):
        kwargs["model"] = args.model
    client = OllamaClient(**kwargs)
    return lambda prompt: client.generate_text(prompt)


def _rename(args: argparse.Namespace, db: ProgressDB) -> int:
    if not db.rename_event(args.event_id, args.name):
        print(f"Error: no event with id {args.event_id}.", file=sys.stderr)
        return 1
    print(f"Event {args.event_id} renamed to {args.name!r}.", file=sys.stderr)
    return 0


def _safe_dirname(name: str) -> str:
    """A directory name that survives every filesystem this runs on.

    Windows rejects ``<>:"/\\|?*``; macOS and Linux only reject the separator,
    but a shared export folder should not change shape depending on where it
    was written.
    """
    cleaned = "".join("-" if ch in '<>:"/\\|?*' else ch for ch in name)
    cleaned = " ".join(cleaned.split()).strip(". ")
    return cleaned[:100] or "event"


def _unique_dirname(event: dict, used: set[str]) -> str:
    """A folder name for *event* that no earlier event has taken.

    Falls back to the start date, then the event id — both stable across runs,
    so re-exporting lands on the same folders rather than shuffling them.
    """
    base = _safe_dirname(event["name"])
    candidate = base
    if candidate in used:
        candidate = _safe_dirname(f"{base} ({(event.get('started_at') or '')[:10]})")
    if candidate in used:
        candidate = _safe_dirname(f"{base} ({event['id']})")
    used.add(candidate)
    return candidate


def _apply(args: argparse.Namespace, db: ProgressDB) -> int:
    events = db.list_events()
    if not events:
        print("No events yet. Run 'pyimgtag events detect'.", file=sys.stderr)
        return 0

    if args.export_dir:
        return _apply_export(args, db, events)
    if args.photos_albums:
        return _apply_photos_albums(args, db, events)
    if args.write_keywords:
        return _apply_keywords(args, db, events)
    print(
        "Choose what to create: --export-dir DIR, --photos-albums, or --write-keywords.",
        file=sys.stderr,
    )
    return 1


def _apply_export(args: argparse.Namespace, db: ProgressDB, events: list[dict]) -> int:
    import os
    import shutil

    root = Path(args.export_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    made = linked = skipped = 0
    # Two events can legitimately share a name -- consecutive days of one trip
    # both read "Lisbon — June 2024" -- and without this they would silently
    # merge into one folder holding both days.
    used: set[str] = set()
    for event in events:
        folder = root / _unique_dirname(event, used)
        folder.mkdir(exist_ok=True)
        made += 1
        for source in sorted(db.event_paths(event["id"])):
            src = Path(source)
            dest = folder / src.name
            if dest.exists() or dest.is_symlink():
                skipped += 1
                continue
            try:
                if args.copy:
                    shutil.copy2(src, dest)
                else:
                    # Symlinks by default: an album of 4,000 photos should not
                    # double the library's size to exist.
                    os.symlink(src, dest)
                linked += 1
            except OSError as exc:
                print(f"  {src}: {exc}", file=sys.stderr)
                skipped += 1
    verb = "copied" if args.copy else "linked"
    print(
        f"{made} folder(s) under {root}; {linked} photo(s) {verb}, {skipped} skipped.",
        file=sys.stderr,
    )
    return 0


def _apply_photos_albums(args: argparse.Namespace, db: ProgressDB, events: list[dict]) -> int:
    import sys as _sys

    if _sys.platform != "darwin":
        print(
            "--photos-albums needs macOS (it drives Apple Photos through osascript). "
            "Use --export-dir on this platform.",
            file=sys.stderr,
        )
        return 1

    from pyimgtag.applescript_writer import add_to_album

    created = added = failed = 0
    for event in events:
        paths = sorted(db.event_paths(event["id"]))
        if not paths:
            continue
        # Album membership only: nothing is moved, renamed or deleted in the
        # user's Photos library by this command.
        ok, count = add_to_album(event["name"], paths, dry_run=args.dry_run)
        if ok:
            created += 1
            added += count
        else:
            failed += 1
    print(
        f"{created} album(s) touched, {added} photo(s) added, {failed} failed.",
        file=sys.stderr,
    )
    return 1 if failed and not created else 0


def _apply_keywords(args: argparse.Namespace, db: ProgressDB, events: list[dict]) -> int:
    from pyimgtag.exif_writer import write_exif_description

    hierarchical_wanted = getattr(args, "hierarchical_keywords", False)
    written = failed = 0
    for event in events:
        keyword = f"Event/{event['name']}"
        hierarchical = None
        if hierarchical_wanted:
            from pyimgtag.hierarchy import keyword_paths

            hierarchical = keyword_paths(event=event["name"])
        for path in sorted(db.event_paths(event["id"])):
            if args.dry_run:
                written += 1
                continue
            # merge=True: the event keyword joins the tags the run already
            # wrote rather than replacing somebody's whole keyword list.
            error = write_exif_description(
                path, keywords=[keyword], merge=True, hierarchical=hierarchical
            )
            if error is None:
                written += 1
            else:
                print(f"  {path}: {error}", file=sys.stderr)
                failed += 1
    prefix = "would write" if args.dry_run else "wrote"
    print(f"{prefix} {written} keyword(s); {failed} failed.", file=sys.stderr)
    return 0
