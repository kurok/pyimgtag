#!/usr/bin/env python3
"""Build a realistic photo library for UX testing pyimgtag.

The corpus mimics what a user actually has after a year of phone photos and
some exports from other apps:

  * phone JPEGs with EXIF (DateTimeOriginal, GPS, camera make/model)
  * dates spread over several months and years, so date filters and the
    timeline have something to show
  * GPS in five cities, so --city / --country / --bbox / the map have data
  * a burst (four near-identical frames) and two exact duplicates
  * a dark night shot and a flat, washed-out frame (cleanup candidates)
  * two screenshots (PNG, no EXIF, no date)
  * a HEIC photo, a video clip, a broken RAW file, a .txt and a .DS_Store
  * names with spaces, accents and non-Latin scripts

Everything is deterministic, so two runs produce the same library.
"""

from __future__ import annotations

import math
import os
import random
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(os.environ.get("PYIMGTAG_UX_ROOT", "/lab/library"))
PHONE = ROOT / "Phone Import 2024"
EXPORT = ROOT / "Exported from chat"
VIDEO = ROOT / "Clips"
random.seed(20240612)

CITIES = [
    ("Paris", "Île-de-France", "France", 48.8566, 2.3522),
    ("Barcelona", "Catalonia", "Spain", 41.3874, 2.1686),
    ("Lisboa", "Lisboa", "Portugal", 38.7223, -9.1393),
    ("New York", "New York", "United States", 40.7128, -74.006),
    ("Zürich", "Zürich", "Switzerland", 47.3769, 8.5417),
]

SCENES = {
    "sunset": ((250, 140, 60), (120, 60, 160)),
    "beach": ((120, 190, 235), (235, 220, 150)),
    "forest": ((60, 130, 70), (150, 190, 90)),
    "street": ((110, 110, 120), (200, 190, 170)),
    "food": ((210, 120, 70), (250, 240, 210)),
    "night": ((25, 28, 45), (60, 60, 80)),
    "flat": ((150, 150, 150), (160, 158, 152)),
}


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (w, y)], fill=color)
    return img


def _scene(name: str, size: tuple[int, int], seed: int) -> Image.Image:
    rnd = random.Random(seed)
    top, bottom = SCENES[name]
    img = _gradient(size, top, bottom)
    draw = ImageDraw.Draw(img)
    if name in ("sunset", "beach"):
        draw.ellipse(
            [size[0] * 0.55, size[1] * 0.12, size[0] * 0.78, size[1] * 0.42],
            fill=(255, 235, 170),
        )
        for _ in range(30):
            x = rnd.uniform(0, size[0])
            y = rnd.uniform(size[1] * 0.45, size[1] * 0.95)
            draw.line([(x, y), (x + rnd.uniform(10, 60), y)], fill=(40, 40, 60), width=2)
    elif name == "forest":
        for _ in range(40):
            x = rnd.uniform(0, size[0])
            h = rnd.uniform(size[1] * 0.2, size[1] * 0.6)
            draw.polygon(
                [(x, size[1] - h), (x - h * 0.25, size[1]), (x + h * 0.25, size[1])],
                fill=(30, 90, 40),
            )
    elif name == "street":
        for i in range(6):
            x = i * size[0] / 6
            draw.rectangle([x, size[1] * 0.35, x + size[0] / 9, size[1]], fill=(70, 70, 80))
        draw.rectangle([0, size[1] * 0.85, size[0], size[1]], fill=(60, 60, 66))
    elif name == "food":
        draw.ellipse(
            [size[0] * 0.2, size[1] * 0.25, size[0] * 0.8, size[1] * 0.85],
            fill=(245, 240, 225),
            outline=(120, 110, 95),
            width=4,
        )
        draw.ellipse(
            [size[0] * 0.38, size[1] * 0.42, size[0] * 0.62, size[1] * 0.68],
            fill=(190, 90, 50),
        )
    elif name == "flat":
        img = Image.new("RGB", size, (150, 150, 150))
        draw = ImageDraw.Draw(img)
        for i in range(12):
            draw.rectangle(
                [10, 10 + i * size[1] / 14, size[0] - 10, 10 + (i + 1) * size[1] / 14],
                fill=(158, 158, 154),
            )
    return img


def _dms(deg: float) -> list[tuple[int, int]]:
    deg = abs(deg)
    d = int(deg)
    m_float = (abs(deg) - d) * 60
    m = int(m_float)
    s = (m_float - m) * 60
    return [(d, 1), (m, 1), (int(s * 100), 100)]


def _exif(date: str, city: tuple | None, make: str = "Apple", model: str = "iPhone 15 Pro") -> bytes:
    import piexif

    data: dict[str, dict] = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
    data["0th"][piexif.ImageIFD.Make] = make.encode()
    data["0th"][piexif.ImageIFD.Model] = model.encode()
    data["0th"][piexif.ImageIFD.Software] = b"iOS 17.5"
    if date:
        data["Exif"][piexif.ExifIFD.DateTimeOriginal] = date.encode()
        data["Exif"][piexif.ExifIFD.DateTimeDigitized] = date.encode()
    if city:
        _, _, _, lat, lon = city
        data["GPS"][piexif.GPSIFD.GPSLatitudeRef] = b"N" if lat >= 0 else b"S"
        data["GPS"][piexif.GPSIFD.GPSLatitude] = _dms(lat)
        data["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b"E" if lon >= 0 else b"W"
        data["GPS"][piexif.GPSIFD.GPSLongitude] = _dms(lon)
    return piexif.dump(data)


def _write_photo(path: Path, scene: str, size: tuple[int, int], date: str, city: tuple | None, seed: int,
                 blur: float = 0.0, noise: int = 0) -> None:
    img = _scene(scene, size, seed)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        rnd = random.Random(seed + 1)
        px = img.load()
        for _ in range(noise * 200):
            x = rnd.randrange(img.width)
            y = rnd.randrange(img.height)
            r, g, b = px[x, y]
            px[x, y] = (max(0, min(255, r + rnd.randint(-12, 12))),
                        max(0, min(255, g + rnd.randint(-12, 12))),
                        max(0, min(255, b + rnd.randint(-12, 12))))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), format="JPEG", quality=88, exif=_exif(date, city))


def build() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    PHONE.mkdir(parents=True)
    EXPORT.mkdir(parents=True)
    VIDEO.mkdir(parents=True)

    # --- a year of phone photos, five cities, June to September 2024 -------
    plan = [
        ("IMG_20240612_081533.jpg", "street", (4032, 3024), "2024:06:12 08:15:33", CITIES[0]),
        ("IMG_20240612_184210.jpg", "sunset", (4032, 3024), "2024:06:12 18:42:10", CITIES[0]),
        ("IMG_20240613_120455.jpg", "food", (4032, 3024), "2024:06:13 12:04:55", CITIES[0]),
        ("IMG_20240615_091233.jpg", "beach", (4032, 3024), "2024:06:15 09:12:33", CITIES[1]),
        ("IMG_20240615_193001.jpg", "sunset", (4032, 3024), "2024:06:15 19:30:01", CITIES[1]),
        ("IMG_20240702_101500.jpg", "street", (4032, 3024), "2024:07:02 10:15:00", CITIES[1]),
        ("IMG_20240703_081122.jpg", "beach", (4032, 3024), "2024:07:03 08:11:22", CITIES[1]),
        ("IMG_20240720_203344.jpg", "night", (4032, 3024), "2024:07:20 20:33:44", CITIES[2]),
        ("IMG_20240721_112233.jpg", "street", (4032, 3024), "2024:07:21 11:22:33", CITIES[2]),
        ("IMG_20240721_115501.jpg", "food", (4032, 3024), "2024:07:21 11:55:01", CITIES[2]),
        ("IMG_20240722_164500.jpg", "sunset", (4032, 3024), "2024:07:22 16:45:00", CITIES[2]),
        ("IMG_20240810_080011.jpg", "forest", (4032, 3024), "2024:08:10 08:00:11", CITIES[3]),
        ("IMG_20240810_080112.jpg", "forest", (4032, 3024), "2024:08:10 08:01:12", CITIES[3]),
        ("IMG_20240810_080215.jpg", "forest", (4032, 3024), "2024:08:10 08:02:15", CITIES[3]),
        ("IMG_20240810_080320.jpg", "forest", (4032, 3024), "2024:08:10 08:03:20", CITIES[3]),
        ("IMG_20240811_143022.jpg", "street", (4032, 3024), "2024:08:11 14:30:22", CITIES[3]),
        ("IMG_20240812_071500.jpg", "flat", (4032, 3024), "2024:08:12 07:15:00", CITIES[3]),
        ("IMG_20240820_220145.jpg", "night", (4032, 3024), "2024:08:20 22:01:45", CITIES[3]),
        ("IMG_20240901_121010.jpg", "food", (4032, 3024), "2024:09:01 12:10:10", CITIES[4]),
        ("IMG_20240901_125530.jpg", "street", (4032, 3024), "2024:09:01 12:55:30", CITIES[4]),
        ("IMG_20240902_173000.jpg", "sunset", (4032, 3024), "2024:09:02 17:30:00", CITIES[4]),
        ("IMG_20240902_181544.jpg", "night", (4032, 3024), "2024:09:02 18:15:44", CITIES[4]),
        ("IMG_20230610_101010.jpg", "beach", (4032, 3024), "2023:06:10 10:10:10", CITIES[1]),
        ("IMG_20230105_151500.jpg", "street", (4032, 3024), "2023:01:05 15:15:00", CITIES[3]),
    ]
    for i, (name, scene, size, date, city) in enumerate(plan):
        blur = 3.5 if scene == "night" and i % 2 == 0 else 0.0
        _write_photo(PHONE / name, scene, size, date, city, seed=100 + i, blur=blur)

    # --- a burst: four near-identical frames of the same sunset -------------
    for i in range(4):
        _write_photo(
            PHONE / f"IMG_20240616_20010{i}.jpg",
            "sunset",
            (4032, 3024),
            f"2024:06:16 20:01:0{i}",
            CITIES[0],
            seed=900 + i,
            noise=2,
        )

    # --- exact duplicates with different names -----------------------------
    shutil.copy(PHONE / "IMG_20240703_081122.jpg", PHONE / "IMG_20240703_081122 (1).jpg")
    shutil.copy(PHONE / "IMG_20240811_143022.jpg", PHONE / "copy of IMG_20240811_143022.jpg")

    # --- names a user really has -------------------------------------------
    _write_photo(PHONE / "Family Photo 2.jpg", "food", (4032, 3024), "2024:07:21 12:35:00", CITIES[0], seed=41)
    _write_photo(PHONE / "Vacances à Montréal.jpg", "street", (4032, 3024), "2024:06:20 16:20:00", CITIES[3], seed=42)
    _write_photo(PHONE / "写真 001.jpg", "forest", (4032, 3024), "2024:08:10 09:00:00", CITIES[3], seed=43)

    # --- screenshots (PNG, no EXIF, no date) --------------------------------
    for name in ("Screenshot 2024-06-14 at 10.32.11.png", "Screenshot 2024-08-12 at 09.14.02.png"):
        img = Image.new("RGB", (1170, 2532), (245, 245, 247))
        draw = ImageDraw.Draw(img)
        for i in range(14):
            draw.rectangle([40, 140 + i * 150, 1130, 250 + i * 150], fill=(220, 222, 228))
        img.save(str(PHONE / name), format="PNG")

    # --- a HEIC photo -------------------------------------------------------
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        heic_src = PHONE / "IMG_20240704_101010.heic"
        _write_photo(PHONE / "_heic_tmp.jpg", "beach", (4032, 3024), "2024:07:04 10:10:10", CITIES[1], seed=77)
        with Image.open(PHONE / "_heic_tmp.jpg") as src:
            src.save(str(heic_src), format="HEIF", exif=_exif("2024:07:04 10:10:10", CITIES[1]))
        (PHONE / "_heic_tmp.jpg").unlink()
    except Exception as exc:  # pragma: no cover - depends on pillow-heif
        print(f"  (no HEIC written: {exc})")

    # --- files that are not photos -----------------------------------------
    (PHONE / "notes.txt").write_text("shopping list\nmilk\nbread\n", encoding="utf-8")
    (PHONE / ".DS_Store").write_bytes(b"\x00\x01\x02")
    (PHONE / "IMG_20240910_120000.CR2").write_bytes(b"II*\x00\x08\x00\x00\x00 not a real raw file")

    # --- a chat export: no EXIF at all, dated by mtime only ----------------
    for i, scene in enumerate(["beach", "food", "street", "sunset", "forest"]):
        img = _scene(scene, (1600, 1200), seed=500 + i)
        img.save(str(EXPORT / f"image-{i + 1}.jpg"), format="JPEG", quality=85)

    # --- two video clips ----------------------------------------------------
    for name, secs in (("IMG_20240615_191500.mov", 3), ("IMG_20240720_203000.mov", 2)):
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={secs}:size=640x480:rate=15",
                "-pix_fmt", "yuv420p", str(VIDEO / name),
            ],
            check=False,
            capture_output=True,
        )

    total = sum(1 for _ in ROOT.rglob("*") if _.is_file())
    print(f"Corpus written to {ROOT}: {total} files")
    for d in (PHONE, EXPORT, VIDEO):
        print(f"  {d.name}: {sum(1 for _ in d.iterdir() if _.is_file())} files")


if __name__ == "__main__":
    build()
