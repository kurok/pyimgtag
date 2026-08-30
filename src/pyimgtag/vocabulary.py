"""Controlled tag vocabulary: load, validate, and canonicalize model tags.

A vocabulary file (JSON always; YAML when PyYAML is installed) declares the
tags the user actually wants, optional synonyms, and an optional hierarchy::

    tags:
      - beach:
          synonyms: [seaside, shore, coast]
      - hiking
      - food:
          children: [restaurant, home-cooking, baking]
    strict: false

Two enforcement layers use it:

1. :meth:`Vocabulary.prompt_block` embeds the flattened tag list in the
   model prompt as the allowed set.
2. :meth:`Vocabulary.canonicalize` post-maps whatever the model returned —
   synonym table first, then case/whitespace/plural normalization, then (in
   strict mode) drop-with-count — so the outcome is deterministic across
   backends regardless of how well each model follows instructions.

Every ``raw -> canonical`` decision is counted in :class:`MappingStats` so the
run summary can show them and users can grow their synonym lists from real
data.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "MappingStats",
    "Vocabulary",
    "VocabularyError",
    "load_vocabulary",
    "normalize_tag",
]

_WS = re.compile(r"\s+")


class VocabularyError(ValueError):
    """Raised for an unreadable, malformed, or structurally invalid vocabulary file."""


def normalize_tag(raw: str) -> str:
    """Lower-case, trim, and collapse internal whitespace."""
    return _WS.sub(" ", str(raw).strip().lower())


def _singular_candidates(tag: str) -> list[str]:
    """Cheap English de-pluralization guesses, most specific first."""
    out: list[str] = []
    if tag.endswith("ies") and len(tag) > 4:
        out.append(tag[:-3] + "y")
    if tag.endswith("es") and len(tag) > 3:
        out.append(tag[:-2])
    if tag.endswith("s") and len(tag) > 2:
        out.append(tag[:-1])
    return out


@dataclass
class MappingStats:
    """Counts of canonicalization decisions accumulated over a run."""

    #: ``(raw, canonical)`` pairs where raw != canonical.
    mapped: Counter[tuple[str, str]] = field(default_factory=Counter)
    #: Off-vocabulary tags that were kept verbatim (non-strict mode).
    kept: Counter[str] = field(default_factory=Counter)
    #: Off-vocabulary tags that were dropped (strict mode).
    dropped: Counter[str] = field(default_factory=Counter)
    #: Tags that were already canonical.
    exact: int = 0

    def merge(self, other: MappingStats) -> None:
        """Fold *other* into this instance."""
        self.mapped.update(other.mapped)
        self.kept.update(other.kept)
        self.dropped.update(other.dropped)
        self.exact += other.exact

    @property
    def total_mapped(self) -> int:
        return sum(self.mapped.values())

    @property
    def total_dropped(self) -> int:
        return sum(self.dropped.values())

    @property
    def total_kept(self) -> int:
        return sum(self.kept.values())

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation (stable ordering, most frequent first)."""
        return {
            "exact": self.exact,
            "mapped": [
                {"raw": raw, "canonical": canon, "count": n}
                for (raw, canon), n in sorted(
                    self.mapped.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1])
                )
            ],
            "kept_off_vocabulary": [
                {"raw": raw, "count": n}
                for raw, n in sorted(self.kept.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "dropped": [
                {"raw": raw, "count": n}
                for raw, n in sorted(self.dropped.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        }


@dataclass
class Vocabulary:
    """A validated controlled vocabulary.

    Attributes:
        tags: Canonical tags in file order (parents before their children).
        synonyms: ``normalized synonym -> canonical tag``.
        parents: ``child -> parent`` for hierarchical entries.
        strict: When ``True``, off-vocabulary tags are dropped instead of kept.
        source: Path the vocabulary was loaded from (for messages), if any.
    """

    tags: list[str]
    synonyms: dict[str, str] = field(default_factory=dict)
    parents: dict[str, str] = field(default_factory=dict)
    strict: bool = False
    source: str | None = None
    stats: MappingStats = field(default_factory=MappingStats)

    def __post_init__(self) -> None:
        self._canonical: set[str] = set(self.tags)

    # --- lookup ------------------------------------------------------------

    def is_canonical(self, tag: str) -> bool:
        return tag in self._canonical

    def children(self, tag: str) -> list[str]:
        """Direct children of *tag* in file order."""
        return [c for c in self.tags if self.parents.get(c) == tag]

    def descendants(self, tag: str) -> list[str]:
        """*tag* followed by all of its descendants (depth-first, file order).

        The input is canonicalized first, so ``descendants("Foods")`` resolves
        through synonyms / plural normalization like everything else.
        """
        root = self.resolve(tag)
        if root is None:
            return [normalize_tag(tag)]
        out: list[str] = []
        stack = [root]
        while stack:
            cur = stack.pop(0)
            out.append(cur)
            stack = self.children(cur) + stack
        return out

    def path(self, tag: str) -> list[str]:
        """Ancestor chain from the root down to *tag* (inclusive)."""
        chain = [tag]
        seen = {tag}
        while chain[0] in self.parents and self.parents[chain[0]] not in seen:
            parent = self.parents[chain[0]]
            chain.insert(0, parent)
            seen.add(parent)
        return chain

    def resolve(self, raw: str) -> str | None:
        """Map one raw tag to its canonical form, or ``None`` if off-vocabulary."""
        tag = normalize_tag(raw)
        if not tag:
            return None
        if tag in self._canonical:
            return tag
        if tag in self.synonyms:
            return self.synonyms[tag]
        for cand in _singular_candidates(tag):
            if cand in self._canonical:
                return cand
            if cand in self.synonyms:
                return self.synonyms[cand]
        return None

    # --- enforcement layers ------------------------------------------------

    def canonicalize(self, raw_tags: list[str]) -> list[str]:
        """Post-map model output to canonical tags (layer 2).

        Order is preserved and duplicates are removed after mapping. Every
        decision is recorded in :attr:`stats`.
        """
        out: list[str] = []
        for raw in raw_tags:
            norm = normalize_tag(raw)
            if not norm:
                continue
            canon = self.resolve(norm)
            if canon is None:
                if self.strict:
                    self.stats.dropped[norm] += 1
                    continue
                self.stats.kept[norm] += 1
                canon = norm
            elif canon != norm:
                self.stats.mapped[(norm, canon)] += 1
            else:
                self.stats.exact += 1
            if canon not in out:
                out.append(canon)
        return out

    def prompt_block(self) -> str:
        """Instruction block embedding the allowed tag set (layer 1)."""
        listing = ", ".join(self.tags)
        if self.strict:
            head = "Allowed tags — choose ONLY from this list, exactly as written:"
        else:
            head = "Preferred tags — choose from this list whenever one fits, exactly as written:"
        return f"{head}\n{listing}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "strict": self.strict,
            "tags": list(self.tags),
            "synonyms": dict(sorted(self.synonyms.items())),
            "parents": dict(sorted(self.parents.items())),
        }


# --- loading ---------------------------------------------------------------


def _read_document(path: Path) -> Any:
    """Parse *path* as YAML (if PyYAML is available) or JSON."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VocabularyError(f"{path}: cannot read vocabulary file: {exc}") from exc

    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped,unused-ignore]
        except ImportError as exc:
            raise VocabularyError(
                f"{path}: YAML vocabularies need PyYAML — install with "
                "pip install 'pyimgtag[vocab]' or convert the file to JSON"
            ) from exc
        try:
            return yaml.safe_load(text)
        except yaml.MarkedYAMLError as exc:  # pragma: no cover - depends on PyYAML
            mark = exc.problem_mark
            where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "unknown"
            raise VocabularyError(f"{path}:{where}: invalid YAML: {exc.problem}") from exc
        except yaml.YAMLError as exc:  # pragma: no cover - depends on PyYAML
            raise VocabularyError(f"{path}: invalid YAML: {exc}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise VocabularyError(
            f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg} "
            "(YAML files must use a .yaml/.yml extension)"
        ) from exc


class _Builder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.tags: list[str] = []
        self.synonyms: dict[str, str] = {}
        self.parents: dict[str, str] = {}

    def fail(self, where: str, msg: str) -> VocabularyError:
        return VocabularyError(f"{self.path}: {where}: {msg}")

    def add_tag(self, raw: Any, where: str, parent: str | None) -> str:
        if not isinstance(raw, str) or not normalize_tag(raw):
            raise self.fail(where, f"tag name must be a non-empty string, got {raw!r}")
        tag = normalize_tag(raw)
        if tag in self.tags:
            raise self.fail(where, f"duplicate tag {tag!r}")
        if tag in self.synonyms:
            raise self.fail(where, f"{tag!r} is already declared as a synonym")
        self.tags.append(tag)
        if parent is not None:
            self.parents[tag] = parent
        return tag

    def add_synonyms(self, values: Any, where: str, canonical: str) -> None:
        if not isinstance(values, list):
            raise self.fail(where, f"'synonyms' must be a list, got {type(values).__name__}")
        for i, raw in enumerate(values):
            sub = f"{where}.synonyms[{i}]"
            if not isinstance(raw, str) or not normalize_tag(raw):
                raise self.fail(sub, f"synonym must be a non-empty string, got {raw!r}")
            syn = normalize_tag(raw)
            if syn == canonical:
                continue
            if syn in self.tags:
                raise self.fail(sub, f"synonym {syn!r} is already a canonical tag")
            other = self.synonyms.get(syn)
            if other is not None and other != canonical:
                raise self.fail(sub, f"synonym {syn!r} already maps to {other!r}")
            self.synonyms[syn] = canonical

    def add_entries(self, entries: Any, where: str, parent: str | None) -> None:
        if not isinstance(entries, list):
            raise self.fail(where, f"must be a list, got {type(entries).__name__}")
        for i, entry in enumerate(entries):
            sub = f"{where}[{i}]"
            if isinstance(entry, str):
                self.add_tag(entry, sub, parent)
                continue
            if isinstance(entry, dict):
                if len(entry) != 1:
                    raise self.fail(sub, "a tag mapping must have exactly one key (the tag name)")
                (name, spec), *_ = entry.items()
                tag = self.add_tag(name, sub, parent)
                if spec is None:
                    continue
                if not isinstance(spec, dict):
                    raise self.fail(
                        f"{sub}.{tag}", "tag options must be a mapping with synonyms/children"
                    )
                unknown = set(spec) - {"synonyms", "children"}
                if unknown:
                    raise self.fail(
                        f"{sub}.{tag}", f"unknown option(s): {', '.join(sorted(unknown))}"
                    )
                if "synonyms" in spec:
                    self.add_synonyms(spec["synonyms"], f"{sub}.{tag}", tag)
                if "children" in spec:
                    self.add_entries(spec["children"], f"{sub}.{tag}.children", tag)
                continue
            raise self.fail(sub, f"entry must be a string or a one-key mapping, got {entry!r}")


def load_vocabulary(path: str | Path) -> Vocabulary:
    """Load and validate a vocabulary file.

    Args:
        path: ``.json`` (always supported) or ``.yaml``/``.yml`` (needs PyYAML).

    Returns:
        A :class:`Vocabulary`.

    Raises:
        VocabularyError: On read, parse, or structural errors. Messages name
            the file (and line for parse errors, or the entry path for
            structural errors).
    """
    p = Path(path)
    doc = _read_document(p)
    if not isinstance(doc, dict):
        raise VocabularyError(f"{p}: top level must be a mapping with a 'tags' key")
    unknown = set(doc) - {"tags", "strict"}
    if unknown:
        raise VocabularyError(f"{p}: unknown top-level key(s): {', '.join(sorted(unknown))}")
    if "tags" not in doc:
        raise VocabularyError(f"{p}: missing required 'tags' list")
    strict = doc.get("strict", False)
    if not isinstance(strict, bool):
        raise VocabularyError(f"{p}: 'strict' must be true or false, got {strict!r}")

    b = _Builder(p)
    b.add_entries(doc["tags"], "tags", None)
    if not b.tags:
        raise VocabularyError(f"{p}: 'tags' must contain at least one tag")
    return Vocabulary(
        tags=b.tags, synonyms=b.synonyms, parents=b.parents, strict=strict, source=str(p)
    )
