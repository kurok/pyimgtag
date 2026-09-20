"""The keyword taxonomy shared by hierarchical XMP tags and the digiKam export.

One place owns what the tree looks like, because two features depend on them
agreeing: a library tagged with ``--hierarchical-keywords`` and the same
library exported with ``pyimgtag export --format digikam`` must land in the
same branches, or the import produces two parallel trees saying the same thing.

``Places`` nests country → region → city. That is the only order in which the
branches merge usefully: a hundred photos from one country share one node
rather than becoming a hundred siblings.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Top-level branches. Singular-to-plural matches what Lightroom and digiKam
#: users already have (``People|Alice``, not ``Person|Alice``).
PEOPLE = "People"
PLACES = "Places"
TAGS = "Tags"
EVENTS = "Events"

#: Separator for a hierarchical keyword path. Lightroom's ``HierarchicalSubject``
#: and digiKam's ``TagsList`` both use it.
SEPARATOR = "|"


def join_path(*parts: str | None) -> str | None:
    """Join non-empty *parts* into one ``A|B|C`` path, or None when empty.

    Empty segments are dropped rather than preserved: a photo geocoded to a
    country but no region must produce ``Places|Spain|Madrid``, never
    ``Places|Spain||Madrid``, which would import as a nameless tag.
    """
    cleaned = [str(p).strip() for p in parts if p is not None and str(p).strip()]
    return SEPARATOR.join(cleaned) if cleaned else None


def keyword_paths(
    tags: Iterable[str] | None = None,
    *,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    people: Iterable[str] | None = None,
    event: str | None = None,
    vocabulary_paths: dict[str, str] | None = None,
) -> list[str]:
    """Build the hierarchical keyword paths for one photo.

    *vocabulary_paths* maps a tag to its controlled-vocabulary path
    (``"beach" -> "Nature|Coast|beach"``); a tag without an entry becomes a
    direct child of ``Tags``.

    Returns them sorted and de-duplicated, so writing the same photo twice
    produces byte-identical metadata and a re-run is a no-op.
    """
    paths: set[str] = set()

    for tag in tags or ():
        if not str(tag).strip():
            continue
        mapped = (vocabulary_paths or {}).get(tag)
        path = join_path(TAGS, *(mapped.split(SEPARATOR) if mapped else [tag]))
        if path:
            paths.add(path)

    place = join_path(PLACES, country, region, city)
    if place and place != PLACES:
        paths.add(place)

    for person in people or ():
        path = join_path(PEOPLE, person)
        if path and path != PEOPLE:
            paths.add(path)

    event_path = join_path(EVENTS, event)
    if event_path and event_path != EVENTS:
        paths.add(event_path)

    return sorted(paths)


def tree_from_paths(paths: Iterable[str]) -> dict:
    """Fold ``A|B|C`` paths into a nested ``{name: {child: {}}}`` tree."""
    tree: dict = {}
    for path in paths:
        node = tree
        for part in path.split(SEPARATOR):
            if not part:
                continue
            node = node.setdefault(part, {})
    return tree


def stars_from_score(score: int | float | None) -> int | None:
    """Map a 1-10 judge score onto the XMP standard's 1-5 stars.

    Each star covers exactly two points -- 1-2 → ★, 3-4 → ★★, 5-6 → ★★★,
    7-8 → ★★★★, 9-10 → ★★★★★ -- which is monotonic and easy to state.

    Deliberately not ``round(score / 2)``: Python rounds halves to even, so a
    5 would become 2 stars while a 7 became 4, and the mapping would have a
    kink in it nobody could predict from the outside.

    Returns None when there is no score, so a photo the judge never saw is
    left without a rating rather than given one star.
    """
    if score is None:
        return None
    import math

    return max(1, min(5, math.ceil(float(score) / 2)))
