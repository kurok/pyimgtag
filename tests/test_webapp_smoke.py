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
        for s in ("path_asc", "path_desc", "newest", "oldest", "judge_asc", "judge_desc"):
            r = client.get("/query/api/images", params={"sort": s})
            assert r.status_code == 200, s

    def test_query_api_judged_filter(self, client: TestClient) -> None:
        only_judged = client.get("/query/api/images", params={"judged": "true"}).json()
        assert all(it["judge_score"] is not None for it in only_judged)
        not_judged = client.get("/query/api/images", params={"judged": "false"}).json()
        assert all(it["judge_score"] is None for it in not_judged)

    def test_judge_api_scores_shape(self, client: TestClient) -> None:
        d = client.get("/judge/api/scores").json()
        assert isinstance(d, list)
        assert d
        score = d[0]
        for key in ("file_path", "file_name", "weighted_score", "core_score", "visible_score"):
            assert key in score, f"judge item missing {key}"
        # 0.8.0 contract: integer scale.
        assert isinstance(score["weighted_score"], int)

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
