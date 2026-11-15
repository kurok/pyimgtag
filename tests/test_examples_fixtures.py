"""examples/fixtures/create_fixtures.py always writes GPS (#440).

It used to need piexif and, without it, silently wrote JPEGs with no
coordinates. It now writes the GPS block with Pillow alone.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyimgtag.exif_reader import read_exif

_SCRIPT = Path(__file__).resolve().parent.parent / "examples" / "fixtures" / "create_fixtures.py"

# Run the script as a user would, but with piexif made unimportable even if
# it happens to be installed, so the test proves it is not needed.
_RUNNER = (
    "import runpy, sys; sys.modules['piexif'] = None; "
    "sys.argv = [sys.argv[1], '--output-dir', sys.argv[2]]; "
    "runpy.run_path(sys.argv[0], run_name='__main__')"
)


def _committed() -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in _SCRIPT.parent.glob("*.jpg")}


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> tuple[Path, str]:
    # Run a copy, so even a script that ignored --output-dir would write into
    # the temp dir and never over the committed fixtures.
    out = tmp_path_factory.mktemp("fixtures")
    script = out / _SCRIPT.name
    shutil.copy(_SCRIPT, script)
    before = _committed()
    result = subprocess.run(  # nosec B603 — fixed interpreter and script
        [sys.executable, "-c", _RUNNER, str(script), str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert _committed() == before
    return out, result.stdout


@pytest.mark.parametrize(
    ("name", "lat", "lon"),
    [
        ("sunset_beach.jpg", 37.7749, -122.4194),
        ("city_walk.jpg", 48.8566, 2.3522),
        ("home_dinner.jpg", 51.5074, -0.1278),
        ("mountain_hike.jpg", 46.8182, 8.2275),
        ("office_meeting.jpg", 40.7128, -74.0060),
    ],
)
def test_each_fixture_carries_its_gps_without_piexif(generated, name, lat, lon):
    out, _ = generated
    exif = read_exif(out / name)
    assert exif.gps_lat == pytest.approx(lat, abs=1e-4)
    assert exif.gps_lon == pytest.approx(lon, abs=1e-4)


def test_the_screenshot_has_no_gps_by_design(generated):
    out, _ = generated
    exif = read_exif(out / "blurry_screenshot.jpg")
    assert exif.gps_lat is None and exif.gps_lon is None


@pytest.mark.parametrize(
    "deg", [0.0, 20.999999, 37.7749, 122.4194, 179.99999999, 46.8182, 0.0000001]
)
def test_dms_never_writes_60_seconds_or_minutes(deg):
    import importlib.util

    spec = importlib.util.spec_from_file_location("create_fixtures", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    d, m, s = module._dms(deg)
    assert 0 <= m < 60 and 0 <= s < 60
    assert float(d + m / 60 + s / 3600) == pytest.approx(deg, abs=1e-5)


def test_the_output_says_which_fixtures_got_gps(generated):
    _, stdout = generated
    assert "created sunset_beach.jpg (640x480, GPS 37.7749, -122.4194)" in stdout
    assert "created blurry_screenshot.jpg (320x240, no GPS (by design))" in stdout
    assert stdout.count(", GPS ") == 5
