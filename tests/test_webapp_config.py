"""Tests for pyimgtag.webapp.config.web_enabled."""

from __future__ import annotations

import argparse

from pyimgtag.webapp.config import web_enabled


def _ns(**kw):
    return argparse.Namespace(**{"no_web": False, "web": False, **kw})


def test_default_enabled(monkeypatch):
    monkeypatch.delenv("PYIMGTAG_NO_WEB", raising=False)
    assert web_enabled(_ns()) is True


def test_no_web_beats_env(monkeypatch):
    monkeypatch.delenv("PYIMGTAG_NO_WEB", raising=False)
    assert web_enabled(_ns(no_web=True)) is False


def test_env_var_disables(monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_WEB", "1")
    assert web_enabled(_ns()) is False


def test_web_overrides_env(monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_WEB", "true")
    assert web_enabled(_ns(web=True)) is True


def test_env_values_are_case_insensitive(monkeypatch):
    for v in ("1", "TRUE", "Yes", " true "):
        monkeypatch.setenv("PYIMGTAG_NO_WEB", v)
        assert web_enabled(_ns()) is False, v
