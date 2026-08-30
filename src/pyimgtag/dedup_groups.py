"""Library-wide duplicate / burst grouping and best-pick ranking.

Two independent pieces live here, both pure functions over plain data so
they can be unit-tested without touching SQLite or the filesystem:

**Grouping** (:func:`group_by_phash`) turns ``(file_path, phash)`` records
into connected components of visually similar photos. A naive pairwise
sweep is O(n²) — 100 k rows would be five billion comparisons — so
candidates are found with *multi-index hashing* instead: the 64-bit hash is
split into ``b`` bands (chosen per library size and threshold by a small cost
model), each indexed in its own bucket table. Pigeonhole guarantees recall: a
pair within ``threshold`` bits differs in at most ``threshold`` bit positions
spread over ``b`` bands, so at least one band differs by at most
``threshold // b`` bits — and every bucket within that radius of each band
value is probed. Every candidate pair is still verified
with a real Hamming distance, so there are no false positives either.
Matches are merged with union-find, which gives the transitive closure for
free.

**Ranking** (:func:`rank_candidates`) orders the members of a group so the
first one is the copy worth keeping. The default order is documented in
:data:`DEFAULT_PREFER` and every criterion is a separate, individually
testable key function; ``--prefer`` reorders them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from math import comb
from pathlib import Path, PurePath

#: Default Hamming distance for ``pyimgtag dedup scan`` (matches ``run --dedup-threshold``).
DEFAULT_THRESHOLD = 5

#: Groups whose members are all within this distance are exact-ish duplicates;
#: anything looser is a burst (a sequence of similar but distinct frames).
DUPLICATE_MAX_DISTANCE = 5

KIND_DUPLICATE = "duplicate"
KIND_BURST = "burst"

# Computing the max pairwise distance of a group is O(m²) in its distinct
# hashes. Groups bigger than this are labelled from the scan threshold alone
# rather than paying for the full matrix.
_MAX_PAIRWISE_HASHES = 64

#: RAW extensions preferred over every rendered format when picking the keeper.
RAW_EXTENSIONS: frozenset[str] = frozenset({"cr2", "nef", "arw", "dng", "raf", "orf", "rw2"})

# Lower rank == more preferred.
_FORMAT_RANK: dict[str, int] = {
    **{ext: 0 for ext in RAW_EXTENSIONS},
    "heic": 1,
    "heif": 1,
    "jpg": 2,
    "jpeg": 2,
    "tif": 3,
    "tiff": 3,
    "png": 4,
}
_FORMAT_FALLBACK_RANK = 5

#: Ranking criteria, in their default priority order.
DEFAULT_PREFER: tuple[str, ...] = ("score", "resolution", "size", "format", "mtime")

#: Alias kept for readability at call sites that validate user input.
CRITERIA: tuple[str, ...] = DEFAULT_PREFER


@dataclass(frozen=True)
class GroupCandidate:
    """One connected component produced by :func:`group_by_phash`."""

    kind: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class PhotoCandidate:
    """The facts :func:`rank_candidates` needs about one photo in a group."""

    file_path: str
    judge_score: float | None = None
    width: int | None = None
    height: int | None = None
    file_size: int | None = None
    file_mtime: float | None = None


def format_rank(file_path: str) -> int:
    """Return the format-preference rank of *file_path* (lower is better).

    RAW (0) beats HEIC/HEIF (1) beats JPEG (2) beats TIFF (3) beats PNG (4);
    anything else ranks last.
    """
    ext = PurePath(file_path).suffix.lstrip(".").lower()
    return _FORMAT_RANK.get(ext, _FORMAT_FALLBACK_RANK)


# Each key function returns a value where *smaller is better*, so a plain
# ascending sort over the tuple of active criteria yields the ranking.
_CRITERION_KEYS: dict[str, Callable[[PhotoCandidate], float]] = {
    # A missing judge score sorts as -1, i.e. below every real 1-10 score.
    "score": lambda c: -(c.judge_score if c.judge_score is not None else -1.0),
    "resolution": lambda c: -float((c.width or 0) * (c.height or 0)),
    "size": lambda c: -float(c.file_size or 0),
    "format": lambda c: float(format_rank(c.file_path)),
    # Oldest capture/modification time wins.
    "mtime": lambda c: c.file_mtime if c.file_mtime is not None else float("inf"),
}


def parse_prefer(value: str | None) -> tuple[str, ...]:
    """Parse a ``--prefer`` comma list into a full criteria order.

    The named criteria take priority in the order given; every criterion the
    caller did not name is appended afterwards in its default order, so the
    ranking is always total.

    Args:
        value: Comma-separated criteria names, or ``None``/empty for the default.

    Returns:
        The full criteria order, always a permutation of :data:`DEFAULT_PREFER`.

    Raises:
        ValueError: If a name is unknown or repeated.
    """
    if not value or not value.strip():
        return DEFAULT_PREFER
    names = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not names:
        raise ValueError("--prefer needs at least one criterion")
    seen: set[str] = set()
    for name in names:
        if name not in CRITERIA:
            raise ValueError(
                f"unknown ranking criterion {name!r} (choose from: {', '.join(CRITERIA)})"
            )
        if name in seen:
            raise ValueError(f"duplicate ranking criterion {name!r}")
        seen.add(name)
    return tuple(names) + tuple(c for c in DEFAULT_PREFER if c not in seen)


def _order(prefer: Sequence[str] | None) -> tuple[str, ...]:
    if prefer is None:
        return DEFAULT_PREFER
    order = tuple(prefer)
    unknown = [name for name in order if name not in _CRITERION_KEYS]
    if unknown:
        raise ValueError(f"unknown ranking criterion {unknown[0]!r}")
    return order


def rank_candidates(
    candidates: Iterable[PhotoCandidate],
    prefer: Sequence[str] | None = None,
) -> list[PhotoCandidate]:
    """Return *candidates* ordered best-first.

    Args:
        candidates: Photos belonging to one duplicate group.
        prefer: Criteria order; defaults to :data:`DEFAULT_PREFER`. Use
            :func:`parse_prefer` to build one from a ``--prefer`` string.

    Returns:
        A new list, best pick first. The file path is the final tie-break, so
        the order is fully deterministic.

    Raises:
        ValueError: If *prefer* names an unknown criterion.
    """
    order = _order(prefer)
    keys = [_CRITERION_KEYS[name] for name in order]
    return sorted(
        candidates,
        key=lambda c: (tuple(key(c) for key in keys), c.file_path),
    )


def best_pick(
    candidates: Iterable[PhotoCandidate],
    prefer: Sequence[str] | None = None,
) -> PhotoCandidate | None:
    """Return the copy worth keeping, or ``None`` for an empty group."""
    ranked = rank_candidates(candidates, prefer)
    return ranked[0] if ranked else None


def best_pick_reasons(
    candidates: Iterable[PhotoCandidate],
    prefer: Sequence[str] | None = None,
) -> list[str]:
    """Return the criteria on which the best pick beats *every* other member.

    These are the badges the ``/dedup`` page shows next to the highlighted
    keeper ("resolution", "size", …). A criterion where the winner merely ties
    is not a reason, so a group of identical copies yields an empty list.
    """
    order = _order(prefer)
    ranked = rank_candidates(candidates, order)
    if len(ranked) < 2:
        return []
    winner, others = ranked[0], ranked[1:]
    return [
        name
        for name in order
        if all(_CRITERION_KEYS[name](winner) < _CRITERION_KEYS[name](other) for other in others)
    ]


def is_photos_library_path(file_path: str) -> bool:
    """Return True if *file_path* points inside an Apple Photos library bundle.

    Originals managed by Photos are never touched on disk — moving or trashing
    one corrupts the library — so the resolve flow only ever records a keyword
    action for these rows.
    """
    return ".photoslibrary" in file_path.replace("\\", "/").lower()


def quarantine_destination(file_path: str, dest_dir: str) -> Path:
    """Map a source path into the quarantine directory, preserving structure.

    ``/a/b/c.jpg`` with ``dest_dir=/q`` becomes ``/q/a/b/c.jpg``; the
    filesystem root (or the Windows drive) is dropped so the result stays
    inside *dest_dir*.
    """
    src = Path(file_path)
    # ``anchor`` covers "/" on POSIX and "C:\\", "\\" or "\\\\server\\share\\" on
    # Windows; dropping it is what keeps the result inside *dest_dir*.
    rel = src.relative_to(src.anchor) if src.anchor else src
    return Path(dest_dir).expanduser().joinpath(*rel.parts)


def candidates_from_members(members: Sequence[dict]) -> list[PhotoCandidate]:
    """Build :class:`PhotoCandidate` objects from ``DedupDB.list_groups`` members."""
    return [
        PhotoCandidate(
            file_path=m["file_path"],
            judge_score=m.get("judge_score"),
            width=m.get("width"),
            height=m.get("height"),
            file_size=m.get("file_size"),
            file_mtime=m.get("file_mtime"),
        )
        for m in members
    ]


def summarize_group(group: dict, prefer: Sequence[str] | None = None) -> dict:
    """Return a group dict enriched with the ranking decision.

    Adds ``best_path``, ``best_reasons``, ``reclaimable_bytes`` (the total size
    of every copy except the best pick) and re-orders ``members`` best-first,
    flagging each with ``is_best``. The stored ``keep_path`` of an already
    resolved group wins over the freshly computed ranking so the UI shows what
    actually happened.
    """
    order = _order(prefer)
    ranked = rank_candidates(candidates_from_members(group.get("members", [])), order)
    by_path = {m["file_path"]: m for m in group.get("members", [])}
    best_path = group.get("keep_path") or (ranked[0].file_path if ranked else None)
    reasons = best_pick_reasons(ranked, order) if not group.get("keep_path") else []
    members = []
    for candidate in ranked:
        member = dict(by_path[candidate.file_path])
        member["is_best"] = candidate.file_path == best_path
        member["photos_library"] = is_photos_library_path(candidate.file_path)
        members.append(member)
    reclaimable = sum(int(m.get("file_size") or 0) for m in members if not m["is_best"])
    enriched = dict(group)
    enriched["members"] = members
    enriched["count"] = len(members)
    enriched["best_path"] = best_path
    enriched["best_reasons"] = reasons
    enriched["reclaimable_bytes"] = reclaimable
    return enriched


class _UnionFind:
    """Minimal union-find with path halving and union by size."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self._size = [1] * size

    def find(self, item: int) -> int:
        parent = self._parent
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._size[ra] < self._size[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        self._size[ra] += self._size[rb]


# Largest per-band search radius we are willing to enumerate (C(w, 3) probes).
_MAX_RADIUS = 3


def _plan_bands(bits: int, threshold: int, count: int) -> tuple[int, int]:
    """Pick ``(bands, radius)`` minimising the expected probe + candidate work.

    With ``b`` bands of ``w = bits // b`` bits, pigeonhole means two hashes
    within ``threshold`` bits agree on some band to within ``r = threshold // b``
    bits. Fewer, wider bands mean sparser buckets (fewer false candidates) but
    more probes per band (``sum C(w, k) for k <= r``); the cost model below
    balances the two for the library size at hand.
    """
    best: tuple[float, int, int] | None = None
    for bands in range(1, min(8, bits) + 1):
        width = bits // bands
        radius = threshold // bands
        if radius > _MAX_RADIUS:
            continue
        probes = sum(comb(width, k) for k in range(radius + 1))
        density = count / float(1 << width)
        cost = bands * probes * (1.0 + density)
        if best is None or cost < best[0]:
            best = (cost, bands, radius)
    if best is None:  # pragma: no cover - bands=8 always has radius <= 3 for threshold <= 31
        raise ValueError(f"threshold {threshold} too large for {bits}-bit hashes")
    return best[1], best[2]


def _flip_masks(width: int, radius: int) -> list[list[int]]:
    """Return XOR masks for exactly 1, 2, … ``radius`` bit flips in ``width`` bits."""
    singles = [1 << i for i in range(width)]
    out: list[list[int]] = []
    if radius >= 1:
        out.append(singles)
    if radius >= 2:
        out.append([singles[i] | singles[j] for i in range(width) for j in range(i + 1, width)])
    if radius >= 3:
        out.append(
            [
                singles[i] | singles[j] | singles[k]
                for i in range(width)
                for j in range(i + 1, width)
                for k in range(j + 1, width)
            ]
        )
    return out


def _candidate_pairs(
    values: Sequence[int],
    bits: int,
    indices: Sequence[int],
    threshold: int,
) -> Iterable[tuple[int, int]]:
    """Yield candidate index pairs via multi-index hashing.

    The ``bits``-wide value is split into bands (see :func:`_plan_bands`),
    each indexed in its own bucket table. Hashes are inserted in order and
    each one is first probed against everything inserted before it — every
    band value within the per-band radius is looked up — so each true pair is
    found at least once and the caller verifies it with the exact Hamming
    distance.
    """
    bands, radius = _plan_bands(bits, threshold, len(indices))
    widths = [(bits // bands) + (1 if b < bits % bands else 0) for b in range(bands)]
    shifts = [sum(widths[:b]) for b in range(bands)]
    masks = [(1 << w) - 1 for w in widths]
    flips = [_flip_masks(widths[b], radius) for b in range(bands)]
    buckets: list[dict[int, list[int]]] = [{} for _ in range(bands)]

    for idx in indices:
        value = values[idx]
        band_vals = [(value >> shifts[b]) & masks[b] for b in range(bands)]
        for b in range(bands):
            bucket = buckets[b]
            band_value = band_vals[b]
            get = bucket.get
            hits = get(band_value)
            if hits:
                for other in hits:
                    yield other, idx
            for level in flips[b]:
                for mask in level:
                    hits = get(band_value ^ mask)
                    if hits:
                        for other in hits:
                            yield other, idx
        for b in range(bands):
            buckets[b].setdefault(band_vals[b], []).append(idx)


def _kind_for(values: Sequence[int], threshold: int) -> str:
    """Classify a component as a duplicate set or a burst."""
    if threshold <= DUPLICATE_MAX_DISTANCE:
        return KIND_DUPLICATE
    if len(values) > _MAX_PAIRWISE_HASHES:
        # Too big to measure exactly; the loose scan threshold is the only
        # signal we have, and it is above the duplicate ceiling.
        return KIND_BURST
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            if (values[i] ^ values[j]).bit_count() > DUPLICATE_MAX_DISTANCE:
                return KIND_BURST
    return KIND_DUPLICATE


def group_by_phash(
    records: Iterable[tuple[str, str | None]],
    threshold: int = DEFAULT_THRESHOLD,
) -> list[GroupCandidate]:
    """Group photos whose perceptual hashes are within *threshold* bits.

    Args:
        records: ``(file_path, phash_hex)`` pairs. Rows with a missing or
            non-hex hash are ignored.
        threshold: Maximum Hamming distance for two photos to be linked.
            Grouping is the transitive closure of that relation.

    Returns:
        Groups of two or more paths, each tagged ``duplicate`` or ``burst``.
        Paths inside a group and the groups themselves are sorted, so the
        result is stable across runs.
    """
    threshold = max(0, int(threshold))
    by_hash: dict[str, list[str]] = {}
    for file_path, phash in records:
        if not phash:
            continue
        key = phash.strip().lower()
        try:
            int(key, 16)
        except ValueError:
            continue
        by_hash.setdefault(key, []).append(file_path)
    if not by_hash:
        return []

    hashes = sorted(by_hash)
    values = [int(h, 16) for h in hashes]
    uf = _UnionFind(len(hashes))

    # Hashes of different hex widths are not comparable (imagehash refuses to
    # subtract differently shaped hashes), so band each width separately.
    by_width: dict[int, list[int]] = {}
    for idx, value in enumerate(hashes):
        by_width.setdefault(len(value), []).append(idx)

    for hex_width, indices in by_width.items():
        if len(indices) < 2:
            continue
        for a, b in _candidate_pairs(values, hex_width * 4, indices, threshold):
            if uf.find(a) == uf.find(b):
                continue
            if (values[a] ^ values[b]).bit_count() <= threshold:
                uf.union(a, b)

    components: dict[int, list[int]] = {}
    for idx in range(len(hashes)):
        components.setdefault(uf.find(idx), []).append(idx)

    groups: list[GroupCandidate] = []
    for member_indices in components.values():
        paths = sorted(path for idx in member_indices for path in by_hash[hashes[idx]])
        if len(paths) < 2:
            continue
        kind = _kind_for([values[idx] for idx in member_indices], threshold)
        groups.append(GroupCandidate(kind=kind, paths=tuple(paths)))
    groups.sort(key=lambda g: g.paths[0])
    return groups
