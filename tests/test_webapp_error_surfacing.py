"""Dashboard pages surface backend failures instead of hiding them (#396).

These check the served HTML: the shared helper is present once on every page,
and each page routes its loads and mutations through it. The behaviour in a
real browser is covered by the Playwright suite in ``tests/e2e/``.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.webapp.nav import API_JS, DESIGN_CSS, render_nav  # noqa: E402
from pyimgtag.webapp.unified_app import create_unified_app  # noqa: E402

_PAGES = ["/", "/review/", "/faces/", "/tags/", "/query/", "/judge/"]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "progress.db"
    with TestClient(create_unified_app(db_path=db)) as c:
        yield c


def test_the_helper_ships_with_the_nav():
    nav = render_nav("tags")
    assert nav.endswith(API_JS)
    for name in ("async function apiFetch(", "function showLoadError(", "function showModalError("):
        assert API_JS.count(name) == 1
    assert ".load-error{" in DESIGN_CSS


def test_the_helper_reports_the_servers_detail_or_the_status():
    # The rejection message comes from ``detail`` (string or FastAPI's list
    # form), else the HTTP status; a network failure says the server is gone.
    assert "typeof data.detail === 'string'" in API_JS
    assert "Array.isArray(data.detail)" in API_JS
    assert "'HTTP ' + r.status" in API_JS
    assert "Cannot reach the pyimgtag server" in API_JS
    assert "err.status = r.status" in API_JS
    assert "box.setAttribute('role', 'alert')" in API_JS
    assert "btn.textContent = 'Retry'" in API_JS


@pytest.mark.parametrize("path", _PAGES)
def test_every_page_gets_the_helper_exactly_once(client, path):
    html = client.get(path).text
    assert html.count("async function apiFetch(") == 1


@pytest.mark.parametrize("path", _PAGES)
def test_no_page_reads_a_response_without_checking_it(client, path):
    html = client.get(path).text
    # Raw fetch() is left only for the open-in-Photos links, which fall back
    # to opening the original on any failure.
    raw = [m.start() for m in re.finditer(r"(?<![A-Za-z])fetch\(", html)]
    api_js_start = html.index("async function apiFetch(")
    api_js_end = api_js_start + len(API_JS)
    outside_helper = [i for i in raw if not api_js_start <= i < api_js_end]
    for index in outside_helper:
        assert "open-in-photos" in html[index : index + 120], html[index : index + 120]


def test_query_guards_the_result_count_and_runs_on_enter(client):
    html = client.get("/query/").text
    assert "showLoadError(wrap, err.message, search)" in html
    guard = html.index("showLoadError(wrap, err.message, search)")
    assert html.index("rows.length + ' results'") > guard
    assert "if (ev.key === 'Enter') { ev.preventDefault(); search(); }" in html


def test_tags_load_and_changes_use_the_helper(client):
    html = client.get("/tags/").text
    assert "showLoadError('list', err.message, load)" in html
    assert "status.textContent = 'Could not load tags'" in html
    assert "showModalError(err.message)" in html


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        ("/review/", "showLoadError('grid', err.message, load)"),
        ("/judge/", "showLoadError('grid', err.message, load)"),
        ("/faces/", "showLoadError('persons', err.message, load)"),
        ("/faces/", "showLoadError('unassigned-grid', err.message, loadUnassigned)"),
        ("/faces/", "showLoadError('trash-grid', err.message, loadTrash)"),
        ("/", "setError('Could not ' + action + ': ' + err.message)"),
    ],
)
def test_each_page_shows_its_failures(client, path, needle):
    assert needle in client.get(path).text


def test_faces_mutations_keep_the_modal_open_on_failure(client):
    html = client.get("/faces/").text
    assert html.count("showModalError(err.message)") == 6
    assert "Promise.allSettled" in html


def test_opening_a_modal_clears_a_previous_error():
    from pyimgtag.webapp.nav import MODAL_JS

    assert "classList.remove('modal-error')" in MODAL_JS
