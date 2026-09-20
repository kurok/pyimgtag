"""Tests for ``pyimgtag export``.

Everything here runs against a fabricated database: no external application,
no network, and no tool the runners might lack. That property is why this part
of the interop work was split out of the rest.
"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from pyimgtag.commands.export import build_tag_tree, tag_tree_to_xml
from pyimgtag.db.image_db import ImageDB
from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB


class ExportCase(unittest.TestCase):
    """A small library with the awkward cases in it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "p.db"
        self.db = ProgressDB(db_path=self.db_path)
        self._seed()

    def tearDown(self):
        self.db.close()

    def _store(self, name: str, **kw) -> Path:
        path = self.tmp / name
        path.write_bytes(b"x")
        self.db.mark_done(
            path,
            ImageResult(file_path=str(path), processing_status="ok", **kw),
        )
        return path

    def _seed(self) -> None:
        self.beach = self._store(
            "beach.jpg",
            tags=["sunset", "beach"],
            scene_summary="A beach at sunset",
            scene_category="outdoor_travel",
            nearest_country="Portugal",
            nearest_region="Lisboa",
            nearest_city="Óbidos",
            gps_lat=39.36,
            gps_lon=-9.15,
            has_text=False,
            image_date="2024-06-01T10:00:00",
        )
        # No place at all, and a clip rather than a still.
        self.clip = self._store(
            "clip.mp4",
            tags=["kids"],
            media_type="video",
            duration_sec=12.5,
            has_text=True,
            text_summary="Happy Birthday",
        )
        # A country but no city: the tree must not grow a nameless child.
        self.partial = self._store("partial.jpg", tags=["hills"], nearest_country="Spain")

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        import contextlib
        import io

        from pyimgtag.main import main

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv + ["--db", str(self.db_path)])
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()


class TestExportRows(ExportCase):
    def test_every_stored_column_is_carried(self):
        """query_images drops several stored fields; export must not."""
        row = next(r for r in self.db.export_rows() if r["file_name"] == "clip.mp4")
        self.assertEqual(row["media_type"], "video")
        self.assertAlmostEqual(row["duration_sec"], 12.5)
        self.assertTrue(row["has_text"])
        self.assertEqual(row["text_summary"], "Happy Birthday")

    def test_rows_are_ordered_by_path(self):
        """Two exports of one library must diff cleanly."""
        paths = [r["file_path"] for r in self.db.export_rows()]
        self.assertEqual(paths, sorted(paths))

    def test_the_judge_score_is_joined_in(self):
        from pyimgtag.models import JudgeResult, JudgeScores

        self.db.save_judge_result(
            JudgeResult(
                file_path=str(self.beach),
                file_name="beach.jpg",
                scores=JudgeScores(score=8, reason="nice light"),
                weighted_score=8,
            )
        )
        row = next(r for r in self.db.export_rows() if r["file_name"] == "beach.jpg")
        self.assertEqual(row["judge_score"], 8)
        self.assertEqual(row["judge_reason"], "nice light")

    def test_the_event_is_joined_in(self):
        self.db.reconcile_events(
            [
                {
                    "members": [str(self.beach)],
                    "started_at": "2024-06-01",
                    "ended_at": "2024-06-01",
                    "place": "Óbidos",
                    "trip": None,
                    "fallback_name": "Óbidos — June 2024",
                }
            ]
        )
        row = next(r for r in self.db.export_rows() if r["file_name"] == "beach.jpg")
        self.assertEqual(row["event_name"], "Óbidos — June 2024")
        self.assertIsNotNone(row["event_id"])

    def test_a_photo_in_no_event_has_none_not_a_missing_key(self):
        row = next(r for r in self.db.export_rows() if r["file_name"] == "clip.mp4")
        self.assertIn("event_id", row)
        self.assertIsNone(row["event_id"])


class TestCsvExport(ExportCase):
    def test_it_writes_every_column_in_a_fixed_order(self):
        out = self.tmp / "library.csv"
        code, _, err = self._run(["export", "--format", "csv", "--output", str(out)])
        self.assertEqual(code, 0)
        self.assertIn("3 row(s)", err)

        with out.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            self.assertEqual(reader.fieldnames, list(ImageDB.EXPORT_COLUMNS))
            rows = list(reader)
        self.assertEqual(len(rows), 3)

    def test_tags_are_semicolon_joined_as_everywhere_else(self):
        out = self.tmp / "library.csv"
        self._run(["export", "--format", "csv", "--output", str(out)])
        row = next(
            r for r in csv.DictReader(out.open(encoding="utf-8")) if r["file_name"] == "beach.jpg"
        )
        self.assertEqual(set(row["tags"].split(";")), {"sunset", "beach"})

    def test_non_ascii_survives_the_round_trip(self):
        out = self.tmp / "library.csv"
        self._run(["export", "--format", "csv", "--output", str(out)])
        self.assertIn("Óbidos", out.read_text(encoding="utf-8"))


class TestJsonExport(ExportCase):
    def test_it_writes_one_object_per_row(self):
        out = self.tmp / "library.json"
        code, _, _ = self._run(["export", "--format", "json", "--output", str(out)])
        self.assertEqual(code, 0)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(payload), 3)
        self.assertEqual({type(r) for r in payload}, {dict})

    def test_tags_stay_a_list_unlike_the_csv(self):
        out = self.tmp / "library.json"
        self._run(["export", "--format", "json", "--output", str(out)])
        payload = json.loads(out.read_text(encoding="utf-8"))
        beach = next(r for r in payload if r["file_name"] == "beach.jpg")
        self.assertEqual(sorted(beach["tags"]), ["beach", "sunset"])


class TestTagTree(unittest.TestCase):
    """The tree shape, as pure functions."""

    def test_places_nest_country_region_city(self):
        tree = build_tag_tree(
            [
                {
                    "tags": [],
                    "nearest_country": "Portugal",
                    "nearest_region": "Lisboa",
                    "nearest_city": "Óbidos",
                }
            ]
        )
        self.assertEqual(tree["Places"]["Portugal"]["Lisboa"], {"Óbidos": {}})

    def test_photos_in_one_country_share_its_node(self):
        """Country-first is the only order in which the branches merge."""
        tree = build_tag_tree(
            [
                {"tags": [], "nearest_country": "Portugal", "nearest_city": "Lisbon"},
                {"tags": [], "nearest_country": "Portugal", "nearest_city": "Porto"},
            ]
        )
        self.assertEqual(sorted(tree["Places"]["Portugal"]), ["Lisbon", "Porto"])

    def test_a_missing_middle_level_does_not_create_a_blank_node(self):
        tree = build_tag_tree(
            [
                {
                    "tags": [],
                    "nearest_country": "Spain",
                    "nearest_region": None,
                    "nearest_city": "Madrid",
                }
            ]
        )
        self.assertEqual(tree["Places"]["Spain"], {"Madrid": {}})
        self.assertNotIn("", tree["Places"]["Spain"])

    def test_a_country_with_no_city_is_still_a_branch(self):
        tree = build_tag_tree([{"tags": [], "nearest_country": "Spain"}])
        self.assertEqual(tree["Places"], {"Spain": {}})

    def test_tags_events_and_people_get_their_own_branches(self):
        tree = build_tag_tree(
            [{"tags": ["sunset"], "event_name": "Italy 2025"}],
            people={"Alice": {"/a.jpg"}},
        )
        self.assertEqual(tree["Tags"], {"sunset": {}})
        self.assertEqual(tree["Events"], {"Italy 2025": {}})
        self.assertEqual(tree["People"], {"Alice": {}})

    def test_a_library_with_nothing_to_say_yields_an_empty_tree(self):
        self.assertEqual(build_tag_tree([{"tags": []}]), {})


class TestDigikamXml(unittest.TestCase):
    def test_it_is_well_formed_with_the_expected_root(self):
        xml = tag_tree_to_xml({"Places": {"Portugal": {"Lisbon": {}}}})
        root = ET.fromstring(xml)  # noqa: S314  # nosec B314 — our own output
        self.assertEqual(root.tag, "digikam-tags")
        self.assertIn("<!DOCTYPE digikam-tags>", xml)

    def test_the_hierarchy_is_nested_not_flattened(self):
        xml = tag_tree_to_xml({"Places": {"Portugal": {"Lisbon": {}}}})
        root = ET.fromstring(xml)  # noqa: S314  # nosec B314
        places = root.find("tag[@name='Places']")
        self.assertIsNotNone(places)
        portugal = places.find("tag[@name='Portugal']")
        self.assertIsNotNone(portugal)
        self.assertIsNotNone(portugal.find("tag[@name='Lisbon']"))

    def test_output_is_byte_identical_between_runs(self):
        """A diff between two exports should mean something changed."""
        tree = {"Tags": {"zebra": {}, "apple": {}}, "People": {"Bob": {}, "Alice": {}}}
        self.assertEqual(tag_tree_to_xml(tree), tag_tree_to_xml(dict(reversed(tree.items()))))

    def test_names_needing_escaping_are_escaped(self):
        xml = tag_tree_to_xml({"Tags": {"a & b <c>": {}}})
        root = ET.fromstring(xml)  # noqa: S314  # nosec B314
        names = [t.get("name") for t in root.iter("tag")]
        self.assertIn("a & b <c>", names)

    def test_an_empty_tree_is_still_valid_xml(self):
        root = ET.fromstring(tag_tree_to_xml({}))  # noqa: S314  # nosec B314
        self.assertEqual(list(root), [])


class TestDigikamExportCommand(ExportCase):
    def test_it_writes_the_tree_for_the_library(self):
        out = self.tmp / "tags.xml"
        code, _, _ = self._run(["export", "--format", "digikam", "--output", str(out)])
        self.assertEqual(code, 0)

        root = ET.fromstring(out.read_text(encoding="utf-8"))  # noqa: S314  # nosec B314
        names = {t.get("name") for t in root.iter("tag")}
        self.assertIn("Places", names)
        self.assertIn("Portugal", names)
        self.assertIn("Óbidos", names)
        self.assertIn("sunset", names)
        # Spain had no city; it is a leaf, not a parent of a blank node.
        spain = next(t for t in root.iter("tag") if t.get("name") == "Spain")
        self.assertEqual(list(spain), [])


class TestExportEdgeCases(ExportCase):
    def test_an_empty_database_says_so_and_writes_nothing(self):
        import contextlib
        import io

        from pyimgtag.main import main

        empty = self.tmp / "empty.db"
        out = self.tmp / "nothing.csv"
        # main() directly rather than self._run: that helper appends its own
        # --db, and argparse takes the last one, so an override there would
        # silently read the seeded database instead.
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = main(["export", "--db", str(empty), "--output", str(out)])
        self.assertEqual(code, 0)
        self.assertIn("Nothing to export", err.getvalue())
        self.assertFalse(out.exists())

    def test_an_unwritable_destination_is_an_error_not_a_traceback(self):
        out = self.tmp / "no-such-dir" / "library.csv"
        code, _, err = self._run(["export", "--format", "csv", "--output", str(out)])
        self.assertEqual(code, 1)
        self.assertIn("Error:", err)

    def test_output_is_required(self):
        code, _, err = self._run(["export", "--format", "csv"])
        self.assertNotEqual(code, 0)
        self.assertIn("--output", err)


if __name__ == "__main__":
    unittest.main()
