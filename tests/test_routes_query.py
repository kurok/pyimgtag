"""Tests for the image query builder router.

Each test uses :class:`TestClient` as a context manager via the
``client_factory`` fixture so the underlying ``ProactorEventLoop`` /
sockets are torn down between tests. Without the ``with`` lifecycle
the Windows runner exhausts TCP/IP buffer space (``WinError 10055``)
under xdist's parallel fan-out.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_query import build_query_router  # noqa: E402


def _seeded_db(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\x00")
    db.mark_done(
        img,
        ImageResult(
            file_path=str(img),
            file_name="a.jpg",
            tags=["sunset"],
            cleanup_class="delete",
            scene_category="landscape",
        ),
    )
    img2 = tmp_path / "b.jpg"
    img2.write_bytes(b"\x01")
    db.mark_done(
        img2,
        ImageResult(file_path=str(img2), file_name="b.jpg", tags=["portrait"]),
    )
    return db


@pytest.fixture()
def client_factory(tmp_path):
    """Yield a callable that returns a context-managed ``TestClient``.

    The context-manager form is required on Windows: bare
    ``TestClient(app)`` instances leak the in-process event loop's
    sockets, and at xdist's worker concurrency that surfaces as
    ``OSError [WinError 10055]`` (TCP/IP buffer exhaustion).
    """
    db = _seeded_db(tmp_path)
    open_clients: list[TestClient] = []

    def _factory(api_base: str = "", prefix: str = "") -> TestClient:
        app = FastAPI()
        if prefix:
            app.include_router(build_query_router(db, api_base=api_base), prefix=prefix)
        else:
            app.include_router(build_query_router(db, api_base=api_base))
        ctx = TestClient(app)
        client = ctx.__enter__()
        open_clients.append(ctx)  # type: ignore[arg-type]
        return client

    yield _factory

    for ctx in open_clients:
        ctx.__exit__(None, None, None)


def test_query_router_html_at_root(client_factory) -> None:
    client = client_factory()
    r = client.get("/")
    assert r.status_code == 200
    assert "/api/images" in r.text


def test_query_router_html_at_prefix(client_factory) -> None:
    client = client_factory(api_base="/query", prefix="/query")
    r = client.get("/query/")
    assert r.status_code == 200
    assert "/query/api/images" in r.text


def test_query_router_html_includes_nav(client_factory) -> None:
    client = client_factory()
    r = client.get("/")
    assert 'href="/query"' in r.text
    assert "nav-link active" in r.text


def test_query_images_no_filter_returns_all(client_factory) -> None:
    client = client_factory()
    r = client.get("/api/images")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_query_images_filter_by_tag(client_factory) -> None:
    client = client_factory()
    r = client.get("/api/images?tag=sunset")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert "sunset" in data[0]["tags_list"]


def test_query_images_filter_by_tag_no_match(client_factory) -> None:
    client = client_factory()
    r = client.get("/api/images?tag=nonexistent")
    assert r.json() == []


def test_query_images_filter_by_cleanup(client_factory) -> None:
    client = client_factory()
    r = client.get("/api/images?cleanup=delete")
    assert r.status_code == 200
    assert len(r.json()) == 1
    r2 = client.get("/api/images?cleanup=review")
    assert r2.json() == []


def test_query_images_respects_limit(client_factory) -> None:
    client = client_factory()
    r = client.get("/api/images?limit=1")
    assert r.status_code == 200
    assert len(r.json()) == 1
