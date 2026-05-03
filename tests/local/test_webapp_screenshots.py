"""Local-only Playwright smoke: screenshot every webapp page + every menu/option.

Boots the unified pyimgtag webapp in a uvicorn thread, drives it with a
sandboxed Chromium, and writes one PNG per page (and per dropdown /
pill / sort option) into ``tests/local/screenshots/<timestamp>/``.

The test is intentionally excluded from CI — see
``tests/local/conftest.py`` and ``pyproject.toml`` (``--ignore=tests/local``).

Run it:

    pip install -e '.[screenshot]'
    playwright install chromium
    pytest tests/local/ --override-ini='addopts=' -s

``--override-ini='addopts='`` resets the project's xdist + ignore
addopts so the singleton server fixture isn't fan-out-raced and so this
directory is actually collected.

Set ``PYIMGTAG_SCREENSHOT_DB`` to a path on your real progress DB to
walk the UI against your own data instead of the seeded fixtures.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

# Hard-skip if any of the moving parts isn't installed locally — the
# test must never half-run.
pytest.importorskip(
    "playwright.sync_api",
    reason="playwright not installed; run: pip install playwright && playwright install chromium",
)
pytest.importorskip("uvicorn", reason="install with: pip install 'pyimgtag[review]'")
pytest.importorskip("fastapi", reason="install with: pip install 'pyimgtag[review]'")

# Belt-and-braces: never run in CI even if a runner happens to have
# Chromium installed.
if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
    pytest.skip("local-only screenshot test", allow_module_level=True)

# ruff: noqa: E402 — these imports must come after the importorskip guard
from PIL import Image
from playwright.sync_api import sync_playwright

from pyimgtag.models import ImageResult, JudgeResult, JudgeScores
from pyimgtag.progress_db import ProgressDB
from pyimgtag.webapp.unified_app import create_unified_app

_RUN_DIR = Path(__file__).parent / "screenshots" / datetime.now().strftime("%Y%m%d-%H%M%S")


def _find_free_port() -> int:
    """Return an OS-assigned free TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _color_from_name(name: str) -> tuple[int, int, int]:
    """Deterministic per-name fill colour so each fixture image looks distinct."""
    h = abs(hash(name))
    return ((h % 200) + 30, ((h >> 8) % 200) + 30, ((h >> 16) % 200) + 30)


def _seed_db(tmp: Path) -> Path:
    """Populate a tmp progress DB with five images covering every render path.

    We deliberately seed:
      - one judged ``ok`` row → judge badge + Judge page have data
      - one ``review`` cleanup row → "Review" pill has hits
      - one ``delete`` cleanup row → "Delete" pill has hits
      - one row with ``has_text`` → Query "Has text" filter has hits
      - one ``error`` row → "Errors" pill + status filter have hits
    """
    db_path = tmp / "progress.db"
    samples = [
        # name, tags, summary, scene_cat, cleanup, city, country,
        # image_date (EXIF), has_text, status, judge?
        (
            "DSC00042.jpg",
            ["sunset", "beach"],
            "Golden-hour beach scene.",
            "outdoor_leisure",
            None,
            "San Francisco",
            "US",
            "2025-09-12T18:30:00",
            False,
            "ok",
            True,
        ),
        (
            "IMG_1001.jpg",
            ["dog", "park"],
            "Black labrador running through autumn leaves.",
            "outdoor_pets",
            "review",
            "Berlin",
            "DE",
            "2024-04-08T13:14:00",
            False,
            "ok",
            False,
        ),
        (
            "IMG_1002.jpg",
            ["cat", "indoor"],
            "Tabby cat napping on a couch.",
            "indoor_home",
            "delete",
            "Kyiv",
            "UA",
            "2023-11-30T09:00:00",
            False,
            "ok",
            False,
        ),
        (
            "IMG_1003.jpg",
            ["sign", "text"],
            "Street sign with caption.",
            "indoor_home",
            None,
            "Paris",
            "FR",
            "2024-08-21T11:11:11",
            True,
            "ok",
            False,
        ),
        (
            "IMG_1004.jpg",
            [],
            None,
            None,
            None,
            None,
            None,
            None,
            False,
            "error",
            False,
        ),
    ]

    with ProgressDB(db_path=db_path) as db:
        for (
            name,
            tags,
            summ,
            cat,
            cleanup,
            city,
            country,
            date,
            has_text,
            status,
            judged,
        ) in samples:
            img = tmp / name
            Image.new("RGB", (320, 240), color=_color_from_name(name)).save(str(img))
            db.mark_done(
                img,
                ImageResult(
                    file_path=str(img),
                    file_name=name,
                    tags=tags,
                    scene_summary=summ,
                    scene_category=cat,
                    cleanup_class=cleanup,
                    nearest_city=city,
                    nearest_country=country,
                    image_date=date,
                    has_text=has_text,
                    processing_status=status,
                    error_message=(
                        None if status == "ok" else "Could not parse JSON from model response"
                    ),
                ),
            )
            if judged:
                db.save_judge_result(
                    JudgeResult(
                        file_path=str(img),
                        file_name=name,
                        scores=JudgeScores(
                            impact=8,
                            story_subject=8,
                            composition_center=8,
                            lighting=7,
                            creativity_style=7,
                            color_mood=8,
                            presentation_crop=7,
                            technical_excellence=8,
                            focus_sharpness=8,
                            exposure_tonal=8,
                            noise_cleanliness=8,
                            subject_separation=7,
                            edit_integrity=8,
                            verdict="Solid frame.",
                            reason="Strong composition, clean exposure, "
                            "subject pops against the soft sky.",
                        ),
                        weighted_score=8,
                        core_score=8,
                        visible_score=7,
                    )
                )
    return db_path


@pytest.fixture(scope="module")
def server_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Boot uvicorn in a thread and yield its base URL.

    Honours ``PYIMGTAG_SCREENSHOT_DB`` so power users can walk the UI
    against their real DB instead of the seeded fixtures.
    """
    import uvicorn

    override = os.environ.get("PYIMGTAG_SCREENSHOT_DB")
    if override:
        db_path = Path(override).expanduser()
        if not db_path.exists():
            pytest.fail(f"PYIMGTAG_SCREENSHOT_DB does not exist: {db_path}")
    else:
        tmp = tmp_path_factory.mktemp("screenshots")
        db_path = _seed_db(tmp)

    app = create_unified_app(db_path=db_path)
    port = _find_free_port()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not getattr(server, "started", False):
        time.sleep(0.05)
    if not getattr(server, "started", False):
        pytest.fail("uvicorn never reached `started=True` within 10 s")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=3.0)


@pytest.fixture(scope="module")
def browser_ctx():
    """A single Chromium context shared across the module's tests."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        # Surface JS console errors next to the screenshot output so a
        # broken page is visible even though the screenshot still renders.
        ctx.on(
            "weberror",
            lambda err: print(f"[js error] {err.error}"),
        )
        yield ctx
        browser.close()


@pytest.fixture(scope="module", autouse=True)
def _announce_run_dir():
    _RUN_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n[screenshots] writing to {_RUN_DIR}")
    yield
    pngs = sorted(_RUN_DIR.glob("*.png"))
    print(f"\n[screenshots] {len(pngs)} captured → {_RUN_DIR}")


def _shoot(page, name: str) -> None:
    """Save a full-page PNG under the run directory."""
    path = _RUN_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)


def _open(browser_ctx, url: str):
    page = browser_ctx.new_page()
    page.goto(url)
    page.wait_for_load_state("networkidle")
    return page


# ---------------------------------------------------------------------------
# Page smoke: every top-level page must render and screenshot cleanly.
# ---------------------------------------------------------------------------


def test_dashboard(server_url: str, browser_ctx) -> None:
    page = _open(browser_ctx, server_url + "/")
    _shoot(page, "00-dashboard")
    page.close()


def test_review_default(server_url: str, browser_ctx) -> None:
    page = _open(browser_ctx, server_url + "/review/")
    _shoot(page, "01-review-default")
    page.close()


def test_review_each_pill(server_url: str, browser_ctx) -> None:
    """All / Delete / Review / Errors pills must each render their slice."""
    page = _open(browser_ctx, server_url + "/review/")
    pills = ("All", "Delete", "Review", "Errors")
    for i, label in enumerate(pills):
        page.locator(".pills button.pill", has_text=label).first.click()
        page.wait_for_timeout(400)
        _shoot(page, f"02-review-pill-{i:02d}-{label.lower()}")
    page.close()


def test_review_each_sort_option(server_url: str, browser_ctx) -> None:
    """Every Sort dropdown option in routes_review.py must accept a click."""
    page = _open(browser_ctx, server_url + "/review/")
    options = ("path_asc", "path_desc", "newest", "oldest", "name_asc", "name_desc")
    for i, val in enumerate(options):
        page.locator("#sortSel").select_option(val)
        page.wait_for_timeout(400)
        _shoot(page, f"03-review-sort-{i:02d}-{val}")
    page.close()


def test_review_each_per_page_option(server_url: str, browser_ctx) -> None:
    """The per-page selector exposes 25 / 50 / 100 / 200; shoot each."""
    page = _open(browser_ctx, server_url + "/review/")
    for v in ("25", "50", "100", "200"):
        page.locator("#pageSel").select_option(v)
        page.wait_for_timeout(400)
        _shoot(page, f"04-review-perpage-{v}")
    page.close()


def test_faces(server_url: str, browser_ctx) -> None:
    page = _open(browser_ctx, server_url + "/faces/")
    _shoot(page, "10-faces")
    page.close()


def test_tags(server_url: str, browser_ctx) -> None:
    page = _open(browser_ctx, server_url + "/tags/")
    _shoot(page, "20-tags")
    page.close()


def test_query_default_and_search(server_url: str, browser_ctx) -> None:
    page = _open(browser_ctx, server_url + "/query/")
    _shoot(page, "30-query-empty")
    page.locator("button.btn-primary", has_text="Search").click()
    page.wait_for_timeout(600)
    _shoot(page, "31-query-results")
    page.close()


def test_query_each_sort_option(server_url: str, browser_ctx) -> None:
    """Cover every Sort option on /query/ — including the new shot_* and judge_*."""
    page = _open(browser_ctx, server_url + "/query/")
    options = (
        "path_asc",
        "path_desc",
        "newest",
        "oldest",
        "shot_desc",
        "shot_asc",
        "judge_desc",
        "judge_asc",
    )
    for i, val in enumerate(options):
        page.locator("#f_sort").select_option(val)
        page.locator("button.btn-primary", has_text="Search").click()
        page.wait_for_timeout(500)
        _shoot(page, f"32-query-sort-{i:02d}-{val}")
    page.close()


def test_query_each_filter_select(server_url: str, browser_ctx) -> None:
    """Cover every value in each select-typed filter on /query/."""
    page = _open(browser_ctx, server_url + "/query/")
    select_filters = (
        ("#f_text", "Has text", ("true", "false")),
        ("#f_cleanup", "Cleanup", ("delete", "review", "keep")),
        ("#f_status", "Status", ("ok", "error")),
        ("#f_judged", "Judged", ("true", "false")),
    )
    for sel, label, values in select_filters:
        for v in values:
            page.locator(sel).select_option(v)
            page.locator("button.btn-primary", has_text="Search").click()
            page.wait_for_timeout(500)
            _shoot(page, f"33-query-filter-{label.lower().replace(' ', '_')}-{v}")
            # Reset for the next pass so filters don't compound.
            page.locator(sel).select_option("")
    page.close()


def test_query_hover_thumbnail(server_url: str, browser_ctx) -> None:
    """Hover the first row to surface the floating 280px thumbnail."""
    page = _open(browser_ctx, server_url + "/query/")
    page.locator("button.btn-primary", has_text="Search").click()
    page.wait_for_timeout(600)
    rows = page.locator("table.tbl tr.row")
    if rows.count() == 0:
        pytest.skip("seeded query had no result rows; nothing to hover")
    rows.first.hover()
    page.wait_for_timeout(600)
    _shoot(page, "34-query-hover-thumbnail")
    page.close()


def test_judge(server_url: str, browser_ctx) -> None:
    page = _open(browser_ctx, server_url + "/judge/")
    _shoot(page, "40-judge")
    page.close()


def test_about(server_url: str, browser_ctx) -> None:
    page = _open(browser_ctx, server_url + "/about/")
    _shoot(page, "50-about")
    page.close()


def test_edit(server_url: str, browser_ctx) -> None:
    """Capture every state of the destructive Edit page.

    The seeded DB has one ``cleanup_class='delete'`` row, so the page
    naturally renders an idle "1 marked" summary. We then flip through
    confirmed (checkbox ticked) and the running / done states by stubbing
    the JS network calls so the screenshots are deterministic and don't
    depend on the real AppleScript path. Resets the in-process Edit job
    singleton on entry so a previous run can't leak ``running`` state.
    """
    # The screenshot worker shares the unified-app process with every
    # other test, so the Edit job singleton may already be at ``done``
    # from an earlier session. Reset it explicitly so the live page
    # always starts at the idle state we expect.
    from pyimgtag.webapp import routes_edit

    routes_edit._reset_job_for_tests()

    page = _open(browser_ctx, server_url + "/edit/")
    page.wait_for_timeout(400)
    _shoot(page, "60-edit-idle")

    # Confirmed: checkbox ticked, button now enabled.
    page.locator("#confirmChk").check()
    page.wait_for_timeout(200)
    _shoot(page, "61-edit-confirmed")

    # Running state: route the run + status calls onto fake responses
    # so the panel renders deterministic progress instead of hitting
    # real Photos.app. Three poll cycles cover idle → running → done.
    poll_count = {"n": 0}

    def _route_status(route, request) -> None:  # noqa: ANN001 — playwright callbacks
        poll_count["n"] += 1
        if poll_count["n"] == 1:
            body = (
                '{"job_id":"x","state":"running","total":3,"done":1,"ok":1,'
                '"failed":0,"started_at":1.0,"finished_at":null,"last_error":null,'
                '"recent":[{"file_name":"DSC00042.jpg","status":"ok"}]}'
            )
        else:
            body = (
                '{"job_id":"x","state":"done","total":3,"done":3,"ok":2,'
                '"failed":1,"started_at":1.0,"finished_at":2.0,'
                '"last_error":"photos_unavailable","recent":['
                '{"file_name":"DSC00042.jpg","status":"ok"},'
                '{"file_name":"IMG_1002.jpg","status":"ok"},'
                '{"file_name":"IMG_1003.jpg","status":"error",'
                '"error":"photos_unavailable"}]}'
            )
        route.fulfill(status=200, content_type="application/json", body=body)

    def _route_run(route, request) -> None:  # noqa: ANN001
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok":true,"job_id":"x"}',
        )

    page.route("**/edit/api/status", _route_status)
    page.route("**/edit/api/run", _route_run)

    page.locator("#runBtn").click()
    # First poll → running.
    page.wait_for_timeout(1100)
    _shoot(page, "62-edit-running")
    # Second poll → done.
    page.wait_for_timeout(1100)
    _shoot(page, "63-edit-done")

    page.unroute("**/edit/api/status")
    page.unroute("**/edit/api/run")
    page.close()

    # Leave the singleton clean for any tests that follow.
    routes_edit._reset_job_for_tests()
