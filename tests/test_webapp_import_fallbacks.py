"""Tests for ImportError fallbacks in webapp factory functions."""

from __future__ import annotations

import builtins

import pytest


def _import_failing_for(missing: set[str]):
    real_import = builtins.__import__

    def fake(name, *a, **kw):
        if name in missing or name.split(".")[0] in missing:
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *a, **kw)

    return fake


def test_create_unified_app_raises_when_fastapi_missing(monkeypatch):
    from pyimgtag.webapp.unified_app import create_unified_app

    monkeypatch.setattr(builtins, "__import__", _import_failing_for({"fastapi"}))
    with pytest.raises(ImportError, match="fastapi and uvicorn are required"):
        create_unified_app()


def test_build_review_router_raises_when_fastapi_missing(monkeypatch, tmp_path):
    from pyimgtag.progress_db import ProgressDB
    from pyimgtag.webapp.routes_review import build_review_router

    db = ProgressDB(db_path=tmp_path / "p.db")
    monkeypatch.setattr(builtins, "__import__", _import_failing_for({"fastapi"}))
    with pytest.raises(ImportError, match="fastapi and uvicorn are required"):
        build_review_router(db)


def test_build_faces_router_raises_when_fastapi_missing(monkeypatch, tmp_path):
    from pyimgtag.progress_db import ProgressDB
    from pyimgtag.webapp.routes_faces import build_faces_router

    db = ProgressDB(db_path=tmp_path / "p.db")
    monkeypatch.setattr(builtins, "__import__", _import_failing_for({"fastapi"}))
    with pytest.raises(ImportError, match="fastapi is required"):
        build_faces_router(db)
