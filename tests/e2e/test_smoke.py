"""Auto-discover-the-nav smoke for the pyimgtag dashboard.

Visits the home page, scrapes every visible top-nav link, then clicks
through each one in order and asserts:

- the page navigation didn't blow up (HTTP 5xx, JS exception, or a
  console error fail the test),
- the URL changed (or stayed the same for ``/``-style anchors),
- the rendered DOM has *meaningful* visible content (heuristic: the
  body has a heading and at least one non-trivial chunk of text).

The test deliberately discovers the nav at runtime rather than baking
it into the test, so adding a new top-level page to ``nav.py`` is
automatically covered without touching the smoke.
"""

from __future__ import annotations

import re

import pytest


def _drain(page) -> tuple[list[str], list[str], list[str]]:
    """Return (console_errors, page_errors, bad_responses) accumulated so far."""
    return (
        list(page.console_errors),  # type: ignore[attr-defined]
        list(page.page_errors),  # type: ignore[attr-defined]
        list(page.bad_responses),  # type: ignore[attr-defined]
    )


def _assert_no_errors(page, label: str) -> None:
    """Fail loudly if any error signal fired since the last drain."""
    console_errors, page_errors, bad_responses = _drain(page)
    problems: list[str] = []
    if bad_responses:
        problems.append(f"5xx responses while loading {label}: {bad_responses}")
    if page_errors:
        problems.append(f"uncaught page errors on {label}: {page_errors}")
    if console_errors:
        problems.append(f"console errors on {label}: {console_errors}")
    assert not problems, "\n".join(problems)


def _assert_page_has_content(page, label: str) -> None:
    """Heuristic 'meaningful content' check.

    A blank page typically has an empty body or a body shorter than a
    few dozen characters. Both are red flags. We also reject the
    literal template-token ``__FOO__`` pattern because that means
    string substitution drifted.

    A successful render must show the dashboard nav with at least one
    visible link — that proves the layout chrome rendered and the
    request reached the FastAPI app rather than (e.g.) a bare 5xx
    error page from uvicorn.
    """
    body_text = page.locator("body").inner_text().strip()
    assert body_text, f"{label} rendered an empty <body>"
    assert len(body_text) >= 40, f"{label} body looks blank: {body_text!r}"

    nav_links = page.locator("nav.nav a.nav-link").count()
    assert nav_links > 0, f"{label} rendered without the dashboard nav"

    leftover = re.findall(r"__[A-Z][A-Z0-9_]+__", body_text)
    assert not leftover, f"{label} rendered unreplaced template tokens: {set(leftover)}"


def test_health_endpoint(base_url: str) -> None:
    """Sanity: ``/health`` is the same signal the runner waits on."""
    import requests

    r = requests.get(base_url + "/health", timeout=3)
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert isinstance(body.get("version"), str)


def test_home_renders(page, base_url: str) -> None:
    """The dashboard home page loads with no errors and has content."""
    page.goto(base_url + "/")
    page.wait_for_load_state("networkidle")
    _assert_no_errors(page, "/")
    _assert_page_has_content(page, "/")


def test_each_nav_link_clicks_and_renders(page, base_url: str) -> None:
    """Auto-discover every top-nav link and click through them in order.

    The test must surface a useful diagnostic when a nav link is
    silently broken — clicking each one in the same browser context
    keeps the trace continuous so a Playwright-trace upload tells the
    full story.
    """
    page.goto(base_url + "/")
    page.wait_for_load_state("networkidle")

    nav_links = page.locator("nav.nav a.nav-link")
    count = nav_links.count()
    assert count > 0, "no nav links discovered — the dashboard nav is missing"

    visited: list[str] = []
    for i in range(count):
        link = nav_links.nth(i)
        label = link.inner_text().strip() or f"<link {i}>"
        href = link.get_attribute("href") or ""
        target = href if href.startswith("http") else (base_url + href)

        # Use direct goto rather than .click() so an accidentally-broken
        # ``href`` (or a JS handler that swallows the navigation) still
        # produces a deterministic test failure rather than a hang.
        page.goto(target)
        page.wait_for_load_state("networkidle")
        _assert_no_errors(page, f"{label} ({target})")
        _assert_page_has_content(page, f"{label} ({target})")
        visited.append(label)

    assert len(visited) == count
    assert "Dashboard" in visited and "About" in visited, (
        f"discovered nav was unexpectedly short: {visited}"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/review/",
        "/faces/",
        "/tags/",
        "/query/",
        "/judge/",
        "/about/",
    ],
)
def test_known_routes_render_cleanly(page, base_url: str, path: str) -> None:
    """Belt-and-braces: every documented top-level page renders fresh.

    The auto-discovery test above already covers these but does so in a
    single browsing session. Re-loading each in isolation catches any
    page that only renders correctly after a prior page warmed it up
    (a real bug we hit in 0.8.x with the version-check cache).
    """
    page.goto(base_url + path)
    page.wait_for_load_state("networkidle")
    _assert_no_errors(page, path)
    _assert_page_has_content(page, path)
