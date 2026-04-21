"""Tests for the shared navigation bar."""

from __future__ import annotations

from pyimgtag.webapp.nav import NAV_STYLES, render_nav


def test_render_nav_includes_all_links():
    html = render_nav("dashboard")
    for href in ('href="/"', 'href="/review"', 'href="/faces"',
                 'href="/tags"', 'href="/query"', 'href="/judge"'):
        assert href in html, f"missing {href}"


def test_render_nav_marks_active_section():
    for section in ("dashboard", "review", "faces", "tags", "query", "judge"):
        html = render_nav(section)
        assert "nav-link active" in html


def test_render_nav_only_one_active():
    html = render_nav("tags")
    assert html.count("nav-link active") == 1


def test_nav_styles_not_empty():
    assert ".nav" in NAV_STYLES
    assert ".nav-link" in NAV_STYLES
