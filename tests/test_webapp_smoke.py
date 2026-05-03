"""End-to-end smoke tests for the unified pyimgtag webapp.

These run against an in-process FastAPI ``TestClient`` (no network, no
external services) and exercise every page and API endpoint the browser
actually hits. They are designed to catch the kinds of regressions the
v0.8.x cycle dealt with by hand:

- A page returning 5xx or 404.
- An HTML template shipped with literal ``__API_BASE__`` / ``__NAV__``
  placeholders unreplaced.
- An internal ``href`` / ``src`` link pointing at a route the app does
  not actually serve (a "dead link").
- An API endpoint silently dropping or renaming a field the JS reads,
  e.g. the ``tags`` vs ``tags_list`` regression that turned every chip
  into a single character.

Each test uses a fresh ``ProgressDB`` seeded with a few representative
rows so the page handlers all hit a "happy" path with data to render.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

# All four optional deps must be present — the CI ``test`` job installs
# the ``[review]`` extra. Skip the whole module if FastAPI is not
# installed locally so the suite still runs in minimal envs.
fastapi_testclient = pytest.importorskip("fastapi.testclient", reason="fastapi not installed")
TestClient = fastapi_testclient.TestClient

# ruff: noqa: E402 — these imports must come after the importorskip guard
from pyimgtag.models import ImageResult, JudgeResult, JudgeScores
from pyimgtag.progress_db import ProgressDB
from pyimgtag.webapp.unified_app import create_unified_app

_TEST_FILE_NAME = "DSC00042.jpg"


def _seed_db(db_path: Path, image_path: Path) -> None:
    """Populate one ok image, one error image, one judge row, one face row."""
    with ProgressDB(db_path=db_path) as db:
        db.mark_done(
            image_path,
            ImageResult(
                file_path=str(image_path),
                file_name=image_path.name,
                source_type="directory",
                tags=["sunset", "beach"],
                scene_summary="Golden-hour beach scene.",
                scene_category="outdoor_leisure",
                emotional_tone="positive",
                cleanup_class=None,
                has_text=False,
                event_hint="outing",
                significance="medium",
                processing_status="ok",
                nearest_city="San Francisco",
                nearest_country="US",
            ),
        )
        err_path = image_path.parent / "broken.jpg"
        err_path.write_bytes(b"\xff\xd8\xff\xe0not a real jpg")
        db.mark_done(
            err_path,
            ImageResult(
                file_path=str(err_path),
                file_name=err_path.name,
                processing_status="error",
                error_message="Could not parse JSON from model response: 'foo'",
            ),
        )
        db.save_judge_result(
            JudgeResult(
                file_path=str(image_path),
                file_name=image_path.name,
                scores=JudgeScores(
                    impact=8,
                    story_subject=7,
                    composition_center=8,
                    lighting=7,
                    creativity_style=6,
                    color_mood=8,
                    presentation_crop=7,
                    technical_excellence=8,
                    focus_sharpness=9,
                    exposure_tonal=7,
                    noise_cleanliness=8,
                    subject_separation=6,
                    edit_integrity=7,
                    verdict="Solid frame.",
                ),
                weighted_score=8,
                core_score=8,
                visible_score=7,
            )
        )


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    img = tmp_path / _TEST_FILE_NAME
    # Embed a tiny but real JPEG payload so /thumbnail and /original
    # actually have bytes to decode.
    from PIL import Image as _PIL

    _PIL.new("RGB", (16, 16), color=(64, 128, 192)).save(str(img))
    db_path = tmp_path / "progress.db"
    _seed_db(db_path, img)
    app = create_unified_app(db_path=db_path)
    with TestClient(app) as c:
        c.test_image_path = str(img)  # type: ignore[attr-defined]
        yield c


# ---------------------------------------------------------------------------
# Smoke: every page + JSON API endpoint returns 2xx
# ---------------------------------------------------------------------------


_PAGES = [
    "/",
    "/review/",
    "/faces/",
    "/tags/",
    "/query/",
    "/judge/",
    "/edit/",
    "/about/",
]

_JSON_APIS = [
    ("/api/run/current", []),  # dashboard
    ("/review/api/stats", []),
    ("/review/api/images", []),
    ("/tags/api/tags", []),
    ("/query/api/images", []),
    ("/judge/api/scores", []),
    ("/faces/api/persons", []),
    ("/edit/api/marked", []),
    ("/edit/api/status", []),
    ("/edit/api/drift", []),
    ("/about/api/version", []),
]


class TestPageSmoke:
    @pytest.mark.parametrize("path", _PAGES)
    def test_page_returns_html(self, client: TestClient, path: str) -> None:
        r = client.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}: {r.text[:200]}"
        assert r.headers["content-type"].startswith("text/html"), path
        assert "<html" in r.text.lower(), path

    @pytest.mark.parametrize("path,_q", _JSON_APIS)
    def test_api_returns_2xx_json(self, client: TestClient, path: str, _q: list) -> None:
        r = client.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}: {r.text[:200]}"
        assert r.headers["content-type"].startswith("application/json"), path
        # Must parse — body is shape-checked in TestApiContracts below.
        r.json()


# ---------------------------------------------------------------------------
# HTML structure: no template placeholders shipped to the browser
# ---------------------------------------------------------------------------


class TestNoUnreplacedPlaceholders:
    """Every HTML template performs string substitution for ``__NAV__``,
    ``__NAV_STYLES__``, ``__API_BASE__``, etc. before serving. A leftover
    ``__FOO__`` token in the response means the substitution drifted out
    of sync with the template — caught here before it reaches a user."""

    @pytest.mark.parametrize("path", _PAGES)
    def test_no_double_underscore_macros(self, client: TestClient, path: str) -> None:
        r = client.get(path)
        leftovers = re.findall(r"__[A-Z][A-Z0-9_]+__", r.text)
        assert not leftovers, (
            f"{path} returned HTML with unreplaced template tokens: {set(leftovers)}"
        )

    def test_judge_html_has_review_style_toolbar(self, client: TestClient) -> None:
        """The Judge page is rebuilt as a Review-style grid + pager. The
        toolbar IDs (``minRating`` / ``maxRating`` / ``sortSel`` /
        ``pageSel``) and the ``#grid`` container are load-bearing for the
        JS — pin them so a future refactor that drops one of them blows
        up here instead of silently rendering a blank page."""
        r = client.get("/judge/")
        assert r.status_code == 200
        for token in (
            'id="minRating"',
            'id="maxRating"',
            'id="sortSel"',
            'id="pageSel"',
            'id="grid"',
            'id="prevBtn"',
            'id="nextBtn"',
            'id="pageInfo"',
            'id="lightbox"',
            "rating_desc",
            "rating_asc",
            "path_asc",
            "shot_desc",
        ):
            assert token in r.text, f"judge HTML missing {token!r}"


# ---------------------------------------------------------------------------
# Dead-link detection: every same-origin href / src on every page must
# resolve to a 2xx (or a 4xx that's expected — e.g. /thumbnail wants a
# real path).
# ---------------------------------------------------------------------------


_HREF_RE = re.compile(r'href="([^"]+)"')


def _normalise(href: str) -> str:
    """Strip query string + anchor for the route-existence check."""
    href = href.split("#", 1)[0]
    href = href.split("?", 1)[0]
    return href


def _is_internal(href: str) -> bool:
    if not href:
        return False
    if href.startswith(("http://", "https://", "mailto:", "javascript:")):
        return False
    if href.startswith("#"):
        return False
    return True


class TestNoDeadInternalLinks:
    """Crawl every shipped page, extract \"href=\\"…\\"\" attributes, and
    GET each internal one. Catches navigation drift like a sidebar item
    pointing at a route that has been renamed or removed."""

    @pytest.mark.parametrize("path", _PAGES)
    def test_internal_links_resolve(self, client: TestClient, path: str) -> None:
        r = client.get(path)
        assert r.status_code == 200
        seen: set[str] = set()
        for raw in _HREF_RE.findall(r.text):
            if not _is_internal(raw):
                continue
            target = _normalise(raw)
            if not target or target in seen:
                continue
            seen.add(target)
            sub = client.get(target)
            assert sub.status_code < 400, (
                f"{path} links to {target!r} which returned {sub.status_code}"
            )


# ---------------------------------------------------------------------------
# API field contracts: shapes the JS depends on are stable.
#
# Every key listed here is referenced from at least one renderer in
# routes_*.py; if a backend rename drops one, the JS silently renders
# blank cells and the user sees ghost cards. The list is intentionally
# tight so adding a new optional key doesn't break the test.
# ---------------------------------------------------------------------------


class TestApiContracts:
    def test_review_api_stats_has_total_and_error(self, client: TestClient) -> None:
        d = client.get("/review/api/stats").json()
        assert "total" in d and "error" in d
        assert isinstance(d["total"], int)
        assert isinstance(d["error"], int)

    def test_review_api_images_item_shape(self, client: TestClient) -> None:
        d = client.get("/review/api/images").json()
        assert "items" in d and "total" in d
        assert isinstance(d["items"], list)
        assert d["items"], "seeded DB should have at least one image"
        item = d["items"][0]
        for key in (
            "file_path",
            "file_name",
            "tags",
            "tags_list",
            "scene_summary",
            "status",
            "cleanup_class",
            "scene_category",
            "error_message",
            # Pulled from the LEFT JOIN with judge_scores; ``None`` for
            # un-judged images, integer 1–10 otherwise. The review card
            # renders a corner badge when it is set.
            "judge_score",
            "judge_verdict",
        ):
            assert key in item, f"review item missing {key}"
        # Regression for the bug where ``tags`` was a JSON string and the
        # JS rendered each character as its own chip.
        assert isinstance(item["tags"], list)
        # The seeded image was judged with weighted_score=8; that should
        # come back through the review API.
        judged = next((i for i in d["items"] if i["judge_score"] is not None), None)
        assert judged is not None, "expected the seeded judged image to surface"
        assert judged["judge_score"] == 8
        assert judged["judge_verdict"] == "Solid frame."

    def test_review_api_images_file_param_returns_single(self, client: TestClient) -> None:
        path = client.test_image_path  # type: ignore[attr-defined]
        d = client.get("/review/api/images", params={"file": path}).json()
        assert d["total"] == 1
        assert len(d["items"]) == 1
        assert d["items"][0]["file_path"] == path

    def test_review_api_images_unknown_file_is_empty(self, client: TestClient) -> None:
        d = client.get("/review/api/images", params={"file": "/no/such/file.jpg"}).json()
        assert d["total"] == 0
        assert d["items"] == []

    def test_review_api_images_sort_param_accepted(self, client: TestClient) -> None:
        # Whitelisted sort keys must all 200 — the JS sort dropdown emits
        # exactly these values.
        for s in ("path_asc", "path_desc", "newest", "oldest", "name_asc", "name_desc"):
            r = client.get("/review/api/images", params={"sort": s})
            assert r.status_code == 200, s

    def test_review_api_images_error_filter(self, client: TestClient) -> None:
        d = client.get("/review/api/images", params={"status": "error"}).json()
        assert d["total"] >= 1
        assert all(it["status"] == "error" for it in d["items"])
        # The seeded error row carries an error_message — the review UI
        # renders this on the card.
        assert any(it.get("error_message") for it in d["items"])

    def test_query_api_images_shape(self, client: TestClient) -> None:
        d = client.get("/query/api/images").json()
        assert isinstance(d, list)
        assert d, "seeded DB should have at least one image"
        item = d[0]
        for key in (
            "file_path",
            "file_name",
            "tags_list",
            "status",
            "scene_category",
            "cleanup_class",
            "nearest_city",
            "nearest_country",
            "error_message",
            # Query LEFT JOINs judge_scores so the JS can render the
            # judge column and hover-thumbnail tooltip.
            "judge_score",
            "judge_reason",
            "judge_verdict",
        ):
            assert key in item, f"query item missing {key}"

    def test_query_api_judge_filter_min(self, client: TestClient) -> None:
        # The seeded image has weighted_score=8 — min_judge_score=8 keeps it,
        # min_judge_score=9 should exclude it.
        kept = client.get("/query/api/images", params={"min_judge_score": 8}).json()
        assert any(it["judge_score"] == 8 for it in kept)
        excluded = client.get("/query/api/images", params={"min_judge_score": 9}).json()
        assert not any(it["judge_score"] == 8 for it in excluded)

    def test_query_api_sort_judge_desc_accepted(self, client: TestClient) -> None:
        for s in (
            "path_asc",
            "path_desc",
            "newest",
            "oldest",
            "judge_asc",
            "judge_desc",
            "shot_asc",
            "shot_desc",
        ):
            r = client.get("/query/api/images", params={"sort": s})
            assert r.status_code == 200, s

    def test_query_api_image_date_field_present(self, client: TestClient) -> None:
        """Every Query result row must carry image_date so the JS can
        render the Date column. The seeded image has no EXIF capture so
        the field is None — that's the contract: the key is always there
        and the JS falls back to an em-dash."""
        d = client.get("/query/api/images").json()
        assert d, "seeded DB should have at least one image"
        assert "image_date" in d[0]

    def test_query_api_judged_filter(self, client: TestClient) -> None:
        only_judged = client.get("/query/api/images", params={"judged": "true"}).json()
        assert all(it["judge_score"] is not None for it in only_judged)
        not_judged = client.get("/query/api/images", params={"judged": "false"}).json()
        assert all(it["judge_score"] is None for it in not_judged)

    def test_judge_api_scores_shape(self, client: TestClient) -> None:
        # 0.13.0 contract: paginated ``{items, total}`` so the Judge UI
        # can render Prev / Next + a live count, mirroring the Review API.
        d = client.get("/judge/api/scores").json()
        assert isinstance(d, dict)
        assert "items" in d and "total" in d
        assert isinstance(d["items"], list)
        assert isinstance(d["total"], int)
        assert d["items"], "seeded DB should have at least one judged image"
        score = d["items"][0]
        for key in (
            "file_path",
            "file_name",
            "weighted_score",
            "reason",
            "verdict",
            "image_date",
            "scene_summary",
            "nearest_city",
            "nearest_country",
            "cleanup_class",
        ):
            assert key in score, f"judge item missing {key}"
        # 0.8.0 contract: integer scale.
        assert isinstance(score["weighted_score"], int)

    def test_judge_api_scores_pagination(self, client: TestClient) -> None:
        """``offset`` / ``limit`` paginate the result; total stays stable."""
        full = client.get("/judge/api/scores").json()
        windowed = client.get("/judge/api/scores", params={"offset": 0, "limit": 1}).json()
        assert windowed["total"] == full["total"]
        assert len(windowed["items"]) <= 1
        # ``limit`` is capped at 200.
        too_big = client.get("/judge/api/scores", params={"limit": 999})
        assert too_big.status_code == 422

    def test_judge_api_scores_sort_options(self, client: TestClient) -> None:
        """Every Sort dropdown value must be accepted by the backend."""
        for s in ("rating_desc", "rating_asc", "path_asc", "path_desc", "shot_desc", "shot_asc"):
            r = client.get("/judge/api/scores", params={"sort": s})
            assert r.status_code == 200, s
            d = r.json()
            assert "items" in d and "total" in d, s

    def test_judge_api_scores_min_rating_filters(self, client: TestClient) -> None:
        """The seeded image is rated 8 — ``min_rating=9`` excludes it,
        ``min_rating=8`` keeps it. Out-of-range values clamp silently."""
        kept = client.get("/judge/api/scores", params={"min_rating": 8}).json()
        assert kept["total"] >= 1
        excluded = client.get("/judge/api/scores", params={"min_rating": 9}).json()
        assert excluded["total"] == 0
        # Out-of-range values clamp rather than 4xx — ``min_rating=99``
        # collapses to ``10`` which excludes the seeded 8/10 row.
        clamped = client.get("/judge/api/scores", params={"min_rating": 99}).json()
        assert clamped["total"] == 0

    def test_judge_api_scores_max_rating_filters(self, client: TestClient) -> None:
        kept = client.get("/judge/api/scores", params={"max_rating": 8}).json()
        assert kept["total"] >= 1
        excluded = client.get("/judge/api/scores", params={"max_rating": 7}).json()
        assert excluded["total"] == 0

    def test_tags_api_returns_list(self, client: TestClient) -> None:
        d = client.get("/tags/api/tags").json()
        assert isinstance(d, list)
        # The seeded image carries two tags; both should surface here.
        names = {t["tag"] for t in d}
        assert {"sunset", "beach"}.issubset(names)


# ---------------------------------------------------------------------------
# Thumbnail / original endpoints: respond with 404 for unknown paths and
# 200 + image content for the seeded one.
# ---------------------------------------------------------------------------


class TestThumbnailAndOriginal:
    def test_thumbnail_unknown_path_404(self, client: TestClient) -> None:
        r = client.get("/review/thumbnail", params={"path": "/not/in/db.jpg"})
        assert r.status_code == 404

    def test_thumbnail_known_path_2xx(self, client: TestClient) -> None:
        r = client.get(
            "/review/thumbnail",
            params={"path": client.test_image_path, "size": 200},  # type: ignore[attr-defined]
        )
        assert r.status_code == 200, r.text[:200]
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content[:3] == b"\xff\xd8\xff", "must be JPEG bytes"

    def test_original_known_path_2xx(self, client: TestClient) -> None:
        r = client.get(
            "/review/original",
            params={"path": client.test_image_path},  # type: ignore[attr-defined]
        )
        assert r.status_code == 200
        # Seeded JPEG → JPEG bytes.
        assert r.headers["content-type"].startswith("image/")


class TestOpenInPhotos:
    """``POST /review/api/open-in-photos`` looks the path up in the DB and
    delegates to ``reveal_in_photos``. Unknown paths return ``ok=False``;
    a successful AppleScript (mocked) returns ``ok=True``; an error from
    the AppleScript layer is mapped to a stable client-facing category
    (the verbose stderr stays server-side in the log) so the JS can
    branch on it without leaking osascript line/column references."""

    def test_open_in_photos_unknown_path(self, client: TestClient) -> None:
        r = client.post(
            "/review/api/open-in-photos",
            params={"path": "/not/in/db.jpg"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert d["error"] == "image_not_found"

    def test_open_in_photos_success(self, client: TestClient) -> None:
        from unittest.mock import patch

        with patch(
            "pyimgtag.applescript_writer.reveal_in_photos",
            return_value=None,
        ):
            r = client.post(
                "/review/api/open-in-photos",
                params={"path": client.test_image_path},  # type: ignore[attr-defined]
            )
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_open_in_photos_maps_timeout_to_category(self, client: TestClient) -> None:
        """A verbose AppleScript error is collapsed onto a stable label
        — the raw stderr never reaches the browser."""
        from unittest.mock import patch

        with patch(
            "pyimgtag.applescript_writer.reveal_in_photos",
            return_value="osascript timed out while revealing photo",
        ):
            r = client.post(
                "/review/api/open-in-photos",
                params={"path": client.test_image_path},  # type: ignore[attr-defined]
            )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert d["error"] == "photos_timeout"

    def test_open_in_photos_maps_macos_message_to_category(self, client: TestClient) -> None:
        from unittest.mock import patch

        with patch(
            "pyimgtag.applescript_writer.reveal_in_photos",
            return_value="Apple Photos reveal is only available on macOS",
        ):
            r = client.post(
                "/review/api/open-in-photos",
                params={"path": client.test_image_path},  # type: ignore[attr-defined]
            )
        assert r.status_code == 200
        assert r.json()["error"] == "platform_unsupported"

    def test_open_in_photos_unknown_error_falls_back(self, client: TestClient) -> None:
        from unittest.mock import patch

        with patch(
            "pyimgtag.applescript_writer.reveal_in_photos",
            return_value="something deeply weird at line 42 column 17",
        ):
            r = client.post(
                "/review/api/open-in-photos",
                params={"path": client.test_image_path},  # type: ignore[attr-defined]
            )
        assert r.status_code == 200
        # Stable unknown-error label — no osascript stderr leaks through.
        assert r.json()["error"] == "photos_error"


class TestAboutPage:
    """About page must load on every request and the version-check API
    must always return a JSON body even when PyPI is unreachable."""

    def test_about_html_renders(self, client: TestClient) -> None:
        r = client.get("/about/")
        assert r.status_code == 200
        assert "About pyimgtag" in r.text
        # The current version must appear in the rendered HTML so the
        # user can see at a glance which build is running.
        from pyimgtag import __version__

        assert __version__ in r.text

    def test_about_version_api_offline_friendly(self, client: TestClient) -> None:
        # Force a clean cache so the endpoint actually attempts a lookup.
        from pyimgtag.webapp import routes_about

        routes_about._CACHE.update({"at": 0.0, "value": None})
        # Patch the requests call to simulate "no network".
        from unittest.mock import patch

        with patch.object(routes_about, "_fetch_latest_pypi", return_value=None):
            d = client.get("/about/api/version").json()
        from pyimgtag import __version__

        assert d["installed"] == __version__
        assert d["latest"] is None
        assert d["update"] is False

    def test_about_version_api_flags_update(self, client: TestClient) -> None:
        from pyimgtag.webapp import routes_about

        routes_about._CACHE.update({"at": 0.0, "value": None})
        from unittest.mock import patch

        # Simulate a fresh PyPI release strictly newer than what we ship.
        from pyimgtag import __version__

        bumped_major = str(int(__version__.split(".")[0]) + 9) + ".0.0"
        with patch.object(routes_about, "_fetch_latest_pypi", return_value=bumped_major):
            d = client.get("/about/api/version").json()
        assert d["latest"] == bumped_major
        assert d["update"] is True

    def test_about_wiki_buttons_have_specific_color_rules(self, client: TestClient) -> None:
        """Regression: the primary "Open wiki" button rendered as a blank
        blue rectangle because the page-scoped ``.about a {color:var(--accent)}``
        rule (specificity 1,1) overrode the unscoped ``.wiki-btn {color:#fff}``
        rule (specificity 1,0), so the white text became the same blue as the
        background. Pin the scoped form so a future de-scope of these rules
        fails this test instead of silently shipping invisible buttons."""
        r = client.get("/about/")
        assert r.status_code == 200
        # Both buttons must use the page-scoped form. The unscoped ``.wiki-btn``
        # selector must not appear standalone — only as ``.about .wiki-btn``.
        assert ".about .wiki-btn{" in r.text or ".about .wiki-btn {" in r.text
        # The primary button must explicitly assert white text.
        assert "color:#fff" in r.text
        # And the markup must still carry the readable label so the button
        # is never blank even if a future user-agent style overrides the
        # accent colour.
        assert "Open wiki" in r.text
        assert "Use-case diagrams" in r.text


class TestDashboardSharesCliDb:
    """Regression: ``start_dashboard_for`` used to instantiate the unified
    webapp without a ``db_path``, so the dashboard opened the default
    ``~/.cache/pyimgtag/progress.db`` while the CLI worker may have been
    writing to a different DB. Users saw "0 scored" on /judge while the
    CLI was happily printing scores. The dashboard must thread
    ``args.db`` through to ``create_unified_app`` so worker and webapp
    share the same SQLite."""

    def test_create_unified_app_accepts_db_path(self, tmp_path) -> None:
        from pyimgtag.webapp.unified_app import create_unified_app

        db = tmp_path / "shared.db"
        # Constructing the app should create the DB file as a side
        # effect — proving the supplied path actually wins over the
        # default location.
        create_unified_app(db_path=db)
        assert db.exists()

    def test_start_dashboard_for_passes_db(self, tmp_path) -> None:
        """``start_dashboard_for`` must hand the namespace's ``db`` value
        to ``create_unified_app``."""
        import argparse
        from unittest.mock import patch

        from pyimgtag.webapp import bootstrap

        db = tmp_path / "from-cli.db"
        ns = argparse.Namespace(
            db=str(db),
            web=False,
            no_web=False,
            web_host="127.0.0.1",
            web_port=8770,
            no_browser=True,
        )

        captured: dict = {}

        def _fake_app(*, db_path):
            captured["db_path"] = db_path
            # Touch the file so the assertion below holds independent of
            # the patch behaviour.
            from pathlib import Path as _P

            _P(db_path).parent.mkdir(parents=True, exist_ok=True)
            _P(db_path).touch()

            class _StubApp:  # uvicorn.Server is never started in this test
                pass

            return _StubApp()

        class _StubServer:
            def __init__(self, *_a, **_kw) -> None:
                self.url = "http://localhost:0"

            def start(self) -> bool:
                return True

        with (
            patch("pyimgtag.webapp.unified_app.create_unified_app", new=_fake_app),
            patch("pyimgtag.webapp.server_thread.DashboardServer", new=_StubServer),
        ):
            session, dashboard = bootstrap.start_dashboard_for(ns, command="judge")

        assert captured["db_path"] == str(db), (
            "start_dashboard_for must hand args.db through to the webapp"
        )
        assert session is not None and dashboard is not None


class TestVersionParse:
    def test_parse_basic_versions(self) -> None:
        from pyimgtag.webapp.routes_about import _is_newer, _parse_version

        assert _parse_version("0.10.0") == (0, 10, 0)
        assert _parse_version("0.9.0") == (0, 9, 0)
        # 0.10.0 is newer than 0.9.0 — the classic mistake when comparing
        # versions as plain strings.
        assert _is_newer("0.10.0", "0.9.0") is True
        assert _is_newer("0.9.0", "0.10.0") is False
        assert _is_newer("1.0.0", "0.99.99") is True

    def test_parse_tolerates_suffixes(self) -> None:
        from pyimgtag.webapp.routes_about import _is_newer, _parse_version

        # Pre-release / dev suffixes get parsed conservatively rather
        # than crashing the compare.
        assert _parse_version("1.2.3rc1") == (1, 2, 3)
        assert _is_newer("1.2.3rc1", "1.2.3") is False


# ---------------------------------------------------------------------------
# Edit page: nav, marked-count, confirmation, job lifecycle, error mapping.
# Each test that pokes the run job singleton resets it explicitly so a flake
# in one case can't leak "running" state into the next.
# ---------------------------------------------------------------------------


def _seed_edit_db(db_path: Path, tmp_path: Path) -> tuple[str, str, str]:
    """Seed three rows: two delete-marked, one untouched. Returns the paths."""
    from PIL import Image as _PIL

    paths: list[str] = []
    for i, name in enumerate(["delete_a.jpg", "delete_b.jpg", "keep.jpg"]):
        p = tmp_path / name
        _PIL.new("RGB", (8, 8), color=(i * 30, 64, 64)).save(str(p))
        paths.append(str(p))
    cleanups = ["delete", "delete", None]
    with ProgressDB(db_path=db_path) as db:
        for path, cleanup in zip(paths, cleanups, strict=True):
            db.mark_done(
                Path(path),
                ImageResult(
                    file_path=path,
                    file_name=Path(path).name,
                    source_type="directory",
                    tags=["x"],
                    scene_summary="seed",
                    cleanup_class=cleanup,
                    processing_status="ok",
                ),
            )
    return paths[0], paths[1], paths[2]


@pytest.fixture()
def edit_client(tmp_path: Path) -> Iterator[TestClient]:
    """Dedicated client fixture seeded with two delete-marked + one keep row.

    The Edit job is a process-wide singleton — reset its state on
    fixture teardown so back-to-back tests don't observe leaked
    ``running`` state.
    """
    db_path = tmp_path / "edit.db"
    a, b, k = _seed_edit_db(db_path, tmp_path)
    app = create_unified_app(db_path=db_path)
    from pyimgtag.webapp import routes_edit

    routes_edit._reset_job_for_tests()
    with TestClient(app) as c:
        c.delete_paths = [a, b]  # type: ignore[attr-defined]
        c.keep_path = k  # type: ignore[attr-defined]
        c.db_path = db_path  # type: ignore[attr-defined]
        yield c
    routes_edit._reset_job_for_tests()


class TestEditPage:
    def test_nav_has_edit_entry_pointing_at_edit(self, client: TestClient) -> None:
        """Every page must surface the new Edit link in the top nav."""
        r = client.get("/")
        assert r.status_code == 200
        assert 'href="/edit"' in r.text, "nav must include /edit link"

    def test_edit_page_renders(self, edit_client: TestClient) -> None:
        r = edit_client.get("/edit/")
        assert r.status_code == 200
        # The placeholder substitution is exercised by
        # TestNoUnreplacedPlaceholders, but spot-check that the page
        # mentions the destructive action and the recovery window.
        assert "Recently Deleted" in r.text
        assert "cleanup" in r.text.lower()

    def test_marked_endpoint_counts_delete_rows(self, edit_client: TestClient) -> None:
        """The seeded DB has two delete-marked rows + one keep row."""
        d = edit_client.get("/edit/api/marked").json()
        assert d["count"] == 2
        sample_names = set(d["sample"])
        assert sample_names == {"delete_a.jpg", "delete_b.jpg"}

    def test_run_rejects_missing_confirmation(self, edit_client: TestClient) -> None:
        r = edit_client.post("/edit/api/run", json={})
        assert r.status_code == 400
        assert r.json()["error"] == "confirmation_required"

    def test_run_rejects_explicit_false(self, edit_client: TestClient) -> None:
        r = edit_client.post("/edit/api/run", json={"confirm": False})
        assert r.status_code == 400
        assert r.json()["error"] == "confirmation_required"

    def test_run_completes_and_maps_errors_to_categories(self, edit_client: TestClient) -> None:
        """One success + one mocked AppleScript failure.

        Asserts the final job state is ``done`` with ``ok=1`` /
        ``failed=1`` and that the failed event's ``error`` field is the
        stable category string (not the verbose AppleScript stderr).
        Also verifies a successful Photos delete removes the row from
        the progress DB so a re-scan won't re-process the now-trashed
        image.
        """
        from unittest.mock import patch

        from pyimgtag.progress_db import ProgressDB

        delete_paths = edit_client.delete_paths  # type: ignore[attr-defined]

        # First call returns success (None), second call returns a verbose
        # AppleScript-style error so we can prove the category mapping.
        side_effects = iter([None, "AppleScript error (exit 1): osascript reported a glitch"])

        def _fake_delete(_path: str) -> str | None:
            return next(side_effects)

        with patch("pyimgtag.applescript_writer.delete_from_photos", side_effect=_fake_delete):
            r = edit_client.post("/edit/api/run", json={"confirm": True})
            assert r.status_code == 200, r.text
            assert r.json()["ok"] is True

            # Wait for the worker thread to finish — keep the budget
            # generous but bounded so a hung job fails the test cleanly.
            for _ in range(50):
                d = edit_client.get("/edit/api/status").json()
                if d["state"] in ("done", "error"):
                    break
                time.sleep(0.05)

        d = edit_client.get("/edit/api/status").json()
        assert d["state"] == "done", d
        assert d["total"] == 2
        assert d["done"] == 2
        assert d["ok"] == 1
        assert d["failed"] == 1
        # Every event must carry a stable category, never the raw stderr.
        errored = [e for e in d["recent"] if e["status"] == "error"]
        assert len(errored) == 1
        assert errored[0]["error"] == "photos_unavailable"
        assert "osascript" not in errored[0]["error"]

        # The successful row must have been removed from the DB.
        with ProgressDB(db_path=edit_client.db_path) as db:  # type: ignore[attr-defined]
            assert db.get_image(delete_paths[0]) is None
            # The failed row stays put so the user can retry.
            assert db.get_image(delete_paths[1]) is not None
            # The keep row was never a target.
            assert db.get_image(edit_client.keep_path) is not None  # type: ignore[attr-defined]

    def test_run_rejects_overlapping_jobs(self, edit_client: TestClient) -> None:
        """Force a ``running`` singleton and assert the second POST 400s."""
        from pyimgtag.webapp import routes_edit

        # Synthesise a fake "in flight" job — we never start the real
        # worker, so the test is fast and deterministic.
        routes_edit._JOB = routes_edit._Job(job_id="held", state="running")
        try:
            r = edit_client.post("/edit/api/run", json={"confirm": True})
            assert r.status_code == 400
            assert r.json()["error"] == "job_already_running"
        finally:
            routes_edit._reset_job_for_tests()


class TestEditCategoriseApplescriptError:
    """Direct unit-tests of ``_categorise_applescript_error`` so each
    failure mode of the new UI-scripting delete path is pinned to a
    stable category. The browser must never see the raw osascript
    stderr — only one of the labels asserted below."""

    def test_macos_marker_routes_to_platform_unsupported(self) -> None:
        from pyimgtag.webapp.routes_edit import _categorise_applescript_error

        assert (
            _categorise_applescript_error("Apple Photos delete is only available on macOS")
            == "platform_unsupported"
        )

    def test_timeout_marker_routes_to_photos_timeout(self) -> None:
        from pyimgtag.webapp.routes_edit import _categorise_applescript_error

        assert (
            _categorise_applescript_error("osascript timed out while deleting photo")
            == "photos_timeout"
        )

    def test_accessibility_denied_signals_route_to_dedicated_category(self) -> None:
        """System Events surfaces accessibility-denied as ``(-1719)`` /
        ``(-25204)`` in the osascript stderr; those must not collapse
        into the generic ``photos_unavailable`` because the user-visible
        next step is different (grant Accessibility, not "is Photos.app
        installed?")."""
        from pyimgtag.webapp.routes_edit import _categorise_applescript_error

        e1 = (
            "AppleScript error (exit 1): System Events got an error: "
            "osascript is not allowed assistive access. (-1719)"
        )
        e2 = (
            "AppleScript error (exit 1): System Events got an error: "
            'Can\'t get process "Photos". (-25204)'
        )
        e3 = "AppleScript error: assistive access not allowed"
        assert _categorise_applescript_error(e1) == "accessibility_denied"
        assert _categorise_applescript_error(e2) == "accessibility_denied"
        assert _categorise_applescript_error(e3) == "accessibility_denied"

    def test_photo_not_found_routes_to_photo_not_in_library(self) -> None:
        """Photo file sits on disk inside the Photos library bundle but
        Photos.app no longer indexes it (deleted from Photos manually,
        orphaned original, etc.). Our own filename-scan AppleScript
        raises ``error "Photo not found: <name>"`` (-2700) in that
        case; surface a dedicated category so the dashboard renders
        the right next-step rather than the misleading
        ``photos_unavailable``."""
        from pyimgtag.webapp.routes_edit import _categorise_applescript_error

        e1 = (
            "AppleScript error (exit 1): 298:357: execution error: "
            "Photo not found: 0110B5A5-C112-4F30-A21D-CBB99BBA3985.png (-2700)"
        )
        e2 = "AppleScript error (exit 1): Photo not found: weird.jpg"
        e3 = "AppleScript error: (-2700)"
        assert _categorise_applescript_error(e1) == "photo_not_in_library"
        assert _categorise_applescript_error(e2) == "photo_not_in_library"
        assert _categorise_applescript_error(e3) == "photo_not_in_library"

    def test_generic_applescript_error_routes_to_photos_unavailable(self) -> None:
        from pyimgtag.webapp.routes_edit import _categorise_applescript_error

        assert (
            _categorise_applescript_error("AppleScript error (exit 1): osascript reported a glitch")
            == "photos_unavailable"
        )

    def test_unknown_text_routes_to_photos_error(self) -> None:
        from pyimgtag.webapp.routes_edit import _categorise_applescript_error

        assert _categorise_applescript_error("something deeply weird") == "photos_error"


# ---------------------------------------------------------------------------
# Edit page: DB drift cleanup panel.
#
# The drift scan is platform-agnostic for the disk-presence check, but the
# Photos.app probe needs mocking out so CI on Linux runners exercises the
# same code path as macOS. Each test forces the probe to return ``None``
# (degraded) so only ``disk_missing`` rows are detected — that's what
# matters for the API contract anyway.
# ---------------------------------------------------------------------------


def _seed_drift_db(db_path: Path, tmp_path: Path) -> tuple[str, str]:
    """Insert one ``present`` row and one ``disk_missing`` row.

    Returns (present_path, disk_missing_path) so callers can assert which
    row was pruned. The ``disk_missing`` file is written then unlinked
    so the row exists in the DB but the file is gone.
    """
    from PIL import Image as _PIL

    present = tmp_path / "drift_present.jpg"
    _PIL.new("RGB", (8, 8), color=(80, 100, 120)).save(str(present))
    disk_missing = tmp_path / "drift_gone.jpg"
    _PIL.new("RGB", (8, 8), color=(120, 100, 80)).save(str(disk_missing))

    with ProgressDB(db_path=db_path) as db:
        for p in (present, disk_missing):
            db.mark_done(
                p,
                ImageResult(
                    file_path=str(p),
                    file_name=p.name,
                    source_type="directory",
                    tags=["x"],
                    scene_summary="seed",
                    processing_status="ok",
                ),
            )

    disk_missing.unlink()
    return str(present), str(disk_missing)


@pytest.fixture()
def drift_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Edit fixture seeded with one present + one disk_missing row.

    Mocks the bulk Photos probe to return ``("parse_error")`` so the
    test runs on every OS and only the disk-presence signal is used.
    """
    db_path = tmp_path / "drift.db"
    present, gone = _seed_drift_db(db_path, tmp_path)

    def _fake_probe() -> tuple[set[str], str | None]:
        # Empty set + an error string forces the scanner into the
        # disk-only branch. The on-disk row collapses into ``present``;
        # the deleted file is still flagged as ``disk_missing``.
        return set(), "parse_error"

    monkeypatch.setattr("pyimgtag.cleanup_drift.fetch_photos_membership", _fake_probe)

    app = create_unified_app(db_path=db_path)
    from pyimgtag.webapp import routes_edit

    routes_edit._reset_job_for_tests()
    with TestClient(app) as c:
        c.present_path = present  # type: ignore[attr-defined]
        c.gone_path = gone  # type: ignore[attr-defined]
        c.db_path = db_path  # type: ignore[attr-defined]
        yield c
    routes_edit._reset_job_for_tests()


class TestEditDriftPanel:
    def test_drift_endpoint_shape(self, drift_client: TestClient) -> None:
        d = drift_client.get("/edit/api/drift").json()
        for key in ("total", "disk_missing", "photos_missing", "sample"):
            assert key in d, f"/edit/api/drift response missing {key!r}: {d}"
        assert d["total"] == 2
        assert d["disk_missing"] == 1
        # Probe is forced into the degraded path; ``photos_missing``
        # cannot be inferred without a usable Photos membership map.
        assert d["photos_missing"] == 0
        # The sample lists the dead path so the UI can render a
        # preview without a second round-trip.
        assert drift_client.gone_path in d["sample"]  # type: ignore[attr-defined]

    def test_prune_drift_rejects_missing_confirmation(self, drift_client: TestClient) -> None:
        r = drift_client.post("/edit/api/prune-drift", json={})
        assert r.status_code == 400
        assert r.json()["error"] == "confirmation_required"

    def test_prune_drift_happy_path_removes_dead_row(self, drift_client: TestClient) -> None:
        from pyimgtag.progress_db import ProgressDB

        r = drift_client.post("/edit/api/prune-drift", json={"confirm": True})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

        # Wait for the worker to finish.
        for _ in range(50):
            d = drift_client.get("/edit/api/status").json()
            if d["state"] in ("done", "error"):
                break
            time.sleep(0.05)

        d = drift_client.get("/edit/api/status").json()
        assert d["state"] == "done", d
        assert d["total"] == 1  # exactly one dead row
        assert d["ok"] == 1
        assert d["done"] == 1

        with ProgressDB(db_path=drift_client.db_path) as db:  # type: ignore[attr-defined]
            paths = sorted(db.iter_image_paths())
            # Only the present row survives.
            assert paths == [drift_client.present_path]  # type: ignore[attr-defined]

    def test_prune_drift_rejects_overlapping_jobs(self, drift_client: TestClient) -> None:
        """Prune-drift must respect the same singleton lock as delete-from-Photos."""
        from pyimgtag.webapp import routes_edit

        routes_edit._JOB = routes_edit._Job(job_id="held", state="running")
        try:
            r = drift_client.post("/edit/api/prune-drift", json={"confirm": True})
            assert r.status_code == 400
            assert r.json()["error"] == "job_already_running"
        finally:
            routes_edit._reset_job_for_tests()

    def test_delete_from_photos_blocks_while_drift_running(self, drift_client: TestClient) -> None:
        """The two destructive jobs must not run simultaneously."""
        from pyimgtag.webapp import routes_edit

        # Pretend a drift-prune is in flight.
        routes_edit._JOB = routes_edit._Job(job_id="drift-held", state="running")
        try:
            r = drift_client.post("/edit/api/run", json={"confirm": True})
            assert r.status_code == 400
            assert r.json()["error"] == "job_already_running"
        finally:
            routes_edit._reset_job_for_tests()
