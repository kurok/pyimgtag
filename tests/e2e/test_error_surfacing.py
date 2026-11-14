"""Dashboard pages show backend failures instead of hiding them (#396).

Each test stubs one API route with ``page.route`` to fail, then checks what
the user sees. The stubbed 5xx responses are intentional, so these tests do
not call the suite's no-5xx assertion.
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import Route, expect


def _fail(status: int, detail: str):
    def handler(route: Route) -> None:
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps({"detail": detail}),
        )

    return handler


def test_tags_load_failure_shows_an_error_with_retry(page, base_url: str) -> None:
    page.route("**/tags/api/tags", _fail(500, "database is locked"))
    page.goto(f"{base_url}/tags/")

    banner = page.locator("#list .load-error")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("database is locked")
    expect(page.locator("#status")).not_to_have_text("Loading…")
    expect(page.locator("#status")).to_have_text("Could not load tags")

    page.unroute("**/tags/api/tags")
    banner.get_by_role("button", name="Retry").click()
    expect(page.locator("#list .load-error")).to_have_count(0)
    expect(page.locator("#status")).to_have_text(re.compile(r"^\d+ tags$"))


def test_query_shows_the_400_detail_not_undefined(page, base_url: str) -> None:
    page.goto(f"{base_url}/query/")
    page.fill("#f_year", "abc")
    page.get_by_role("button", name="Search").click()

    expect(page.locator("#results .load-error")).to_contain_text(
        "expected a 4-digit year 'YYYY', got 'abc'"
    )
    expect(page.locator("#count")).not_to_contain_text("undefined")
    expect(page.locator("#count")).to_have_text("Search failed")


def test_enter_in_a_query_filter_runs_the_search(page, base_url: str) -> None:
    page.goto(f"{base_url}/query/")
    page.fill("#f_tag", "sunset")
    with page.expect_request("**/query/api/images?*") as request:
        page.press("#f_tag", "Enter")
    assert "tag=sunset" in request.value.url


def test_a_failed_rename_keeps_the_modal_open_with_the_reason(page, base_url: str) -> None:
    page.route(
        "**/tags/api/tags",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([{"tag": "sunset", "count": 3}]),
        ),
    )
    page.route("**/tags/api/tags/rename", _fail(500, "disk I/O error"))
    page.goto(f"{base_url}/tags/")

    page.get_by_role("button", name="Rename").first.click()
    page.fill("#m-inp", "dusk")
    page.click("#modal-confirm")

    expect(page.locator("#modal-overlay")).to_have_class("modal-overlay open")
    expect(page.locator("#modal-sub")).to_have_text("Error: disk I/O error")


def test_a_refused_run_control_is_shown_on_the_dashboard(page, base_url: str) -> None:
    page.route(
        "**/api/run/current",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"active": True, "state": "running", "command": "run", "counters": {}}),
        ),
    )
    page.route("**/api/run/current/stop", _fail(404, "no active run"))
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(f"{base_url}/")

    stop = page.locator("#stopBtn")
    expect(stop).to_be_enabled()
    stop.click()
    expect(page.locator("#error")).to_have_text("Could not stop: no active run")


def test_a_rejected_query_offers_no_pointless_retry(page, base_url: str) -> None:
    """A 400 cannot succeed on retry; a 503 can."""
    page.goto(f"{base_url}/query/")
    page.fill("#f_year", "abc")
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#results .load-error")).to_be_visible()
    expect(page.locator("#results .load-error-retry")).to_have_count(0)

    page.route("**/query/api/images?*", _fail(503, "busy, try again"))
    page.fill("#f_year", "")
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#results .load-error")).to_contain_text("busy, try again")
    expect(page.locator("#results .load-error-retry")).to_have_count(1)


def test_a_non_json_success_is_reported_not_counted(page, base_url: str) -> None:
    page.route(
        "**/query/api/images?*",
        lambda route: route.fulfill(
            status=200, content_type="text/html", body="<html>proxy</html>"
        ),
    )
    page.goto(f"{base_url}/query/")
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#results .load-error")).to_contain_text("Unexpected response")
    expect(page.locator("#count")).to_have_text("Search failed")


def test_enter_in_a_select_does_not_search(page, base_url: str) -> None:
    page.goto(f"{base_url}/query/")
    requests_seen: list[str] = []
    page.on("request", lambda r: requests_seen.append(r.url) if "/api/images" in r.url else None)
    page.focus("#f_status")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert requests_seen == []
