"""The nav and page shell work on a phone-width viewport (#406).

At 390 px the document must not scroll sideways, every nav destination
must be reachable (the nav scrolls on its own), and the active link must
be in view on load. On desktop widths the nav must load unscrolled.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page

PHONE = {"width": 390, "height": 800}

_NAV_STATE = (
    "(() => { const n = document.querySelector('nav.nav');"
    " return {scrollX: window.scrollX, docWidth: document.documentElement.scrollWidth,"
    " navScroll: n.scrollLeft, navScrollWidth: n.scrollWidth, navWidth: n.clientWidth,"
    " navHeight: n.getBoundingClientRect().height}; })()"
)


def _open(browser: Browser, base_url: str, viewport: dict) -> Iterator[Page]:
    ctx = browser.new_context(viewport=viewport, base_url=base_url)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.on("console", lambda msg: msg.type == "error" and errors.append(msg.text))
    try:
        yield page
        assert errors == [], errors
    finally:
        ctx.close()


@pytest.fixture()
def phone(browser: Browser, base_url: str) -> Iterator[Page]:
    yield from _open(browser, base_url, PHONE)


def _goto(page: Page, path: str) -> None:
    page.goto(path, wait_until="load")


def _in_view(page: Page, selector: str, width: int) -> dict:
    box = page.locator(selector).bounding_box()
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= width, box
    return box


@pytest.mark.parametrize("path", ["/", "/review/"])
def test_the_page_does_not_scroll_sideways(phone: Page, path: str) -> None:
    _goto(phone, path)
    state = phone.evaluate(_NAV_STATE)
    assert state["docWidth"] <= PHONE["width"], (path, state)


@pytest.mark.parametrize(("path", "active"), [("/", "Dashboard"), ("/review/", "Review")])
def test_the_active_link_is_in_view_on_load(phone: Page, path: str, active: str) -> None:
    _goto(phone, path)
    assert phone.locator("nav.nav a.nav-link.active").inner_text() == active
    _in_view(phone, "nav.nav a.nav-link.active", PHONE["width"])


def test_about_is_reached_by_scrolling_the_nav_not_the_page(phone: Page) -> None:
    _goto(phone, "/review/")
    about = phone.locator("nav.nav a.nav-link", has_text="About")
    about.scroll_into_view_if_needed()
    _in_view(phone, "nav.nav a.nav-link >> text=About", PHONE["width"])
    state = phone.evaluate(_NAV_STATE)
    assert state["scrollX"] == 0, state
    assert state["navScroll"] > 0, state
    about.click()
    phone.wait_for_url(re.compile(r".*/about/?$"))


def test_the_active_link_far_right_is_scrolled_into_view(phone: Page) -> None:
    """On /about the active link is the last one; it must start in view."""
    _goto(phone, "/about")
    _in_view(phone, "nav.nav a.nav-link.active", PHONE["width"])
    assert phone.evaluate("window.scrollX") == 0


@pytest.mark.parametrize("width", [1200, 1440])
@pytest.mark.parametrize("path", ["/review/", "/about"])
def test_the_desktop_nav_loads_unscrolled(
    browser: Browser, base_url: str, width: int, path: str
) -> None:
    """On desktop the nav is not pre-scrolled, and stays 52 px tall."""
    for page in _open(browser, base_url, {"width": width, "height": 900}):
        _goto(page, path)
        state = page.evaluate(_NAV_STATE)
        assert state["navHeight"] == 52, state
        assert state["navScroll"] == 0, state
        if width == 1440:
            assert state["navScrollWidth"] <= state["navWidth"], state
        _in_view(page, ".nav-logo", width)
