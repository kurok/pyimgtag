"""Tests for :mod:`pyimgtag.vocabulary` (loading, validation, canonicalization)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyimgtag.vocabulary import (
    MappingStats,
    Vocabulary,
    VocabularyError,
    load_vocabulary,
    normalize_tag,
)

SAMPLE = {
    "tags": [
        {"beach": {"synonyms": ["seaside", "shore", "coast"]}},
        "hiking",
        "Family",
        {"food": {"children": ["restaurant", "home-cooking", {"baking": {"synonyms": ["bread"]}}]}},
    ],
    "strict": False,
}


def _write(tmp_path: Path, doc: object, name: str = "vocab.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


@pytest.fixture
def vocab(tmp_path) -> Vocabulary:
    return load_vocabulary(_write(tmp_path, SAMPLE))


# --- loading & validation --------------------------------------------------------


def test_load_flat_hierarchical_and_synonyms(vocab):
    assert vocab.tags == [
        "beach",
        "hiking",
        "family",
        "food",
        "restaurant",
        "home-cooking",
        "baking",
    ]
    assert vocab.synonyms == {
        "seaside": "beach",
        "shore": "beach",
        "coast": "beach",
        "bread": "baking",
    }
    assert vocab.parents == {"restaurant": "food", "home-cooking": "food", "baking": "food"}
    assert vocab.strict is False
    assert vocab.source and vocab.source.endswith("vocab.json")


def test_load_yaml_when_pyyaml_available(tmp_path):
    yaml = pytest.importorskip("yaml")
    p = tmp_path / "v.yaml"
    p.write_text(yaml.safe_dump(SAMPLE), encoding="utf-8")
    v = load_vocabulary(p)
    assert "beach" in v.tags and v.synonyms["shore"] == "beach"


def test_yaml_without_pyyaml_gives_actionable_error(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "yaml":
            raise ImportError("no yaml")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    p = tmp_path / "v.yaml"
    p.write_text("tags: [a]\n", encoding="utf-8")
    with pytest.raises(VocabularyError, match=r"pyimgtag\[vocab\]"):
        load_vocabulary(p)


def test_missing_file_names_path(tmp_path):
    with pytest.raises(VocabularyError, match="missing.json"):
        load_vocabulary(tmp_path / "missing.json")


def test_invalid_json_reports_line_and_column(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"tags": [\n  "a",\n  oops\n]}', encoding="utf-8")
    with pytest.raises(VocabularyError, match=r"bad\.json:3:3"):
        load_vocabulary(p)


@pytest.mark.parametrize(
    ("doc", "pattern"),
    [
        (["a"], "top level must be a mapping"),
        ({"strict": True}, "missing required 'tags'"),
        ({"tags": []}, "at least one tag"),
        ({"tags": "beach"}, r"tags: must be a list"),
        ({"tags": ["a"], "bogus": 1}, "unknown top-level key"),
        ({"tags": ["a"], "strict": "yes"}, "'strict' must be true or false"),
        ({"tags": [42]}, r"tags\[0\]: entry must be a string"),
        ({"tags": [""]}, r"tags\[0\]: tag name must be a non-empty string"),
        ({"tags": ["a", "A "]}, r"tags\[1\]: duplicate tag 'a'"),
        ({"tags": [{"a": 1, "b": 2}]}, r"tags\[0\]: a tag mapping must have exactly one key"),
        ({"tags": [{"a": "x"}]}, r"tags\[0\]\.a: tag options must be a mapping"),
        ({"tags": [{"a": {"color": "red"}}]}, r"tags\[0\]\.a: unknown option\(s\): color"),
        ({"tags": [{"a": {"synonyms": "b"}}]}, r"tags\[0\]\.a: 'synonyms' must be a list"),
        ({"tags": [{"a": {"synonyms": [""]}}]}, r"synonyms\[0\]: synonym must be a non-empty"),
        ({"tags": ["b", {"a": {"synonyms": ["b"]}}]}, "synonym 'b' is already a canonical tag"),
        ({"tags": [{"a": {"synonyms": ["x"]}}, {"b": {"synonyms": ["x"]}}]}, "already maps to 'a'"),
        ({"tags": [{"a": {"synonyms": ["b"]}}, "b"]}, "already declared as a synonym"),
        ({"tags": [{"a": {"children": "b"}}]}, r"tags\[0\]\.a\.children: must be a list"),
    ],
)
def test_structural_errors_name_entry_path(tmp_path, doc, pattern):
    p = _write(tmp_path, doc)
    with pytest.raises(VocabularyError, match=pattern) as exc:
        load_vocabulary(p)
    assert "vocab.json" in str(exc.value)


def test_null_options_and_self_synonym_are_tolerated(tmp_path):
    v = load_vocabulary(_write(tmp_path, {"tags": [{"a": None}, {"b": {"synonyms": ["B"]}}]}))
    assert v.tags == ["a", "b"] and v.synonyms == {}


# --- canonicalization ----------------------------------------------------------------


def test_normalize_tag():
    assert normalize_tag("  Home   Cooking ") == "home cooking"


def test_canonicalize_synonym_case_plural_and_dedup(vocab):
    out = vocab.canonicalize(["Seaside", "BEACH", "beaches", "Hikes", "families", "coast"])
    # 'hikes' -> 'hike' is not in the vocab, so it is kept verbatim (non-strict).
    assert out == ["beach", "hikes", "family"]
    st = vocab.stats
    assert st.exact == 1  # BEACH
    assert st.mapped[("seaside", "beach")] == 1
    assert st.mapped[("beaches", "beach")] == 1
    assert st.mapped[("coast", "beach")] == 1
    assert st.mapped[("families", "family")] == 1
    assert st.kept["hikes"] == 1
    assert st.total_mapped == 4


def test_canonicalize_non_strict_keeps_unknown(vocab):
    assert vocab.canonicalize(["sunset", "beach"]) == ["sunset", "beach"]
    assert vocab.stats.kept["sunset"] == 1
    assert vocab.stats.total_dropped == 0


def test_canonicalize_strict_drops_unknown(tmp_path):
    doc = dict(SAMPLE, strict=True)
    v = load_vocabulary(_write(tmp_path, doc))
    assert v.canonicalize(["sunset", "shore", "", "  "]) == ["beach"]
    assert v.stats.dropped == {"sunset": 1}
    assert v.stats.total_kept == 0


def test_canonicalize_synonym_via_plural(vocab):
    # "shores" -> singular "shore" -> synonym -> beach
    assert vocab.canonicalize(["shores", "breads"]) == ["beach", "baking"]


def test_stats_merge_and_to_dict(vocab):
    vocab.canonicalize(["seaside", "seaside", "sunset"])
    other = MappingStats()
    other.mapped[("seaside", "beach")] += 1
    other.exact += 2
    vocab.stats.merge(other)
    d = vocab.stats.to_dict()
    assert d["exact"] == 2
    assert d["mapped"] == [{"raw": "seaside", "canonical": "beach", "count": 3}]
    assert d["kept_off_vocabulary"] == [{"raw": "sunset", "count": 1}]
    assert d["dropped"] == []
    json.dumps(d)


# --- hierarchy ------------------------------------------------------------------------


def test_children_descendants_and_path(vocab):
    assert vocab.children("food") == ["restaurant", "home-cooking", "baking"]
    assert vocab.descendants("food") == ["food", "restaurant", "home-cooking", "baking"]
    assert vocab.descendants("Foods") == ["food", "restaurant", "home-cooking", "baking"]
    assert vocab.descendants("bread") == ["baking"]  # resolves through synonyms
    assert vocab.descendants("unknown tag") == ["unknown tag"]
    assert vocab.path("baking") == ["food", "baking"]
    assert vocab.path("hiking") == ["hiking"]


def test_nested_children_multiple_levels(tmp_path):
    doc = {"tags": [{"a": {"children": [{"b": {"children": ["c", "d"]}}, "e"]}}]}
    v = load_vocabulary(_write(tmp_path, doc))
    assert v.descendants("a") == ["a", "b", "c", "d", "e"]
    assert v.path("c") == ["a", "b", "c"]


# --- prompt block ----------------------------------------------------------------------


def test_prompt_block_lists_all_tags(vocab):
    block = vocab.prompt_block()
    assert block.startswith("Preferred tags")
    assert "beach, hiking, family, food, restaurant, home-cooking, baking" in block


def test_prompt_block_strict_wording(tmp_path):
    v = load_vocabulary(_write(tmp_path, {"tags": ["a", "b"], "strict": True}))
    assert v.prompt_block().startswith("Allowed tags — choose ONLY")


def test_to_dict_round_trips_shape(vocab):
    d = vocab.to_dict()
    assert set(d) == {"source", "strict", "tags", "synonyms", "parents"}
    json.dumps(d)


def test_example_vocabularies_load_and_agree():
    root = Path(__file__).resolve().parents[1] / "examples" / "vocabularies"
    js = load_vocabulary(root / "birding.json")
    assert js.descendants("bird") == ["bird", "raptor", "waterfowl", "songbird", "seabird"]
    assert js.canonicalize(["Seagulls", "hawk", "birds"]) == ["seabird", "raptor", "bird"]
    try:
        import yaml  # noqa: F401
    except ImportError:  # pragma: no cover - yaml optional
        return
    ym = load_vocabulary(root / "birding.yaml")
    assert ym.tags == js.tags and ym.synonyms == js.synonyms and ym.parents == js.parents
