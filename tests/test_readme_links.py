"""Every in-page link in the README points at a heading that exists (#469).

GitHub builds a heading's anchor from its rendered text: lower-cased, with
punctuation other than hyphens dropped and spaces turned into hyphens;
repeated headings get -1, -2, ... Only fenced code is skipped.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_README = Path(__file__).resolve().parent.parent / "README.md"
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.*?)(?:\s+#+)?\s*$")
_LINK = re.compile(r"\]\(#([^)\s]+)\)")


def _slug(heading: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # [text](url) -> text
    text = text.strip().lower()
    keep = (
        c
        for c in text
        if c in "-_ " or unicodedata.category(c)[0] in "LNM"  # letters, numbers, marks
    )
    return "".join(keep).replace(" ", "-")


def _outside_code(text: str):
    fence = None
    for line in text.splitlines():
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is None:
            yield line


def _anchors(text: str) -> set[str]:
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for line in _outside_code(text):
        match = _HEADING.match(line)
        if not match:
            continue
        slug = _slug(match.group(1))
        count = seen.get(slug, 0)
        anchors.add(slug if count == 0 else f"{slug}-{count}")
        seen[slug] = count + 1
    return anchors


def test_every_in_page_link_has_a_heading():
    text = _README.read_text(encoding="utf-8")
    anchors = _anchors(text)
    links = {m for line in _outside_code(text) for m in _LINK.findall(line)}
    assert links, "expected some in-page links"
    assert sorted(links - anchors) == []


def test_the_slug_rules():
    assert _slug("Checking what `--write-exif` wrote (Windows, non-ASCII paths)") == (
        "checking-what---write-exif-wrote-windows-non-ascii-paths"
    )
    assert _slug("[Link](https://x.y) here") == "link-here"
    assert _slug("हिन्दी") == "हिन्दी"  # combining marks are kept


def test_the_windows_verification_recipe_is_documented():
    text = _README.read_text(encoding="utf-8")
    assert "-charset filename=utf8" in text
    assert "#checking-what---write-exif-wrote-windows-non-ascii-paths" in text
