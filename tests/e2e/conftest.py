"""End-to-end Playwright smoke fixtures.

Drives the running pyimgtag dashboard (started by the surrounding
runner — ``scripts/test-smoke-local.sh`` locally,
``.github/workflows/pr-tests.yml`` in CI) at ``BASE_URL`` (default
``http://127.0.0.1:8000``).

Fail-loud signals collected per test:

- HTTP **5xx** on any response — captured via ``page.on("response")``.
- Uncaught **page errors** (JS exceptions that bubble to ``window``).
- Browser **console errors** (``console.error`` / runtime errors).

Each test uses the ``page`` fixture below (instead of pytest-playwright's
default) so the same hooks are wired up consistently. Failures
automatically write a screenshot + trace to
``tests/e2e/artifacts/<test-id>/`` so CI can upload them.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterator

import pytest

# Hard-skip the entire suite if a Playwright environment is not available.
# Locally the runner script installs the deps; in CI the workflow does.
pytest.importorskip(
    "playwright.sync_api",
    reason="install with: pip install playwright && playwright install chromium",
)
pytest.importorskip("requests", reason="install with: pip install requests")

# ruff: noqa: E402
import requests
from playwright.sync_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Error,
    Page,
    Response,
    sync_playwright,
)

BASE_URL = os.environ.get("BASE_URL") or (
    f"http://{os.environ.get('HOST', '127.0.0.1')}:{os.environ.get('PORT', '8000')}"
)

ARTIFACT_DIR = Path(__file__).parent / "artifacts"


def _wait_for_health(url: str, timeout: float = 30.0) -> None:
    """Block until ``GET <url>/health`` returns 200, or fail the test session."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = requests.get(url + "/health", timeout=1.5)
            if r.status_code == 200 and r.json().get("ok") is True:
                return
        except Exception as exc:
            last_err = exc
        time.sleep(0.25)
    pytest.fail(
        f"dashboard never became healthy at {url}/health within {timeout:.0f}s "
        f"(last error: {last_err!r})"
    )


@pytest.fixture(scope="session")
def base_url() -> str:
    """Return the smoke target URL after confirming /health is up.

    The runner is responsible for starting the app; this fixture only
    waits for it to come up so individual tests don't each pay the
    startup latency.
    """
    _wait_for_health(BASE_URL)
    return BASE_URL


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=os.environ.get("PYIMGTAG_E2E_HEADLESS", "1") != "0",
        )
        yield browser
        browser.close()


@pytest.fixture()
def context(browser: Browser, request: pytest.FixtureRequest) -> Iterator[BrowserContext]:
    """Per-test browser context with Playwright tracing enabled.

    The trace is started for every test and only kept on failure, so
    you get a full Playwright trace (DOM, network, screenshots) for
    any red test without paying the disk cost on green ones.
    """
    artefact_dir = ARTIFACT_DIR / request.node.name
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
    yield ctx
    failed = (
        hasattr(request.node, "rep_call") and request.node.rep_call.failed  # type: ignore[attr-defined]
    )
    trace_path = artefact_dir / "trace.zip"
    if failed:
        artefact_dir.mkdir(parents=True, exist_ok=True)
        ctx.tracing.stop(path=str(trace_path))
    else:
        ctx.tracing.stop()
    ctx.close()


@pytest.fixture()
def page(context: BrowserContext, request: pytest.FixtureRequest) -> Iterator[Page]:
    """Page with console / pageerror / 5xx capture wired up.

    The captured lists are attached to the page object as
    ``page.console_errors`` / ``page.page_errors`` / ``page.bad_responses``
    so individual tests can assert against them.
    """
    p = context.new_page()
    p.console_errors = []  # type: ignore[attr-defined]
    p.page_errors = []  # type: ignore[attr-defined]
    p.bad_responses = []  # type: ignore[attr-defined]

    def _on_console(msg: ConsoleMessage) -> None:
        if msg.type == "error":
            p.console_errors.append(msg.text)  # type: ignore[attr-defined]

    def _on_pageerror(err: Error) -> None:
        p.page_errors.append(str(err))  # type: ignore[attr-defined]

    def _on_response(resp: Response) -> None:
        if resp.status >= 500:
            p.bad_responses.append(f"{resp.status} {resp.url}")  # type: ignore[attr-defined]

    p.on("console", _on_console)
    p.on("pageerror", _on_pageerror)
    p.on("response", _on_response)

    yield p

    failed = (
        hasattr(request.node, "rep_call") and request.node.rep_call.failed  # type: ignore[attr-defined]
    )
    if failed:
        artefact_dir = ARTIFACT_DIR / request.node.name
        artefact_dir.mkdir(parents=True, exist_ok=True)
        try:
            p.screenshot(path=str(artefact_dir / "screenshot.png"), full_page=True)
        except Exception:
            pass
    p.close()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    """Expose call-phase outcome on the item so fixtures can detect failure."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
