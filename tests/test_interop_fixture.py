"""Tests for the Lightroom Classic / digiKam verification fixture.

The fixture exists to be trusted by someone who will not re-derive it: they
import it and compare what the application shows against the documented tree.
That only works if the sample, the taxonomy and the checklist cannot drift
apart, so the tests here hold all three to each other, and the one exiftool
round-trip proves the files really carry what the page claims.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys  # nosec B404
from pathlib import Path
from unittest.mock import patch

import pytest

from pyimgtag.hierarchy import keyword_paths, stars_from_score
from pyimgtag.interop_fixture import (
    CITY,
    COUNTRY,
    DESCRIPTION,
    EMBEDDED_NAME,
    EVENT,
    JUDGE_SCORE,
    PEOPLE,
    REGION,
    SIDECAR_NAME,
    TAGS,
    VOCABULARY_PATHS,
    build_fixture,
    expected_paths,
    expected_rating,
    flat_keywords,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = REPO_ROOT / "docs" / "interop-verification.md"

requires_exiftool = pytest.mark.skipif(
    not shutil.which("exiftool"), reason="requires exiftool on PATH"
)

#: On Windows the accented city is stored as "?bidos": the value is destroyed
#: in the argv code-page conversion before exiftool ever sees it (#366). Marked
#: strict, so the day that is fixed these fail and the marker comes off rather
#: than sitting here forever saying a bug exists that no longer does.
windows_mangles_non_ascii = pytest.mark.xfail(
    sys.platform == "win32",
    reason="#366: non-ASCII metadata values are written as '?' on Windows",
    strict=True,
)


class TestSample:
    """The sample has to exercise every branch, or the import proves less."""

    def test_it_covers_every_branch_the_taxonomy_can_emit(self):
        assert expected_paths() == [
            "Events|Portugal 2026",
            "People|Alice Marques",
            "People|Bo Nilsson",
            "Places|Portugal|Leiria|Óbidos",
            "Tags|Nature|Coast|beach",
            "Tags|castle",
            "Tags|sunset",
        ]

    def test_paths_come_from_the_taxonomy_not_from_a_list(self):
        """A hand-written list would keep passing after the taxonomy changed."""
        assert expected_paths() == keyword_paths(
            TAGS,
            country=COUNTRY,
            region=REGION,
            city=CITY,
            people=PEOPLE,
            event=EVENT,
            vocabulary_paths=VOCABULARY_PATHS,
        )

    def test_a_vocabulary_tag_uses_its_own_branch(self):
        paths = expected_paths()
        assert "Tags|Nature|Coast|beach" in paths
        assert "Tags|beach" not in paths

    def test_the_place_nests_three_deep(self):
        """Flattening here would make the hardest case look easy in the import."""
        place = next(p for p in expected_paths() if p.startswith("Places|"))
        assert place.count("|") == 3

    def test_rating_matches_the_documented_mapping(self):
        assert expected_rating() == stars_from_score(JUDGE_SCORE) == 4

    def test_flat_keywords_are_sorted_and_unique(self):
        keywords = flat_keywords()
        assert keywords == sorted(set(keywords))
        assert set(keywords) == {*TAGS, *PEOPLE, EVENT}


class TestBuildFixture:
    """Arg construction, asserted the way `test_exif_writer.py` does it."""

    def test_it_writes_both_images_and_returns_their_paths(self, tmp_path):
        with (
            patch("pyimgtag.exif_writer.write_exif_description", return_value=None),
            patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None),
        ):
            written = build_fixture(tmp_path)

        assert [p.name for p in written] == [
            EMBEDDED_NAME,
            SIDECAR_NAME,
            Path(SIDECAR_NAME).with_suffix(".xmp").name,
        ]
        assert (tmp_path / EMBEDDED_NAME).exists()
        assert (tmp_path / SIDECAR_NAME).exists()

    def test_it_creates_a_missing_output_directory(self, tmp_path):
        target = tmp_path / "does" / "not" / "exist"
        with (
            patch("pyimgtag.exif_writer.write_exif_description", return_value=None),
            patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None),
        ):
            build_fixture(target)
        assert (target / EMBEDDED_NAME).exists()

    def test_the_embedded_write_gets_the_tree_the_flat_list_and_the_rating(self, tmp_path):
        with (
            patch("pyimgtag.exif_writer.write_exif_description", return_value=None) as embedded,
            patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None),
        ):
            build_fixture(tmp_path)

        kwargs = embedded.call_args.kwargs
        assert kwargs["hierarchical"] == expected_paths()
        assert kwargs["keywords"] == flat_keywords()
        assert kwargs["rating"] == expected_rating()
        assert kwargs["description"] == DESCRIPTION

    def test_the_sidecar_write_gets_the_same_metadata(self, tmp_path):
        """The RAW path has to be identical, or half the check means nothing."""
        with (
            patch("pyimgtag.exif_writer.write_exif_description", return_value=None),
            patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None) as sidecar,
        ):
            build_fixture(tmp_path)

        kwargs = sidecar.call_args.kwargs
        assert kwargs["hierarchical"] == expected_paths()
        assert kwargs["keywords"] == flat_keywords()
        assert kwargs["rating"] == expected_rating()

    def test_a_failed_embedded_write_raises_rather_than_returning_half_a_fixture(self, tmp_path):
        with (
            patch("pyimgtag.exif_writer.write_exif_description", return_value="no exiftool"),
            patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None),
            pytest.raises(RuntimeError, match="embedded fixture: no exiftool"),
        ):
            build_fixture(tmp_path)

    def test_a_failed_sidecar_write_raises_too(self, tmp_path):
        with (
            patch("pyimgtag.exif_writer.write_exif_description", return_value=None),
            patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value="sidecar exists"),
            pytest.raises(RuntimeError, match="sidecar fixture: sidecar exists"),
        ):
            build_fixture(tmp_path)

    def test_the_image_is_deterministic(self, tmp_path):
        """Two runs differing would make a real metadata change hard to spot."""
        with (
            patch("pyimgtag.exif_writer.write_exif_description", return_value=None),
            patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None),
        ):
            build_fixture(tmp_path / "one")
            build_fixture(tmp_path / "two")

        assert (tmp_path / "one" / EMBEDDED_NAME).read_bytes() == (
            tmp_path / "two" / EMBEDDED_NAME
        ).read_bytes()


class TestMain:
    def test_it_prints_the_tree_and_the_rating(self, tmp_path, capsys):
        with (
            patch("pyimgtag.exif_writer.write_exif_description", return_value=None),
            patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None),
        ):
            code = main(["--out-dir", str(tmp_path)])

        assert code == 0
        out = capsys.readouterr().out
        for path in expected_paths():
            assert path in out
        assert f"{expected_rating()} of 5 stars" in out

    def test_it_reports_a_failure_on_stderr_instead_of_raising(self, tmp_path, capsys):
        with patch(
            "pyimgtag.interop_fixture.build_fixture",
            side_effect=RuntimeError("exiftool is not available on this system"),
        ):
            code = main(["--out-dir", str(tmp_path)])

        assert code == 1
        assert "exiftool is not available" in capsys.readouterr().err


class TestChecklistStaysInSync:
    """The page is what the person doing the import compares against.

    If it can say one thing while the fixture writes another, the manual check
    reports a mismatch that is really a stale document, and the real question
    -- does the application show a tree -- goes unanswered.
    """

    @staticmethod
    def _documented_tree() -> list[str]:
        text = CHECKLIST.read_text(encoding="utf-8")
        match = re.search(r"<!-- expected-tree.*?-->\s*```\n(.*?)```", text, re.DOTALL)
        assert match, "the checklist no longer has a marked expected-tree block"
        return [line for line in match.group(1).splitlines() if line.strip()]

    def test_the_documented_tree_is_the_one_the_fixture_writes(self):
        assert self._documented_tree() == expected_paths()

    def test_the_documented_rating_matches(self):
        text = CHECKLIST.read_text(encoding="utf-8")
        assert f"**{expected_rating()} of 5 stars**" in text

    def test_the_documented_flat_keywords_match(self):
        text = CHECKLIST.read_text(encoding="utf-8")
        assert ", ".join(flat_keywords()) in text

    def test_the_documented_description_matches(self):
        assert DESCRIPTION in CHECKLIST.read_text(encoding="utf-8")


class TestExiftoolRoundTrip:
    """The fixture is only useful if the files really carry the tree."""

    TAGS_READ = (
        "-XMP-lr:HierarchicalSubject",
        "-XMP-digiKam:TagsList",
        "-XMP:Rating",
        "-XMP:Subject",
    )

    @staticmethod
    def _read(path: Path) -> dict:
        """Read the tags back as JSON.

        Deliberately not ``-s -s -s`` with ``text=True``: that returns a blob
        of text decoded with the locale encoding, which is not UTF-8 on every
        runner. exiftool writes its JSON as UTF-8, so the bytes are decoded
        explicitly here and the values compared as lists rather than as
        substrings -- which also makes the assertion exact instead of "appears
        somewhere in the output".
        """
        proc = subprocess.run(  # noqa: S603  # nosec B603
            [
                shutil.which("exiftool"),
                "-json",
                *TestExiftoolRoundTrip.TAGS_READ,
                str(path),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
        return json.loads(proc.stdout.decode("utf-8"))[0]

    @requires_exiftool
    @windows_mangles_non_ascii
    def test_the_embedded_image_carries_the_documented_tree(self, tmp_path):
        build_fixture(tmp_path)
        data = self._read(tmp_path / EMBEDDED_NAME)

        assert data["HierarchicalSubject"] == expected_paths()
        assert data["TagsList"] == expected_paths()
        assert data["Rating"] == expected_rating()
        assert data["Subject"] == flat_keywords()

    @requires_exiftool
    @windows_mangles_non_ascii
    def test_the_sidecar_carries_the_same_tree(self, tmp_path):
        build_fixture(tmp_path)
        data = self._read((tmp_path / SIDECAR_NAME).with_suffix(".xmp"))

        assert data["HierarchicalSubject"] == expected_paths()
        assert data["TagsList"] == expected_paths()
        assert data["Rating"] == expected_rating()
        assert data["Subject"] == flat_keywords()

    @requires_exiftool
    def test_the_sidecar_image_itself_stays_bare(self, tmp_path):
        """An application that ignores sidecars must show nothing, not a tree."""
        build_fixture(tmp_path)
        assert set(self._read(tmp_path / SIDECAR_NAME)) == {"SourceFile"}
