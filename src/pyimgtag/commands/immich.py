"""Handler for the ``immich-sync`` subcommand."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyimgtag.progress_db import ProgressDB

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import datetime

    from pyimgtag.immich import SyncPlan


def _taken_at(row: dict) -> "datetime | None":
    """The capture timestamp for a row, in either stored spelling."""
    # Shared with ``events``: both commands have to read the two forms the DB
    # stores (ISO 8601 from Pillow, ``YYYY:MM:DD`` from exiftool), and a second
    # parser would drift from the first.
    from pyimgtag.commands.events import _parse_date

    return _parse_date(row.get("image_date"))


def build_plan(
    rows: list[dict],
    client: Any,
    *,
    favorite_threshold: int | None = None,
    tag_prefix: str = "",
) -> "SyncPlan":
    """Work out what a sync would do, touching nothing.

    Every local photo is searched for by filename and capture date; the ones
    that resolve to exactly one asset get their tags and, above the judge
    threshold, a favourite. The rest are recorded with the reason, because
    "47 unmatched" without reasons is not actionable.
    """
    from pyimgtag.immich import SyncPlan, match_asset

    plan = SyncPlan()
    for row in rows:
        file_name = row.get("file_name") or Path(row["file_path"]).name
        taken_at = _taken_at(row)
        candidates = client.search_by_filename(file_name, taken_at)
        result = match_asset(file_name, taken_at, candidates)
        result.file_path = row["file_path"]  # only the caller knows the path

        asset_id = result.asset_id
        if asset_id is None:
            plan.unmatched.append(result)
            continue
        plan.matched.append(result)

        tags = [f"{tag_prefix}{t}" for t in (row.get("tags") or []) if str(t).strip()]
        if tags:
            plan.tags_by_asset[asset_id] = tags
        score = row.get("judge_score")
        if favorite_threshold is not None and score is not None and score >= favorite_threshold:
            plan.favorites.append(asset_id)
    return plan


def _resolve_tag_ids(client: Any, names: set[str], *, apply: bool) -> dict[str, str]:
    """Map full tag path to immich tag id.

    In a dry run nothing may be created, so only the tags that already exist
    can be resolved; with ``--apply`` one upsert call resolves and creates the
    whole set at once.
    """
    if not names:
        return {}
    if apply:
        return client.upsert_tags(sorted(names))
    existing = {
        str(tag.get("value") or tag.get("name")): str(tag.get("id"))
        for tag in client.list_tags()
        if tag.get("id")
    }
    return {name: existing[name] for name in sorted(names) if name in existing}


def cmd_immich_sync(args: argparse.Namespace) -> int:
    """Execute the immich-sync subcommand."""
    from pyimgtag.immich import TESTED_IMMICH_VERSION, ImmichClient, ImmichError, resolve_api_key

    try:
        api_key = resolve_api_key(args.api_key)
    except ImmichError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    apply = bool(getattr(args, "apply", False))
    print(
        f"immich-sync is experimental; written against immich {TESTED_IMMICH_VERSION}. "
        f"{'Applying changes.' if apply else 'Dry run -- nothing will be written.'}",
        file=sys.stderr,
    )

    client = ImmichClient(args.url, api_key)
    with ProgressDB(db_path=args.db) as db:
        rows = db.export_rows()
    rows = [r for r in rows if r.get("status") == "ok"]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("Nothing to sync: the database has no processed images.", file=sys.stderr)
        return 0

    try:
        plan = build_plan(
            rows,
            client,
            favorite_threshold=args.favorite_min_score,
            tag_prefix=args.tag_prefix or "",
        )
    except ImmichError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(plan.summary(), file=sys.stderr)
    for result in plan.unmatched[:20]:
        print(f"  unmatched: {Path(result.file_path).name} — {result.reason}", file=sys.stderr)
    if len(plan.unmatched) > 20:
        print(f"  ... and {len(plan.unmatched) - 20} more", file=sys.stderr)

    wanted = {name for names in plan.tags_by_asset.values() for name in names}

    if not apply:
        # Read-only, so the dry run can say how much of this is new. "12 tags
        # to push" reads very differently from "12 tags to push, 12 of them
        # new" when you are deciding whether to let it near your library.
        try:
            existing = _resolve_tag_ids(client, wanted, apply=False)
        except ImmichError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(
            f"{len(wanted) - len(existing)} of {len(wanted)} tag(s) would be created; "
            f"the rest already exist.",
            file=sys.stderr,
        )
        print("Dry run complete. Re-run with --apply to push.", file=sys.stderr)
        return 0

    try:
        tag_ids = _resolve_tag_ids(client, wanted, apply=True)
        missing = sorted(wanted - set(tag_ids))
        if missing:
            print(
                f"Warning: the server did not return an id for {len(missing)} tag(s); "
                f"they were not pushed: {', '.join(missing[:5])}",
                file=sys.stderr,
            )

        # Grouped by tag rather than by asset: one bulk call per tag instead of
        # one per photo, which on a 40,000-photo library is the difference
        # between a few dozen requests and forty thousand.
        assets_by_tag: dict[str, list[str]] = {}
        for asset_id, names in plan.tags_by_asset.items():
            for name in names:
                if name in tag_ids:
                    assets_by_tag.setdefault(tag_ids[name], []).append(asset_id)
        for tag_id, asset_ids in sorted(assets_by_tag.items()):
            client.tag_assets([tag_id], sorted(set(asset_ids)))

        if plan.favorites:
            client.set_favorite(sorted(set(plan.favorites)), True)
    except ImmichError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Pushed tags for {len(plan.tags_by_asset)} asset(s) and "
        f"{len(set(plan.favorites))} favourite(s).",
        file=sys.stderr,
    )
    return 0
