"""Tests for the process-wide run registry."""

from __future__ import annotations

import pytest

from pyimgtag import run_registry
from pyimgtag.run_session import RunSession


@pytest.fixture(autouse=True)
def _reset_registry():
    run_registry.set_current(None)
    yield
    run_registry.set_current(None)


def test_get_current_is_none_by_default():
    assert run_registry.get_current() is None


def test_set_current_roundtrips():
    s = RunSession(command="run")
    run_registry.set_current(s)
    assert run_registry.get_current() is s


def test_set_current_none_clears():
    s = RunSession(command="run")
    run_registry.set_current(s)
    run_registry.set_current(None)
    assert run_registry.get_current() is None
