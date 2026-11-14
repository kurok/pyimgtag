"""Pages show an empty state with a next step on a fresh database (#397).

The suite's main server runs on a seeded database, so these tests start a
second dashboard on an empty one (``tests/e2e_support.dashboard``). The
"filters matched nothing" case runs against the main server with the image
list stubbed empty.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.e2e_support import dashboard


@pytest.fixture(scope="module")
def empty_base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    home = tmp_path_factory.mktemp("empty-home")
    with dashboard(Path(home) / "empty.db", Path(home)) as base:
        yield base


@pytest.mark.parametrize(
    ("path", "title", "command"),
    [
        ("/review/", "No photos tagged yet", "pyimgtag run --input-dir <folder>"),
        ("/tags/", "No tags yet", "pyimgtag run --input-dir <folder>"),
        ("/judge/", "No photos scored yet", "pyimgtag judge --input-dir <folder>"),
        ("/", "No run in progress", "pyimgtag run --input-dir <folder>"),
    ],
)
def test_an_empty_library_says_what_to_run(
    page, empty_base_url: str, path: str, title: str, command: str
) -> None:
    page.goto(empty_base_url + path)
    empty = page.locator(".empty")
    expect(empty).to_be_visible()
    expect(empty).to_contain_text(title)
    expect(empty.locator("code")).to_have_text(command)


def test_the_idle_dashboard_shows_the_library_total_and_links(page, empty_base_url: str) -> None:
    page.goto(empty_base_url + "/")
    empty = page.locator(".empty")
    expect(empty).to_contain_text("Library: 0 photo(s) tagged.")
    expect(empty.get_by_role("link", name="Review")).to_have_attribute("href", "/review")
    expect(empty.get_by_role("link", name="Insights")).to_have_attribute("href", "/insights")


def test_a_query_with_no_matches_says_so(page, empty_base_url: str) -> None:
    page.goto(empty_base_url + "/query/")
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#results .empty")).to_contain_text("No images match these filters")
    expect(page.locator("#count")).to_have_text("0 results")


def test_a_filter_that_matches_nothing_is_not_an_empty_library(page, base_url: str) -> None:
    page.goto(base_url + "/review/")
    page.route(
        "**/review/api/images?*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"total": 0, "items": []}),
        ),
    )
    page.get_by_role("button", name="Errors").click()
    empty = page.locator("#grid .empty")
    expect(empty).to_contain_text("No photos match these filters")
    expect(empty).not_to_contain_text("No photos tagged yet")
    expect(empty.locator("code")).to_have_count(0)
