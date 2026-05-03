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
]

_JSON_APIS = [
    ("/api/run/current", []),  # dashboard
    ("/review/api/stats", []),
    ("/review/api/images", []),
    ("/tags/api/tags", []),
    ("/query/api/images", []),
    ("/judge/api/scores", []),
    ("/faces/api/persons", []),
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
        ):
            assert key in item, f"query item missing {key}"

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
