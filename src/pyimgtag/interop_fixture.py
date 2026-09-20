"""Generate the fixture for the Lightroom Classic / digiKam import check.

Hierarchical keyword writing is verified against exiftool in CI, which proves
the tags land in the right namespaces with the right values. It cannot prove
that Lightroom Classic and digiKam *interpret* the tree as intended. Only those
applications can settle that, and neither is scriptable, so the verification
splits in two: this module produces the files to import and states the exact
tree they should produce, and someone with either application in front of them
does the import and records what they saw.

``docs/interop-verification.md`` is the checklist for that second half.

The sample deliberately exercises every branch the taxonomy can emit in a
single file -- two people, a three-level place, a plain tag, a
controlled-vocabulary tag, an event and a star rating -- because importing one
file is a small enough favour to ask and six would not be. Nothing here is
random: regenerating the fixture produces the same image and the same
metadata, so a difference between two runs means something actually changed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pyimgtag.hierarchy import keyword_paths, stars_from_score

#: Written with the tree embedded in the JPEG itself.
EMBEDDED_NAME = "pyimgtag-interop-embedded.jpg"

#: Written with the tree in a companion ``.xmp`` instead -- the RAW workflow.
SIDECAR_NAME = "pyimgtag-interop-sidecar.jpg"

#: ASCII on purpose. The description also goes into IPTC, and pyimgtag does not
#: declare ``IPTC:CodedCharacterSet``, so non-ASCII text there arrives with an
#: undeclared encoding and could display mangled for reasons that have nothing
#: to do with the keyword tree. Every value this fixture puts into IPTC is
#: therefore ASCII, and the non-ASCII case it exists to exercise lives in the
#: city name, which travels in XMP only.
DESCRIPTION = "pyimgtag interop fixture: sunset over the castle at Obidos"

#: The pretend result. Field names match what `run`, `faces apply` and
#: `events apply` each contribute to a real photo's tree.
PEOPLE: tuple[str, ...] = ("Alice Marques", "Bo Nilsson")
COUNTRY = "Portugal"
REGION = "Leiria"
CITY = "Óbidos"
TAGS: tuple[str, ...] = ("beach", "castle", "sunset")
EVENT = "Portugal 2026"

#: ``beach`` sits under a controlled vocabulary, ``castle`` and ``sunset`` do
#: not, so one fixture covers both shapes of Tags branch.
VOCABULARY_PATHS: dict[str, str] = {"beach": "Nature|Coast|beach"}

#: 7 maps to 4 stars. A score at the edge of a band would make a rounding
#: mistake look like a correct answer, so this one sits inside a band.
JUDGE_SCORE = 7


def expected_paths() -> list[str]:
    """The hierarchical paths the fixture carries, sorted and de-duplicated.

    Built by calling the taxonomy rather than by listing strings, so the
    fixture cannot drift away from what pyimgtag actually writes.
    """
    return keyword_paths(
        TAGS,
        country=COUNTRY,
        region=REGION,
        city=CITY,
        people=PEOPLE,
        event=EVENT,
        vocabulary_paths=VOCABULARY_PATHS,
    )


def expected_rating() -> int:
    """The XMP star rating the fixture carries."""
    stars = stars_from_score(JUDGE_SCORE)
    if stars is None:  # pragma: no cover - JUDGE_SCORE is a constant int
        raise RuntimeError("JUDGE_SCORE must be a score, not None")
    return stars


def flat_keywords() -> list[str]:
    """The flat ``XMP-dc:Subject`` list, written alongside the tree.

    Flat keywords are what every tool other than these two reads, so they are
    never dropped in favour of the tree. Both lists must arrive intact.
    """
    return sorted({*TAGS, *PEOPLE, EVENT})


def _sample_image(path: Path) -> None:
    """Draw a small, deterministic image that is recognizable in a DAM grid."""
    from PIL import Image, ImageDraw

    width, height = 640, 480
    image = Image.new("RGB", (width, height), (18, 22, 38))
    draw = ImageDraw.Draw(image)

    # One band per top-level branch: a glance at the thumbnail says which file
    # this is, without reading any metadata.
    bands = [(214, 116, 60), (92, 148, 122), (110, 124, 196), (198, 176, 92)]
    band_height = height // (len(bands) * 2)
    for index, colour in enumerate(bands):
        top = 120 + index * band_height
        draw.rectangle([60, top, width - 60, top + band_height - 12], fill=colour)

    draw.text((60, 60), "pyimgtag interop fixture", fill=(236, 238, 245))
    draw.text((60, 80), "import me into Lightroom Classic / digiKam", fill=(150, 156, 178))

    image.save(path, quality=92)


def build_fixture(out_dir: str | Path) -> list[Path]:
    """Write the fixture files into *out_dir* and return them.

    Produces two images: one with the tree embedded, one with the tree in a
    companion ``.xmp``. Both paths need checking, because a DAM can read
    embedded metadata happily and ignore sidecars, or the reverse.

    Generate on macOS or Linux. On Windows non-ASCII values are written as a
    literal ``?`` (#366), so the accented city would go into the file already
    broken and the import check would blame the wrong thing.

    Raises:
        RuntimeError: if exiftool is missing, or a write fails. Half a fixture
            would be worse than none -- it would be imported and believed.
    """
    from pyimgtag.exif_writer import write_exif_description, write_xmp_sidecar

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    paths = expected_paths()
    keywords = flat_keywords()
    rating = expected_rating()

    embedded = directory / EMBEDDED_NAME
    _sample_image(embedded)
    error = write_exif_description(
        str(embedded),
        description=DESCRIPTION,
        keywords=keywords,
        hierarchical=paths,
        rating=rating,
    )
    if error:
        raise RuntimeError(f"embedded fixture: {error}")

    sidecar_image = directory / SIDECAR_NAME
    _sample_image(sidecar_image)
    error = write_xmp_sidecar(
        str(sidecar_image),
        description=DESCRIPTION,
        keywords=keywords,
        hierarchical=paths,
        rating=rating,
    )
    if error:
        raise RuntimeError(f"sidecar fixture: {error}")

    return [embedded, sidecar_image, sidecar_image.with_suffix(".xmp")]


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m pyimgtag.interop_fixture``."""
    parser = argparse.ArgumentParser(
        prog="python -m pyimgtag.interop_fixture",
        description=(
            "Generate the Lightroom Classic / digiKam verification fixture. "
            "See docs/interop-verification.md for what to do with it."
        ),
    )
    parser.add_argument(
        "--out-dir",
        default="interop-fixture",
        help="directory to write the fixture into (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    try:
        written = build_fixture(args.out_dir)
    except RuntimeError as exc:
        print(f"Could not build the fixture: {exc}", file=sys.stderr)
        return 1

    print("Wrote:")
    for path in written:
        print(f"  {path}")

    print("\nExpected keyword tree in Lightroom Classic / digiKam:")
    for keyword_path in expected_paths():
        print(f"  {keyword_path}")
    print(f"\nExpected rating: {expected_rating()} of 5 stars")
    print(f"Expected flat keywords: {', '.join(flat_keywords())}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
