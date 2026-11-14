"""Pages say what to do when there is nothing to show (#397).

Checks the served HTML for the copy and commands; the behaviour in a browser
against an empty database is covered by ``tests/e2e/test_empty_states.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.webapp.nav import API_JS, DESIGN_CSS  # noqa: E402
from pyimgtag.webapp.unified_app import create_unified_app  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "progress.db"
    with TestClient(create_unified_app(db_path=db)) as c:
        yield c


def test_the_shared_empty_state_ships_with_every_page():
    assert API_JS.count("function renderEmpty(") == 1
    assert "box.className = 'empty empty-state'" in API_JS
    assert ".empty-state{" in DESIGN_CSS
    # Pages that style their own ``.empty`` keep their look: the shared
    # rules hang off ``.empty-state`` only.
    assert ".empty{" not in DESIGN_CSS


@pytest.mark.parametrize(
    ("path", "copy"),
    [
        ("/review/", ["No photos tagged yet", "pyimgtag run --input-dir <folder>"]),
        ("/review/", ["No photos match these filters"]),
        ("/tags/", ["No tags yet", "pyimgtag run --input-dir <folder>"]),
        ("/judge/", ["No photos scored yet", "pyimgtag judge --input-dir <folder>"]),
        ("/judge/", ["No photos match these filters"]),
        ("/query/", ["No images match these filters"]),
        ("/", ["No run in progress", "'/review/api/stats'", "review.href = '/review'"]),
        ("/", ["insights.href = '/insights'"]),
    ],
)
def test_each_page_carries_its_empty_state_copy(client, path, copy):
    html = client.get(path).text
    for text in copy:
        assert text.replace("<", "&lt;") in html or text in html


def test_empty_copy_is_never_html_injected():
    # renderEmpty builds with textContent / createTextNode only.
    start = API_JS.index("function renderEmpty(")
    body = API_JS[start : API_JS.index("function showModalError(")]
    assert "innerHTML = ''" in body
    assert body.count("innerHTML") == 1
