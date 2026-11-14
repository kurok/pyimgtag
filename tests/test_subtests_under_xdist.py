"""Subtest parameters must survive pytest-xdist's execnet channel (#408).

pytest 9 reports each ``subTest`` separately, and under ``-n`` that report,
including the subtest's keyword parameters, is sent to the controller over
execnet, which only serialises builtin types. A ``datetime`` parameter made
``test_timestamps_match_the_specs_date_time_pattern`` fail with
``DumpError: can't serialize <class 'datetime.datetime'>`` whenever it ran on a
worker, and pass when run in-process.
"""

from __future__ import annotations

import subprocess  # nosec B404 — runs pytest on this repository's own tests
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_the_immich_timestamp_test_passes_on_an_xdist_worker():
    pytest.importorskip("xdist")
    result = subprocess.run(  # nosec B603 — fixed interpreter and arguments
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "1",
            "-p",
            "no:cacheprovider",
            "--no-cov",
            "tests/test_immich.py::TestRequestsConformToTheSpec"
            "::test_timestamps_match_the_specs_date_time_pattern",
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "can't serialize" not in result.stdout


def test_execnet_cannot_carry_a_datetime_but_can_carry_its_iso_string():
    """Pins why the subtest is labelled with a string."""
    execnet = pytest.importorskip("execnet")
    from datetime import datetime

    with pytest.raises(execnet.DataFormatError):
        execnet.dumps({"value": datetime(2024, 6, 1)})
    execnet.dumps({"value": datetime(2024, 6, 1).isoformat()})
