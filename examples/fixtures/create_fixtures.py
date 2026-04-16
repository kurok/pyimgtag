#!/usr/bin/env python3
"""Generate minimal JPEG fixtures with fake EXIF GPS for demo/capture use."""

from __future__ import annotations

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


def _deg_to_dms(deg: float) -> tuple[int, int, float]:
    d = int(deg)
    m_float = (deg - d) * 60
    m = int(m_float)
    s = (m_float - m) * 60
    return d, m, s


def _make_exif_gps(lat: float, lon: float) -> bytes:
    try:
        import piexif

        lat_d, lat_m, lat_s = _deg_to_dms(abs(lat))
        lon_d, lon_m, lon_s = _deg_to_dms(abs(lon))

        gps_ifd = {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: [(lat_d, 1), (lat_m, 1), (int(lat_s * 100), 100)],
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: [(lon_d, 1), (lon_m, 1), (int(lon_s * 100), 100)],
        }
        return piexif.dump({"GPS": gps_ifd})
    except ImportError:
        return b""


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for spec in FIXTURES:
        img = Image.new("RGB", spec["size"], spec["color"])
        out_path = OUTPUT_DIR / spec["name"]
        if spec["gps"]:
            exif_bytes = _make_exif_gps(*spec["gps"])
            if exif_bytes:
                img.save(str(out_path), format="JPEG", exif=exif_bytes)
            else:
                img.save(str(out_path), format="JPEG")
        else:
            img.save(str(out_path), format="JPEG")
        print(f"  created {out_path.name} ({spec['size'][0]}x{spec['size'][1]})")
    print(f"Done — {len(FIXTURES)} fixtures in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
