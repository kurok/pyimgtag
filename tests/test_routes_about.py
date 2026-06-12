"""Tests for the About page + PyPI version-check endpoint.

All PyPI lookups are mocked at the ``requests`` boundary so the tests
never touch the network and run identically in CI (where ``requests`` is
present but there is no internet).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag import update_check  # noqa: E402
from pyimgtag.update_check import (  # noqa: E402
    _CACHE,
    _fetch_latest_pypi,
    _parse_version,
    is_newer,
    latest_pypi_version,
)
from pyimgtag.webapp import routes_about  # noqa: E402
from pyimgtag.webapp.routes_about import build_about_router, render_about_html  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    """The PyPI cache is a module-level singleton — reset around each test."""
    _CACHE["at"] = 0.0
    _CACHE["value"] = None
    yield
    _CACHE["at"] = 0.0
    _CACHE["value"] = None


class TestParseVersion:
    def test_three_segments(self):
        assert _parse_version("0.10.0") == (0, 10, 0)

    def test_two_segments(self):
        assert _parse_version("1.2") == (1, 2)

    def test_prerelease_suffix_short_circuits_segment(self):
        # "rc1" -> non-digit breaks the segment at 0.
        assert _parse_version("1.2.0rc1") == (1, 2, 0)
        assert _parse_version("1.2.rc1") == (1, 2, 0)

    def test_is_newer_true_and_false(self):
        assert is_newer("0.18.3", "0.18.2") is True
        assert is_newer("0.18.2", "0.18.2") is False
        assert is_newer("0.18.1", "0.18.2") is False

    def test_is_newer_pads_mixed_length_tuples(self):
        """Same release with a different segment count is not an update (regression)."""
        assert is_newer("0.18.0", "0.18") is False
        assert is_newer("0.18", "0.18.0") is False
        assert is_newer("0.18.1", "0.18") is True
        assert is_newer("0.18", "0.18.1") is False
        assert is_newer("1.2.3.4", "1.2.3") is True


class TestFetchLatestPypi:
    def test_returns_version_on_success(self):
        resp = MagicMock()
        resp.json.return_value = {"info": {"version": "9.9.9"}}
        resp.raise_for_status.return_value = None
        fake_requests = MagicMock()
        fake_requests.get.return_value = resp
        with patch.dict("sys.modules", {"requests": fake_requests}):
            assert _fetch_latest_pypi() == "9.9.9"
        fake_requests.get.assert_called_once()

    def test_returns_none_when_version_missing(self):
        resp = MagicMock()
        resp.json.return_value = {"info": {}}
        resp.raise_for_status.return_value = None
        fake_requests = MagicMock()
        fake_requests.get.return_value = resp
        with patch.dict("sys.modules", {"requests": fake_requests}):
            assert _fetch_latest_pypi() is None

    def test_returns_none_when_version_not_a_string(self):
        resp = MagicMock()
        resp.json.return_value = {"info": {"version": 123}}
        resp.raise_for_status.return_value = None
        fake_requests = MagicMock()
        fake_requests.get.return_value = resp
        with patch.dict("sys.modules", {"requests": fake_requests}):
            assert _fetch_latest_pypi() is None

    def test_returns_none_on_request_exception(self):
        fake_requests = MagicMock()
        # The except clause references requests.RequestException / ValueError, so
        # RequestException must be a real exception class.
        fake_requests.RequestException = RuntimeError
        fake_requests.get.side_effect = RuntimeError("boom")
        with patch.dict("sys.modules", {"requests": fake_requests}):
            assert _fetch_latest_pypi() is None

    def test_returns_none_when_requests_unimportable(self):
        # Simulate ``import requests`` failing inside the function.
        with patch.dict("sys.modules", {"requests": None}):
            assert _fetch_latest_pypi() is None


class TestLatestVersion:
    def test_uses_cache_within_ttl(self):
        _CACHE["value"] = "1.0.0"
        _CACHE["at"] = 100.0
        with patch.object(update_check, "_fetch_latest_pypi") as fetch:
            # now is only 1 second later, well within the hour TTL.
            assert latest_pypi_version(now=101.0) == "1.0.0"
            fetch.assert_not_called()

    def test_fetches_and_populates_cache_when_stale(self):
        with patch.object(update_check, "_fetch_latest_pypi", return_value="2.0.0") as fetch:
            assert latest_pypi_version(now=5000.0) == "2.0.0"
            fetch.assert_called_once()
        assert _CACHE["value"] == "2.0.0"
        assert _CACHE["at"] == 5000.0

    def test_does_not_cache_when_fetch_fails(self):
        with patch.object(update_check, "_fetch_latest_pypi", return_value=None):
            assert latest_pypi_version(now=5000.0) is None
        assert _CACHE["value"] is None

    def test_defaults_now_to_monotonic(self):
        with patch.object(update_check, "_fetch_latest_pypi", return_value="3.0.0"):
            # now=None branch calls time.monotonic().
            assert latest_pypi_version() == "3.0.0"


def _client():
    app = FastAPI()
    app.include_router(build_about_router(), prefix="/about")
    return TestClient(app)


class TestAboutPage:
    def test_render_about_html_has_version_and_nav(self):
        html = render_about_html()
        assert "About pyimgtag" in html
        assert 'href="/about"' in html

    def test_about_page_route(self):
        client = _client()
        r = client.get("/about/")
        assert r.status_code == 200
        assert "About pyimgtag" in r.text


class TestBuildAboutRouterImportGuard:
    def test_missing_fastapi_raises_importerror(self):
        # Force ``from fastapi import APIRouter`` to fail inside the factory.
        with patch.dict("sys.modules", {"fastapi": None}):
            with pytest.raises(ImportError, match="fastapi is required"):
                build_about_router()


class TestVersionEndpoint:
    def test_update_available(self):
        with patch.object(routes_about, "latest_pypi_version", return_value="999.0.0"):
            r = _client().get("/about/api/version")
        assert r.status_code == 200
        body = r.json()
        assert body["latest"] == "999.0.0"
        assert body["update"] is True

    def test_up_to_date(self):
        with patch.object(routes_about, "latest_pypi_version", return_value="0.0.0"):
            r = _client().get("/about/api/version")
        body = r.json()
        assert body["latest"] == "0.0.0"
        assert body["update"] is False

    def test_lookup_failed_returns_none_latest(self):
        with patch.object(routes_about, "latest_pypi_version", return_value=None):
            r = _client().get("/about/api/version")
        body = r.json()
        assert body["latest"] is None
        assert body["update"] is False

    def test_stale_cache_forces_refetch_when_installed_newer(self):
        # Pin __version__ to a known value so this test is independent of the
        # build-derived version (which may be "0.0.0+unknown" in shallow-clone CI).
        installed_ver = "1.0.0"
        with patch("pyimgtag.__version__", installed_ver):
            with patch.object(
                routes_about,
                "latest_pypi_version",
                side_effect=["0.0.1", installed_ver],
            ) as lv:
                r = _client().get("/about/api/version")
        body = r.json()
        assert lv.call_count == 2
        assert body["latest"] == installed_ver
        assert body["update"] is False
