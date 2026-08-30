"""Tests for the phash grouping + best-pick ranking engine."""

from __future__ import annotations

import random
import time
from pathlib import Path

import pytest

from pyimgtag.dedup_groups import (
    DEFAULT_PREFER,
    KIND_BURST,
    KIND_DUPLICATE,
    PhotoCandidate,
    best_pick,
    best_pick_reasons,
    format_rank,
    group_by_phash,
    is_photos_library_path,
    parse_prefer,
    quarantine_destination,
    rank_candidates,
    summarize_group,
)


def _hex(value: int) -> str:
    """Render a 64-bit int as the 16-char hex string imagehash uses."""
    return f"{value & ((1 << 64) - 1):016x}"


def _flip(value: int, bits: int) -> int:
    """Flip the lowest *bits* bits of *value*."""
    mask = (1 << bits) - 1
    return value ^ mask


BASE = 0xF0F0F0F0F0F0F0F0


# --- grouping --------------------------------------------------------------


def test_no_groups_below_two_records():
    assert group_by_phash([], 5) == []
    assert group_by_phash([("a.jpg", _hex(BASE))], 5) == []


def test_threshold_zero_groups_only_exact_matches():
    records = [
        ("a.jpg", _hex(BASE)),
        ("b.jpg", _hex(BASE)),
        ("c.jpg", _hex(_flip(BASE, 1))),
    ]
    groups = group_by_phash(records, 0)
    assert [g.paths for g in groups] == [("a.jpg", "b.jpg")]
    assert groups[0].kind == KIND_DUPLICATE


# a-b = 4 bits, a-c = 9 bits (disjoint bit ranges), b-c = 13 bits.
_NEAR_RECORDS = [
    ("a.jpg", _hex(BASE)),
    ("b.jpg", _hex(BASE ^ 0xF)),
    ("c.jpg", _hex(BASE ^ (0x1FF << 24))),
]


def test_threshold_five_groups_near_duplicates():
    groups = group_by_phash(_NEAR_RECORDS, 5)
    assert [g.paths for g in groups] == [("a.jpg", "b.jpg")]


def test_threshold_ten_widens_the_group_and_flags_a_burst():
    groups = group_by_phash(_NEAR_RECORDS, 10)
    assert [g.paths for g in groups] == [("a.jpg", "b.jpg", "c.jpg")]
    assert groups[0].kind == KIND_BURST


def test_transitive_chain_is_one_group():
    # a-b = 3, b-c = 3, a-c = 6: a and c only meet through b.
    a = BASE
    b = a ^ 0b111
    c = b ^ 0b111000
    groups = group_by_phash([("a.jpg", _hex(a)), ("b.jpg", _hex(b)), ("c.jpg", _hex(c))], 3)
    assert [g.paths for g in groups] == [("a.jpg", "b.jpg", "c.jpg")]


def test_tight_group_at_loose_threshold_stays_a_duplicate():
    records = [("a.jpg", _hex(BASE)), ("b.jpg", _hex(_flip(BASE, 2)))]
    groups = group_by_phash(records, 10)
    assert groups[0].kind == KIND_DUPLICATE


def test_identical_hashes_on_many_paths_group_together():
    records = [(f"{i}.jpg", _hex(BASE)) for i in range(5)]
    groups = group_by_phash(records, 5)
    assert len(groups) == 1
    assert len(groups[0].paths) == 5


def test_invalid_and_missing_hashes_are_ignored():
    records = [
        ("a.jpg", _hex(BASE)),
        ("b.jpg", None),
        ("c.jpg", ""),
        ("d.jpg", "not-hex"),
    ]
    assert group_by_phash(records, 5) == []


def test_hashes_of_different_widths_never_group():
    records = [("a.jpg", "ffff"), ("b.jpg", "ffffffffffffffff")]
    assert group_by_phash(records, 5) == []


def _brute_force_groups(records, threshold):
    """Reference implementation: every pair, no index."""
    paths = [p for p, _ in records]
    values = [int(h, 16) for _, h in records]
    parent = list(range(len(paths)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            if (values[i] ^ values[j]).bit_count() <= threshold:
                parent[find(i)] = find(j)
    comps: dict[int, list[str]] = {}
    for i, path in enumerate(paths):
        comps.setdefault(find(i), []).append(path)
    return sorted(tuple(sorted(v)) for v in comps.values() if len(v) > 1)


@pytest.mark.parametrize("threshold", [0, 3, 5, 8, 10])
def test_banding_recall_matches_brute_force(threshold):
    """The banding index must never miss a pair the brute-force sweep finds."""
    rng = random.Random(1234 + threshold)
    records = []
    for i in range(120):
        base = rng.getrandbits(64)
        records.append((f"base{i}.jpg", _hex(base)))
        # A near-duplicate within the threshold and one just outside it.
        near = base
        for bit in rng.sample(range(64), max(threshold, 1)):
            near ^= 1 << bit
        records.append((f"near{i}.jpg", _hex(near)))
    expected = _brute_force_groups(records, threshold)
    actual = sorted(g.paths for g in group_by_phash(records, threshold))
    assert actual == expected


@pytest.mark.slow
def test_groups_100k_hashes_under_30_seconds():
    rng = random.Random(7)
    records = [(f"/photos/{i}.jpg", _hex(rng.getrandbits(64))) for i in range(100_000)]
    started = time.perf_counter()
    groups = group_by_phash(records, 5)
    elapsed = time.perf_counter() - started
    assert elapsed < 30.0, f"grouping 100k hashes took {elapsed:.1f}s"
    # Random 64-bit hashes essentially never collide within 5 bits.
    assert isinstance(groups, list)


# --- ranking ---------------------------------------------------------------


def _cand(path="/a/x.jpg", **kw):
    return PhotoCandidate(file_path=path, **kw)


@pytest.mark.parametrize(
    ("winner", "loser", "criterion"),
    [
        (_cand("/a/1.jpg", judge_score=9), _cand("/a/2.jpg", judge_score=4), "score"),
        (_cand("/a/1.jpg", judge_score=1), _cand("/a/2.jpg"), "score"),
        (
            _cand("/a/1.jpg", width=4000, height=3000),
            _cand("/a/2.jpg", width=800, height=600),
            "resolution",
        ),
        (_cand("/a/1.jpg", file_size=900), _cand("/a/2.jpg", file_size=100), "size"),
        (_cand("/a/1.cr2"), _cand("/a/2.jpg"), "format"),
        (_cand("/a/1.heic"), _cand("/a/2.jpg"), "format"),
        (_cand("/a/1.jpg"), _cand("/a/2.tiff"), "format"),
        (_cand("/a/1.tiff"), _cand("/a/2.png"), "format"),
        (_cand("/a/1.png"), _cand("/a/2.xyz"), "format"),
        (_cand("/a/1.jpg", file_mtime=10.0), _cand("/a/2.jpg", file_mtime=99.0), "mtime"),
    ],
)
def test_each_ranking_rule_picks_the_expected_winner(winner, loser, criterion):
    assert best_pick([loser, winner]).file_path == winner.file_path
    assert criterion in best_pick_reasons([loser, winner])


def test_ranking_order_is_score_then_resolution_then_size():
    small_high_score = _cand("/a/1.jpg", judge_score=9, width=100, height=100, file_size=1)
    huge_low_score = _cand("/a/2.jpg", judge_score=2, width=9000, height=9000, file_size=99)
    assert best_pick([huge_low_score, small_high_score]).file_path == "/a/1.jpg"


def test_path_is_the_final_tie_break():
    ranked = rank_candidates([_cand("/a/z.jpg"), _cand("/a/a.jpg")])
    assert [c.file_path for c in ranked] == ["/a/a.jpg", "/a/z.jpg"]


def test_best_pick_of_empty_group_is_none():
    assert best_pick([]) is None
    assert best_pick_reasons([]) == []


def test_identical_copies_have_no_reason_badges():
    a = _cand("/a/1.jpg", judge_score=5, width=10, height=10, file_size=5, file_mtime=1.0)
    b = _cand("/a/2.jpg", judge_score=5, width=10, height=10, file_size=5, file_mtime=1.0)
    assert best_pick_reasons([a, b]) == []


def test_format_rank_ordering():
    assert format_rank("/a/x.DNG") < format_rank("/a/x.heic")
    assert format_rank("/a/x.heic") < format_rank("/a/x.jpeg")
    assert format_rank("/a/x.jpeg") < format_rank("/a/x.tif")
    assert format_rank("/a/x.tif") < format_rank("/a/x.png")
    assert format_rank("/a/x.png") < format_rank("/a/x")


# --- --prefer --------------------------------------------------------------


def test_parse_prefer_default_is_the_documented_order():
    assert parse_prefer(None) == DEFAULT_PREFER
    assert parse_prefer("  ") == DEFAULT_PREFER


def test_parse_prefer_appends_unlisted_criteria():
    assert parse_prefer("size") == ("size", "score", "resolution", "format", "mtime")
    assert parse_prefer("mtime, format") == ("mtime", "format", "score", "resolution", "size")


@pytest.mark.parametrize("value", ["bogus", "score,bogus", "score,score"])
def test_parse_prefer_rejects_bad_input(value):
    with pytest.raises(ValueError):
        parse_prefer(value)


def test_prefer_reordering_changes_the_winner():
    big_low_score = _cand("/a/big.jpg", judge_score=3, width=4000, height=3000)
    small_high_score = _cand("/a/small.jpg", judge_score=9, width=100, height=100)
    group = [big_low_score, small_high_score]
    assert best_pick(group).file_path == "/a/small.jpg"
    assert best_pick(group, parse_prefer("resolution")).file_path == "/a/big.jpg"


def test_rank_candidates_rejects_unknown_criterion():
    with pytest.raises(ValueError):
        rank_candidates([_cand()], ["nope"])


# --- helpers ---------------------------------------------------------------


def test_quarantine_destination_preserves_structure(tmp_path):
    dest = quarantine_destination(str(Path("/a/b/c.jpg")), str(tmp_path))
    assert dest == tmp_path / "a" / "b" / "c.jpg"


def test_quarantine_destination_handles_relative_paths(tmp_path):
    dest = quarantine_destination(str(Path("b/c.jpg")), str(tmp_path))
    assert dest == tmp_path / "b" / "c.jpg"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/Users/x/Pictures/Photos Library.photoslibrary/originals/1/a.jpg", True),
        (r"C:\Users\x\Photos.photoslibrary\originals\a.jpg", True),
        ("/Users/x/Pictures/export/a.jpg", False),
    ],
)
def test_is_photos_library_path(path, expected):
    assert is_photos_library_path(path) is expected


def test_summarize_group_enriches_the_plan():
    group = {
        "id": 3,
        "kind": KIND_DUPLICATE,
        "resolved_at": None,
        "keep_path": None,
        "members": [
            {"file_path": "/a/small.jpg", "file_size": 100, "width": 10, "height": 10},
            {"file_path": "/a/big.jpg", "file_size": 900, "width": 90, "height": 90},
        ],
    }
    out = summarize_group(group)
    assert out["best_path"] == "/a/big.jpg"
    assert out["count"] == 2
    assert out["reclaimable_bytes"] == 100
    assert out["members"][0]["is_best"] is True
    assert out["members"][1]["is_best"] is False
    assert "resolution" in out["best_reasons"]


def test_summarize_group_honours_a_stored_keep_path():
    group = {
        "id": 4,
        "kind": KIND_DUPLICATE,
        "resolved_at": "2026-01-01T00:00:00+00:00",
        "keep_path": "/a/small.jpg",
        "members": [
            {"file_path": "/a/small.jpg", "file_size": 100},
            {"file_path": "/a/big.jpg", "file_size": 900},
        ],
    }
    out = summarize_group(group)
    assert out["best_path"] == "/a/small.jpg"
    assert out["reclaimable_bytes"] == 900
    assert out["best_reasons"] == []
