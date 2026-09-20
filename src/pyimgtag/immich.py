"""Minimal immich API client for ``pyimgtag immich-sync``.

**Experimental.** Written against immich's published OpenAPI specification —
the request shapes are checked against a committed slice of it in
``tests/test_immich.py`` — but not yet exercised against a running server. See
the README for what that does and does not guarantee.

One-way by design: pyimgtag pushes tags and favourites, and never reads edits
back. Two-way sync needs a conflict model, and a tagger that silently
overwrites a user's own edits is worse than one that does nothing.

Only four endpoints are touched, all documented in immich 3.2.0:

- ``POST /search/metadata`` — find an asset by original filename and date
- ``PUT  /tags`` — resolve or create tags by full path, in one call
- ``PUT  /tags/assets`` — attach tags to assets in bulk
- ``PUT  /assets`` — set ``isFavorite``
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

#: The immich release this client was written against. Printed by the command
#: so a mismatch is visible before it becomes a confusing 404.
TESTED_IMMICH_VERSION = "3.2.0"

#: Environment variable for the API key. Preferred over the flag: an argument
#: is visible to every other user in `ps aux` on a shared machine, and an
#: immich key is a credential for someone's whole photo library.
API_KEY_ENV = "IMMICH_API_KEY"

#: How far apart two capture timestamps may be and still be the same photo.
#: One minute absorbs timezone-naive EXIF and immich's own normalisation
#: without being loose enough to collide with a burst.
MATCH_WINDOW = timedelta(minutes=1)

_TIMEOUT = 30
_PAGE_SIZE = 250


class ImmichError(RuntimeError):
    """The server refused a request, or could not be reached."""


@dataclass
class MatchResult:
    """What the matcher made of one local photo.

    ``file_name`` is what the match was made on; ``file_path`` is filled in by
    the caller, which is the only place that knows where the file lives.
    """

    file_name: str
    asset_id: str | None = None
    reason: str = ""
    file_path: str = ""

    @property
    def matched(self) -> bool:
        """True when this photo was resolved to exactly one immich asset."""
        return self.asset_id is not None


@dataclass
class SyncPlan:
    """What a sync would do, before it does any of it."""

    matched: list[MatchResult] = field(default_factory=list)
    unmatched: list[MatchResult] = field(default_factory=list)
    tags_by_asset: dict[str, list[str]] = field(default_factory=dict)
    favorites: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One line for the terminal."""
        tag_count = sum(len(v) for v in self.tags_by_asset.values())
        return (
            f"{len(self.matched)} matched, {len(self.unmatched)} unmatched, "
            f"{tag_count} tag(s) to push, {len(self.favorites)} favourite(s)"
        )


class ImmichClient:
    """A very small slice of the immich API."""

    def __init__(self, url: str, api_key: str, *, session: Any = None) -> None:
        """Bind to a server. *session* is injectable so tests need no network."""
        self.base_url = url.rstrip("/")
        if not self.base_url.endswith("/api"):
            self.base_url += "/api"
        self._session = session or requests.Session()
        self._session.headers.update({"x-api-key": api_key, "Accept": "application/json"})

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self._session.request(method, url, json=payload, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise ImmichError(f"{method} {path} failed: {exc}") from exc
        if response.status_code >= 400:
            # The body often names the problem ("invalid api key"); the status
            # alone sends people to the wrong place.
            detail = (response.text or "").strip()[:200]
            raise ImmichError(f"{method} {path} returned {response.status_code}: {detail}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ImmichError(f"{method} {path} returned unreadable JSON: {exc}") from exc

    # --- reads ------------------------------------------------------------

    def search_by_filename(self, filename: str, taken_at: datetime | None) -> list[dict]:
        """Assets whose original filename matches, narrowed by capture time.

        The date narrows the query server-side rather than filtering a large
        page client-side, which matters on a library where one filename
        (``IMG_0001.JPG``) can legitimately appear dozens of times.

        Uses the nested ``filter`` object rather than the flat
        ``originalFileName`` / ``takenAfter`` / ``takenBefore`` fields: those
        are all marked deprecated in the 3.2.0 spec, which is the same release
        that added ``filter``. Writing the deprecated form against the version
        that deprecated it would be born stale. The cost is that servers older
        than 3.2.0 do not understand ``filter`` at all -- see the README.
        """
        search_filter: dict[str, Any] = {"originalFileName": {"eq": filename}}
        if taken_at is not None:
            search_filter["takenAt"] = {
                "gte": _isoformat(taken_at - MATCH_WINDOW),
                "lte": _isoformat(taken_at + MATCH_WINDOW),
            }
        payload: dict[str, Any] = {"filter": search_filter, "size": _PAGE_SIZE}
        data = self._request("POST", "/search/metadata", payload) or {}
        return list((data.get("assets") or {}).get("items") or [])

    def list_tags(self) -> list[dict]:
        """Every tag on the server."""
        return list(self._request("GET", "/tags") or [])

    # --- writes -----------------------------------------------------------

    def upsert_tags(self, names: list[str]) -> dict[str, str]:
        """Resolve tag names to ids, creating whatever is missing.

        ``PUT /tags`` rather than ``POST /tags``: the create endpoint's
        ``name`` is constrained to ``^[^/]*$``, so it cannot express a nested
        tag at all, while upsert takes full slash-delimited paths and returns
        each tag with its ``value`` (documented as the full path). Upsert also
        resolves names that already exist, which is what keeps a re-sync from
        growing a second copy of every tag.

        Returns:
            Full tag path -> immich tag id, for the names the server resolved.
        """
        if not names:
            return {}
        rows = self._request("PUT", "/tags", {"tags": sorted(names)}) or []
        return {str(r["value"]): str(r["id"]) for r in rows if r.get("id") and r.get("value")}

    def tag_assets(self, tag_ids: list[str], asset_ids: list[str]) -> None:
        """Attach tags to assets in bulk.

        ``PUT /tags/assets`` is set semantics on immich's side, so running
        this twice with the same arguments leaves the same state -- which is
        what makes re-syncing idempotent without reading anything back first.
        """
        if not tag_ids or not asset_ids:
            return
        self._request("PUT", "/tags/assets", {"tagIds": tag_ids, "assetIds": asset_ids})

    def set_favorite(self, asset_ids: list[str], favorite: bool = True) -> None:
        """Mark assets as favourites."""
        if not asset_ids:
            return
        self._request("PUT", "/assets", {"ids": asset_ids, "isFavorite": favorite})


def _isoformat(value: datetime) -> str:
    """UTC ISO-8601 with a ``Z``, which is what the spec's date-time wants."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_api_key(explicit: str | None) -> str:
    """The API key, from the flag or the environment.

    Raises:
        ImmichError: Neither source provided one.
    """
    key = explicit or os.environ.get(API_KEY_ENV)
    if not key:
        raise ImmichError(
            f"No API key. Set {API_KEY_ENV} in the environment (preferred -- a "
            f"--api-key argument is visible in `ps aux` to every other user on "
            f"the machine) or pass --api-key."
        )
    return key


def match_asset(file_name: str, taken_at: datetime | None, candidates: list[dict]) -> MatchResult:
    """Resolve one local photo against immich search results.

    **Both** the filename and the capture date must agree, and exactly one
    candidate must survive. A filename alone is not evidence: every phone in
    the world produces ``IMG_0001.JPG``, and tagging the wrong person's photo
    is worse than tagging nothing. A photo with no capture date is therefore
    never matched, however unambiguous its name looks.
    """
    if not candidates:
        return MatchResult(file_name, reason="no asset with that filename")
    if taken_at is None:
        return MatchResult(
            file_name, reason="no capture date locally; filename alone is not enough"
        )

    viable = []
    for asset in candidates:
        if str(asset.get("originalFileName") or "") != file_name:
            continue
        remote = _parse_iso(asset.get("localDateTime") or asset.get("fileCreatedAt"))
        if remote is None:
            continue
        if abs(_naive(remote) - _naive(taken_at)) <= MATCH_WINDOW:
            viable.append(asset)

    if not viable:
        return MatchResult(file_name, reason="filename matched but capture date did not")
    if len(viable) > 1:
        # Ambiguity is reported, never resolved by picking one: a burst of
        # identically named frames would otherwise get a coin toss.
        return MatchResult(file_name, reason=f"{len(viable)} assets matched; ambiguous")
    return MatchResult(file_name, asset_id=str(viable[0].get("id")))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _naive(value: datetime) -> datetime:
    """Drop the timezone so a naive EXIF date and an aware API date compare."""
    return value.replace(tzinfo=None)
