"""Tests for the shared navigation bar."""

from __future__ import annotations

from pyimgtag.webapp.nav import NAV_STYLES, render_nav


def test_render_nav_includes_all_links():
    html = render_nav("dashboard")
    for href in (
        'href="/"',
        'href="/review"',
        'href="/faces"',
        'href="/tags"',
        'href="/query"',
        'href="/search"',
        'href="/events"',
        'href="/judge"',
    ):
        assert href in html, f"missing {href}"


def test_render_nav_marks_active_section():
    for section in ("dashboard", "review", "faces", "tags", "query", "search", "events", "judge"):
        html = render_nav(section)
        assert "nav-link active" in html


def test_render_nav_only_one_active():
    html = render_nav("tags")
    assert html.count("nav-link active") == 1


def test_nav_styles_not_empty():
    assert ".nav" in NAV_STYLES
    assert ".nav-link" in NAV_STYLES


# --- accessibility baseline (#402) ---------------------------------------------


def test_the_active_nav_link_is_marked_current_once():
    from pyimgtag.webapp.nav import render_nav

    html = render_nav("tags")
    assert html.count('aria-current="page"') == 1
    assert 'class="nav-link active" href="/tags" aria-current="page"' in html
    assert 'aria-current="page"' not in render_nav("no-such-page")


def test_the_shared_modal_is_a_dialog():
    from pyimgtag.webapp.nav import MODAL_HTML, MODAL_JS

    assert 'role="dialog"' in MODAL_HTML
    assert 'aria-modal="true"' in MODAL_HTML
    assert 'aria-labelledby="modal-title"' in MODAL_HTML
    assert "e.key === 'Escape'" in MODAL_JS
    assert "_modalReturnFocus" in MODAL_JS
