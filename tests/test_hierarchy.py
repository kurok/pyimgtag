"""Tests for the shared keyword taxonomy and hierarchical XMP writing.

The taxonomy is a pure function, so the tree shape is asserted as data. The
exiftool writing is asserted the way the rest of `test_exif_writer.py` does it
— by inspecting the argument vector — plus one real round-trip that runs only
where exiftool is installed.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pyimgtag.hierarchy import (
    EVENTS,
    PEOPLE,
    PLACES,
    TAGS,
    join_path,
    keyword_paths,
    stars_from_score,
    tree_from_paths,
)

requires_exiftool = unittest.skipUnless(shutil.which("exiftool"), "requires exiftool on PATH")


class TestJoinPath(unittest.TestCase):
    def test_it_joins_with_pipes(self):
        self.assertEqual(join_path("Places", "Portugal", "Lisbon"), "Places|Portugal|Lisbon")

    def test_empty_segments_are_dropped_not_preserved(self):
        """A blank between two levels would import as a nameless tag."""
        self.assertEqual(join_path("Places", "Spain", None, "Madrid"), "Places|Spain|Madrid")
        self.assertEqual(join_path("Places", "Spain", "", "Madrid"), "Places|Spain|Madrid")

    def test_whitespace_is_trimmed(self):
        self.assertEqual(join_path("Places", "  Spain  "), "Places|Spain")

    def test_nothing_to_join_is_none(self):
        self.assertIsNone(join_path())
        self.assertIsNone(join_path(None, "", "   "))


class TestKeywordPaths(unittest.TestCase):
    def test_each_kind_gets_its_branch(self):
        paths = keyword_paths(
            ["sunset"], country="Portugal", city="Lisbon", people=["Alice"], event="Italy 2025"
        )
        self.assertIn(f"{TAGS}|sunset", paths)
        self.assertIn(f"{PLACES}|Portugal|Lisbon", paths)
        self.assertIn(f"{PEOPLE}|Alice", paths)
        self.assertIn(f"{EVENTS}|Italy 2025", paths)

    def test_places_nest_country_region_city(self):
        self.assertEqual(
            keyword_paths(country="Portugal", region="Lisboa", city="Óbidos"),
            ["Places|Portugal|Lisboa|Óbidos"],
        )

    def test_a_missing_region_does_not_leave_a_gap(self):
        self.assertEqual(keyword_paths(country="Spain", city="Madrid"), ["Places|Spain|Madrid"])

    def test_a_bare_branch_name_is_never_emitted(self):
        """'Places' alone says nothing and would import as an empty tag."""
        self.assertEqual(keyword_paths([], country=None, city=None), [])
        self.assertEqual(keyword_paths(people=[""], event=""), [])

    def test_output_is_sorted_and_deduplicated(self):
        """Writing the same photo twice must produce identical metadata."""
        paths = keyword_paths(["b", "a", "a"])
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(len(paths), len(set(paths)))

    def test_a_vocabulary_path_replaces_the_flat_tag(self):
        self.assertEqual(
            keyword_paths(["beach"], vocabulary_paths={"beach": "Nature|Coast|beach"}),
            ["Tags|Nature|Coast|beach"],
        )

    def test_a_tag_with_no_vocabulary_entry_stays_a_direct_child(self):
        paths = keyword_paths(["beach", "misc"], vocabulary_paths={"beach": "Nature|beach"})
        self.assertIn("Tags|Nature|beach", paths)
        self.assertIn("Tags|misc", paths)

    def test_blank_tags_are_skipped(self):
        self.assertEqual(keyword_paths(["", "   "]), [])


class TestTreeFromPaths(unittest.TestCase):
    def test_paths_fold_into_a_nested_tree(self):
        tree = tree_from_paths(["Places|Portugal|Lisbon", "Places|Portugal|Porto"])
        self.assertEqual(sorted(tree["Places"]["Portugal"]), ["Lisbon", "Porto"])

    def test_a_leaf_is_an_empty_dict(self):
        self.assertEqual(tree_from_paths(["Tags|sunset"]), {"Tags": {"sunset": {}}})

    def test_nothing_in_nothing_out(self):
        self.assertEqual(tree_from_paths([]), {})


class TestStarsFromScore(unittest.TestCase):
    def test_each_star_covers_exactly_two_points(self):
        self.assertEqual(
            [stars_from_score(s) for s in range(1, 11)], [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
        )

    def test_it_is_monotonic(self):
        """The banker's-rounding version was not, which is the point of ceil."""
        stars = [stars_from_score(s) for s in range(1, 11)]
        self.assertEqual(stars, sorted(stars))

    def test_no_score_means_no_rating(self):
        """A photo the judge never saw must not be given one star."""
        self.assertIsNone(stars_from_score(None))

    def test_it_is_clamped_to_the_xmp_range(self):
        self.assertEqual(stars_from_score(0), 1)
        self.assertEqual(stars_from_score(99), 5)


class TestHierarchicalExifArgs(unittest.TestCase):
    """Argument-vector assertions, the pattern the rest of exif_writer uses."""

    def _run_writer(self, **kwargs) -> list[str]:
        from pyimgtag.exif_writer import write_exif_description

        ok = MagicMock(spec=subprocess.CompletedProcess)
        ok.returncode = 0
        ok.stdout = ""
        ok.stderr = ""
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch("pyimgtag.exif_writer.subprocess.run", return_value=ok) as run:
                write_exif_description("/photos/a.jpg", **kwargs)
        return list(run.call_args_list[-1].args[0])

    def test_both_namespaces_are_written(self):
        """Lightroom reads lr:, digiKam reads digiKam:; one alone leaves a pile."""
        args = self._run_writer(hierarchical=["Places|Spain|Madrid"])
        self.assertIn("-XMP-lr:HierarchicalSubject=Places|Spain|Madrid", args)
        self.assertIn("-XMP-digiKam:TagsList=Places|Spain|Madrid", args)

    def test_flat_subject_is_still_written_alongside(self):
        """Dropping flat Subject would trade every other tool for two."""
        args = self._run_writer(keywords=["sunset"], hierarchical=["Tags|sunset"])
        self.assertIn("-XMP:Subject=sunset", args)
        self.assertIn("-XMP-lr:HierarchicalSubject=Tags|sunset", args)

    def test_it_is_one_exiftool_pass(self):
        from pyimgtag.exif_writer import write_exif_description

        ok = MagicMock(spec=subprocess.CompletedProcess)
        ok.returncode = 0
        ok.stdout = ""
        ok.stderr = ""
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch("pyimgtag.exif_writer.subprocess.run", return_value=ok) as run:
                with patch("pyimgtag.exif_writer._read_date_fields", return_value=None):
                    write_exif_description(
                        "/photos/a.jpg",
                        description="d",
                        keywords=["sunset"],
                        hierarchical=["Tags|sunset"],
                        rating=4,
                    )
        self.assertEqual(run.call_count, 1, "hierarchical keywords cost an extra exiftool run")

    def test_merge_uses_the_remove_then_add_idiom(self):
        """Same as the flat keywords: a re-run must not accumulate duplicates."""
        args = self._run_writer(hierarchical=["Tags|sunset"], merge=True)
        self.assertIn("-XMP-lr:HierarchicalSubject-=Tags|sunset", args)
        self.assertIn("-XMP-lr:HierarchicalSubject+=Tags|sunset", args)
        self.assertNotIn("-XMP-lr:HierarchicalSubject=", args)

    def test_replace_mode_clears_first(self):
        args = self._run_writer(hierarchical=["Tags|sunset"])
        self.assertIn("-XMP-lr:HierarchicalSubject=", args)

    def test_a_rating_is_written_as_an_xmp_star_value(self):
        self.assertIn("-XMP:Rating=4", self._run_writer(rating=4))

    def test_no_rating_writes_no_rating_tag(self):
        args = self._run_writer(keywords=["a"])
        self.assertFalse([a for a in args if a.startswith("-XMP:Rating")])

    def test_hierarchical_is_skipped_for_a_non_xmp_format(self):
        """iptc/exif have no equivalent tag; writing one there is meaningless."""
        args = self._run_writer(keywords=["a"], hierarchical=["Tags|a"], fmt="iptc")
        self.assertFalse([a for a in args if "HierarchicalSubject" in a])

    def test_hierarchical_alone_is_enough_to_write(self):
        """It must not be silently dropped when there is no description."""
        args = self._run_writer(hierarchical=["Tags|sunset"])
        self.assertIn("-XMP-lr:HierarchicalSubject=Tags|sunset", args)

    def test_nothing_at_all_still_short_circuits(self):
        from pyimgtag.exif_writer import write_exif_description

        with patch("pyimgtag.exif_writer.subprocess.run") as run:
            self.assertIsNone(write_exif_description("/photos/a.jpg"))
        run.assert_not_called()


class TestSidecarHierarchical(unittest.TestCase):
    def _run_sidecar(self, tmp: Path, **kwargs) -> list[str]:
        from pyimgtag.exif_writer import write_xmp_sidecar

        ok = MagicMock(spec=subprocess.CompletedProcess)
        ok.returncode = 0
        ok.stdout = ""
        ok.stderr = ""
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch("pyimgtag.exif_writer.subprocess.run", return_value=ok) as run:
                write_xmp_sidecar(str(tmp / "raw.dng"), **kwargs)
        return list(run.call_args_list[-1].args[0])

    def test_the_sidecar_gets_the_tree_too(self):
        """RAW workflows live in sidecars; a tree that skipped them would miss
        exactly the users who care most about hierarchical keywords."""
        tmp = Path(__import__("tempfile").mkdtemp())
        args = self._run_sidecar(tmp, keywords=["sunset"], hierarchical=["Tags|sunset"])
        self.assertIn("-XMP-lr:HierarchicalSubject=Tags|sunset", args)
        self.assertIn("-XMP-digiKam:TagsList=Tags|sunset", args)
        self.assertIn("-XMP:Subject=sunset", args)

    def test_a_sidecar_rating_is_written(self):
        tmp = Path(__import__("tempfile").mkdtemp())
        self.assertIn("-XMP:Rating=3", self._run_sidecar(tmp, rating=3))


class TestExiftoolRoundTrip(unittest.TestCase):
    """The strongest verification available without Lightroom in the room.

    Writes the tags with exiftool and reads them back with exiftool. It does
    not prove Lightroom or digiKam *interpret* the tree as expected — that is
    the manual step still open on the issue — but it does prove the tags land
    in the right namespaces with the right values.
    """

    @requires_exiftool
    def test_hierarchical_tags_survive_a_write_and_read(self):
        from PIL import Image

        from pyimgtag.exif_writer import write_exif_description

        tmp = Path(__import__("tempfile").mkdtemp())
        photo = tmp / "a.jpg"
        Image.new("RGB", (16, 16), (1, 2, 3)).save(photo)

        error = write_exif_description(
            str(photo),
            keywords=["sunset"],
            hierarchical=["Places|Spain|Madrid", "Tags|sunset"],
            rating=4,
        )
        self.assertIsNone(error)

        proc = subprocess.run(  # noqa: S603  # nosec B603
            [
                shutil.which("exiftool"),
                "-s",
                "-s",
                "-s",
                "-XMP-lr:HierarchicalSubject",
                "-XMP-digiKam:TagsList",
                "-XMP:Rating",
                "-XMP:Subject",
                str(photo),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        self.assertIn("Places|Spain|Madrid", out)
        self.assertIn("Tags|sunset", out)
        self.assertIn("4", out)
        self.assertIn("sunset", out)


if __name__ == "__main__":
    unittest.main()
