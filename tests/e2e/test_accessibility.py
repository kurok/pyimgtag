"""Keyboard and screen-reader baseline on Tags, Query, Review and Judge (#402).

Lists are stubbed with ``page.route`` so every test sees the controls it
needs (tag rows, image cards) whatever the server's database holds. The
stubbed thumbnails 404, so cards fall back to their text placeholder, which
must be just as reachable as the image.
"""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, expect

_UNLABELLED = """
() => [...document.querySelectorAll('input,select,textarea')]
  .filter(e => !(e.labels && e.labels.length) && !e.getAttribute('aria-label'))
  .map(e => e.id || e.className || e.tagName)
"""

_IMAGES = {
    "total": 2,
    "items": [
        {
            "file_path": f"/missing/{name}",
            "file_name": name,
            "tags": ["sunset"],
            "status": "ok",
            "score": 7,
            "rating": 4,
        }
        for name in ("a.jpg", "b.jpg")
    ],
}


def _json(route, body) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def _stub_lists(page: Page) -> None:
    page.route("**/tags/api/tags", lambda r: _json(r, [{"tag": "sunset", "count": 2}]))
    page.route("**/review/api/images?*", lambda r: _json(r, _IMAGES))
    page.route("**/judge/api/scores?*", lambda r: _json(r, _IMAGES))


@pytest.mark.parametrize("path", ["/tags/", "/query/", "/review/", "/judge/"])
def test_every_form_control_has_a_label(page: Page, base_url: str, path: str) -> None:
    _stub_lists(page)
    page.goto(base_url + path)
    page.wait_for_load_state("domcontentloaded")
    if path in ("/review/", "/judge/"):
        expect(page.locator("#grid .img-card")).to_have_count(2)
    assert page.evaluate(_UNLABELLED) == []


@pytest.mark.parametrize("path", ["/tags/", "/query/", "/review/", "/judge/"])
def test_one_main_landmark_and_the_current_page_is_marked(
    page: Page, base_url: str, path: str
) -> None:
    page.goto(base_url + path)
    expect(page.locator("main")).to_have_count(1)
    expect(page.locator('nav [aria-current="page"]')).to_have_count(1)


def test_the_modal_takes_focus_closes_on_escape_and_gives_it_back(
    page: Page, base_url: str
) -> None:
    _stub_lists(page)
    page.goto(base_url + "/tags/")
    trigger = page.get_by_role("button", name="Delete").first
    trigger.focus()
    page.keyboard.press("Enter")

    dialog = page.get_by_role("dialog", name="Delete tag")
    expect(dialog).to_be_visible()
    assert page.evaluate(
        "() => document.querySelector('#modal-overlay .modal-box').contains(document.activeElement)"
    )
    # Tab stays inside the dialog.
    for _ in range(4):
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.querySelector('#modal-overlay .modal-box')"
            ".contains(document.activeElement)"
        )

    page.keyboard.press("Escape")
    expect(page.locator("#modal-overlay")).not_to_have_class(re.compile(r"\bopen\b"))
    expect(trigger).to_be_focused()


@pytest.mark.parametrize("path", ["/review/", "/judge/"])
def test_the_lightbox_works_from_the_keyboard(page: Page, base_url: str, path: str) -> None:
    _stub_lists(page)
    page.goto(base_url + path)
    thumb = page.get_by_role("button", name="Open a.jpg")
    expect(thumb).to_be_visible()
    thumb.focus()
    page.keyboard.press("Enter")

    lightbox = page.locator("#lightbox")
    expect(lightbox).to_have_class(re.compile(r"\bopen\b"))
    close = page.get_by_role("button", name="Close")
    expect(close).to_be_focused()
    page.keyboard.press("Enter")
    expect(lightbox).not_to_have_class(re.compile(r"\bopen\b"))
    expect(page.get_by_role("button", name="Open a.jpg")).to_be_focused()


def test_tag_remove_buttons_say_which_tag(page: Page, base_url: str) -> None:
    _stub_lists(page)
    page.goto(base_url + "/review/")
    expect(page.get_by_role("button", name="Remove tag sunset")).to_have_count(2)


@pytest.mark.parametrize("path", ["/review/", "/judge/"])
def test_tab_stays_inside_the_open_lightbox(page: Page, base_url: str, path: str) -> None:
    _stub_lists(page)
    page.goto(base_url + path)
    page.get_by_role("button", name="Open a.jpg").focus()
    page.keyboard.press("Space")  # Space activates too
    expect(page.locator("#lightbox")).to_have_class(re.compile(r"\bopen\b"))
    for _ in range(5):
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.getElementById('lightbox').contains(document.activeElement)"
        )
    for _ in range(3):
        page.keyboard.press("Shift+Tab")
        assert page.evaluate(
            "() => document.getElementById('lightbox').contains(document.activeElement)"
        )


@pytest.mark.parametrize("path", ["/review/", "/judge/"])
def test_a_backdrop_click_also_gives_focus_back(page: Page, base_url: str, path: str) -> None:
    _stub_lists(page)
    page.goto(base_url + path)
    thumb = page.get_by_role("button", name="Open b.jpg")
    thumb.focus()
    page.keyboard.press("Enter")
    page.locator("#lightbox").click(position={"x": 5, "y": 5})
    expect(page.locator("#lightbox")).not_to_have_class(re.compile(r"\bopen\b"))
    expect(page.get_by_role("button", name="Open b.jpg")).to_be_focused()
