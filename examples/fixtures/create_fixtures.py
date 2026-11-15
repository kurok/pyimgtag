#!/usr/bin/env python3
"""Generate minimal JPEG fixtures with fake EXIF GPS for demo/capture use.

The GPS block is written with Pillow alone (no piexif or exiftool needed).
"""

from __future__ import annotations

import argparse
from fractions import Fraction
from pathlib import Path

from PIL import Image

OUTPUT_DIR = Path(__file__).parent

FIXTURES = [
    {
        "name": "sunset_beach.jpg",
        "color": (255, 180, 50),
        "size": (640, 480),
        "gps": (37.7749, -122.4194),  # San Francisco
    },
    {
        "name": "city_walk.jpg",
        "color": (100, 149, 237),
        "size": (640, 480),
        "gps": (48.8566, 2.3522),  # Paris
    },
    {
        "name": "home_dinner.jpg",
        "color": (200, 100, 80),
        "size": (480, 640),
        "gps": (51.5074, -0.1278),  # London
    },
    {
        "name": "blurry_screenshot.jpg",
        "color": (220, 220, 220),
        "size": (320, 240),
        "gps": None,
    },
    {
        "name": "mountain_hike.jpg",
        "color": (80, 120, 60),
        "size": (800, 600),
        "gps": (46.8182, 8.2275),  # Switzerland
    },
    {
        "name": "office_meeting.jpg",
        "color": (180, 190, 200),
        "size": (640, 480),
        "gps": (40.7128, -74.0060),  # New York
    },
]


_GPS_IFD = 0x8825  # EXIF pointer to the GPS IFD
# GPS IFD tags (EXIF 2.3, section 4.6.6)
_VERSION, _LAT_REF, _LAT, _LON_REF, _LON = 0, 1, 2, 3, 4


def _dms(deg: float) -> tuple[Fraction, Fraction, Fraction]:
    """Degrees as the (degrees, minutes, seconds) rationals EXIF stores."""
    # Whole hundredths of a second, so rounding can never produce 60".
    hundredths = round(deg * 360_000)
    d, rest = divmod(hundredths, 360_000)
    m, s = divmod(rest, 6_000)
    return Fraction(d), Fraction(m), Fraction(s, 100)


def make_exif(gps: tuple[float, float] | None) -> Image.Exif:
    """Return an EXIF block carrying *gps* as decimal (lat, lon), if given.

    Written with Pillow alone, so the fixtures get their coordinates on any
    machine that can run the script (no piexif or exiftool needed).
    """
    exif = Image.Exif()
    if gps is not None:
        lat, lon = gps
        exif[_GPS_IFD] = {
            _VERSION: b"\x02\x03\x00\x00",
            _LAT_REF: "N" if lat >= 0 else "S",
            _LAT: _dms(abs(lat)),
            _LON_REF: "E" if lon >= 0 else "W",
            _LON: _dms(abs(lon)),
        }
    return exif


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Where to write the JPEGs (default: the folder next to this script)",
    )
    out_dir = parser.parse_args(argv).output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for spec in FIXTURES:
        img = Image.new("RGB", spec["size"], spec["color"])
        out_path = out_dir / spec["name"]
        gps = spec["gps"]
        # No EXIF segment at all for the screenshot, as before.
        extra = {"exif": make_exif(gps)} if gps else {}
        img.save(str(out_path), format="JPEG", **extra)
        where = f"GPS {gps[0]:.4f}, {gps[1]:.4f}" if gps else "no GPS (by design)"
        print(f"  created {out_path.name} ({spec['size'][0]}x{spec['size'][1]}, {where})")
    print(f"Done — {len(FIXTURES)} fixtures in {out_dir}")


if __name__ == "__main__":
    main()
