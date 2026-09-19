"""Tests for spatiotemporal event and trip detection.

Timelines are fabricated rather than read from a library: the whole point of
keeping :mod:`pyimgtag.events` free of SQLite and the filesystem is that
boundary rules can be stated as data and checked exactly.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from pyimgtag.events import (
    DEFAULT_DISTANCE_KM,
    DEFAULT_GAP_HOURS,
    Event,
    PhotoPoint,
    detect_events,
    fallback_name,
    haversine_km,
)

# Two places far enough apart that any sane distance rule separates them.
LISBON = (38.7223, -9.1393)
PORTO = (41.1579, -8.6291)


def _point(name: str, when: datetime, where: tuple[float, float] | None = None, **kw) -> PhotoPoint:
    lat, lon = where if where else (None, None)
    return PhotoPoint(file_path=f"/photos/{name}.jpg", taken_at=when, lat=lat, lon=lon, **kw)


class TestHaversine(unittest.TestCase):
    def test_a_known_distance(self):
        """Lisbon to Porto is about 273 km."""
        self.assertAlmostEqual(haversine_km(*LISBON, *PORTO), 273, delta=5)

    def test_the_same_point_is_zero(self):
        self.assertEqual(haversine_km(*LISBON, *LISBON), 0.0)

    def test_it_crosses_the_antimeridian_correctly(self):
        """A flat degree delta would call this half the planet."""
        self.assertLess(haversine_km(0.0, 179.9, 0.0, -179.9), 30)


class TestEventBoundaries(unittest.TestCase):
    def test_photos_close_in_time_and_place_are_one_event(self):
        start = datetime(2024, 6, 1, 10, 0)
        points = [_point(f"p{i}", start + timedelta(minutes=20 * i), LISBON) for i in range(5)]
        events, unassigned = detect_events(points)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0].members), 5)
        self.assertEqual(unassigned, [])

    def test_a_long_gap_starts_a_new_event(self):
        start = datetime(2024, 6, 1, 10, 0)
        points = [
            _point("morning", start, LISBON),
            _point("evening", start + timedelta(hours=DEFAULT_GAP_HOURS + 1), LISBON),
        ]
        events, _ = detect_events(points)
        self.assertEqual([len(e.members) for e in events], [1, 1])

    def test_a_gap_just_under_the_limit_does_not(self):
        start = datetime(2024, 6, 1, 10, 0)
        points = [
            _point("a", start, LISBON),
            _point("b", start + timedelta(hours=DEFAULT_GAP_HOURS - 0.5), LISBON),
        ]
        events, _ = detect_events(points)
        self.assertEqual(len(events), 1)

    def test_a_long_jump_starts_a_new_event_even_within_the_gap(self):
        """Two cities in one afternoon is two occasions."""
        start = datetime(2024, 6, 1, 10, 0)
        points = [
            _point("lisbon", start, LISBON),
            _point("porto", start + timedelta(hours=1), PORTO),
        ]
        events, _ = detect_events(points)
        self.assertEqual(len(events), 2)

    def test_a_missing_coordinate_is_not_evidence_of_travel(self):
        """One photo without GPS must not split an afternoon in half."""
        start = datetime(2024, 6, 1, 10, 0)
        points = [
            _point("a", start, LISBON),
            _point("b", start + timedelta(minutes=10)),  # no GPS
            _point("c", start + timedelta(minutes=20), LISBON),
        ]
        events, _ = detect_events(points)
        self.assertEqual(len(events), 1)

    def test_photos_with_no_gps_at_all_cluster_on_time(self):
        """Half of any archive predates GPS; it still deserves events."""
        start = datetime(2015, 3, 1, 9, 0)
        points = [
            _point("a", start),
            _point("b", start + timedelta(minutes=30)),
            _point("c", start + timedelta(hours=DEFAULT_GAP_HOURS + 2)),
        ]
        events, _ = detect_events(points)
        self.assertEqual([len(e.members) for e in events], [2, 1])

    def test_gap_hours_is_tunable(self):
        start = datetime(2024, 6, 1, 10, 0)
        points = [_point("a", start, LISBON), _point("b", start + timedelta(hours=3), LISBON)]
        self.assertEqual(len(detect_events(points, gap_hours=2)[0]), 2)
        self.assertEqual(len(detect_events(points, gap_hours=4)[0]), 1)

    def test_distance_km_is_tunable(self):
        start = datetime(2024, 6, 1, 10, 0)
        points = [
            _point("a", start, LISBON),
            _point("b", start + timedelta(minutes=30), PORTO),
        ]
        self.assertEqual(len(detect_events(points, distance_km=500)[0]), 1)
        self.assertEqual(len(detect_events(points, distance_km=DEFAULT_DISTANCE_KM)[0]), 2)


class TestUnassigned(unittest.TestCase):
    def test_photos_without_a_date_are_set_aside_not_guessed_at(self):
        start = datetime(2024, 6, 1, 10, 0)
        points = [
            _point("dated", start, LISBON),
            PhotoPoint(file_path="/photos/undated.jpg", taken_at=None, lat=38.7, lon=-9.1),
        ]
        events, unassigned = detect_events(points)
        self.assertEqual(len(events), 1)
        self.assertEqual([p.file_path for p in unassigned], ["/photos/undated.jpg"])

    def test_a_library_with_no_dates_at_all_yields_no_events(self):
        points = [PhotoPoint(file_path=f"/photos/{i}.jpg", taken_at=None) for i in range(3)]
        events, unassigned = detect_events(points)
        self.assertEqual(events, [])
        self.assertEqual(len(unassigned), 3)


class TestTrips(unittest.TestCase):
    def _week_in_lisbon(self) -> list[PhotoPoint]:
        points = []
        for day in range(4):
            base = datetime(2024, 6, 3 + day, 9, 0)
            points.append(_point(f"d{day}-morning", base, LISBON, place="Lisbon"))
            points.append(
                _point(f"d{day}-evening", base + timedelta(hours=9), LISBON, place="Lisbon")
            )
        return points

    def test_consecutive_days_at_one_place_become_a_trip(self):
        events, _ = detect_events(self._week_in_lisbon())
        self.assertGreater(len(events), 1)
        trips = {e.trip for e in events}
        self.assertEqual(trips, {0}, "the week did not become one trip")

    def test_a_single_busy_day_is_not_a_trip(self):
        """Otherwise every Saturday with a morning and an evening is a holiday."""
        base = datetime(2024, 6, 1, 9, 0)
        points = [
            _point("morning", base, LISBON),
            _point("evening", base + timedelta(hours=10), LISBON),
        ]
        events, _ = detect_events(points)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.trip is None for e in events))

    def test_a_distant_week_is_a_separate_trip(self):
        points = self._week_in_lisbon()
        for day in range(3):
            base = datetime(2024, 8, 10 + day, 9, 0)
            points.append(_point(f"p{day}-morning", base, PORTO, place="Porto"))
            points.append(
                _point(f"p{day}-evening", base + timedelta(hours=9), PORTO, place="Porto")
            )
        events, _ = detect_events(points)
        trips = {e.trip for e in events if e.trip is not None}
        self.assertEqual(trips, {0, 1}, "the two holidays were not separated")


class TestDeterminism(unittest.TestCase):
    def test_input_order_does_not_change_the_result(self):
        """Two runs over one library must agree, whatever order rows arrive in."""
        start = datetime(2024, 6, 1, 10, 0)
        points = [_point(f"p{i}", start + timedelta(hours=i * 3), LISBON) for i in range(8)]
        forward = detect_events(points)[0]
        backward = detect_events(list(reversed(points)))[0]
        self.assertEqual(
            [[p.file_path for p in e.members] for e in forward],
            [[p.file_path for p in e.members] for e in backward],
        )

    def test_simultaneous_photos_order_by_path(self):
        when = datetime(2024, 6, 1, 10, 0)
        points = [_point("b", when, LISBON), _point("a", when, LISBON)]
        events, _ = detect_events(points)
        self.assertEqual(
            [p.file_path for p in events[0].members], ["/photos/a.jpg", "/photos/b.jpg"]
        )


class TestFallbackName(unittest.TestCase):
    def test_a_place_and_a_month(self):
        event = Event(members=[_point("a", datetime(2024, 6, 15, 10, 0), LISBON, place="Lisbon")])
        self.assertEqual(fallback_name(event), "Lisbon — June 2024")

    def test_a_single_day_without_a_place(self):
        event = Event(members=[_point("a", datetime(2024, 6, 15, 10, 0))])
        self.assertIn("2024", fallback_name(event))
        self.assertIn("June", fallback_name(event))

    def test_the_most_common_place_wins(self):
        when = datetime(2024, 6, 15, 10, 0)
        event = Event(
            members=[
                _point("a", when, LISBON, place="Lisbon"),
                _point("b", when + timedelta(minutes=5), LISBON, place="Lisbon"),
                _point("c", when + timedelta(minutes=9), LISBON, place="Sintra"),
            ]
        )
        self.assertTrue(fallback_name(event).startswith("Lisbon"))

    def test_names_use_no_platform_specific_strftime_flags(self):
        """%-d is a glibc/BSD extension; Windows raises on it.

        Naming runs for every event in the library, so one unsupported flag
        would take the feature down entirely on one of the three supported
        platforms.
        """
        single = Event(members=[_point("a", datetime(2024, 6, 5, 10, 0))])
        multi = Event(
            members=[
                _point("a", datetime(2024, 6, 5, 10, 0)),
                _point("b", datetime(2024, 6, 7, 10, 0)),
            ]
        )
        for event in (single, multi):
            name = fallback_name(event)
            self.assertTrue(name)
            # The day is rendered unpadded, which is what %-d was there for.
            self.assertIn("5", name)

    def test_naming_needs_no_model_and_no_network(self):
        """The deterministic path is what makes `events name` optional."""
        event = Event(members=[_point("a", datetime(2024, 1, 2, 8, 0))])
        self.assertTrue(fallback_name(event))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Persistence, identity across re-runs, and the command surface
# ---------------------------------------------------------------------------


def _cluster(name: str, members: list[str], start: str, end: str, **kw) -> dict:
    return {
        "members": members,
        "started_at": start,
        "ended_at": end,
        "place": kw.get("place"),
        "trip": kw.get("trip"),
        "fallback_name": name,
    }


class TestEventsDb(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from pyimgtag.progress_db import ProgressDB

        self.tmp = Path(tempfile.mkdtemp())
        self.db = ProgressDB(db_path=self.tmp / "p.db")

    def tearDown(self):
        self.db.close()

    def test_detected_clusters_are_stored_and_listed(self):
        self.db.reconcile_events(
            [
                _cluster("Lisbon — June 2024", ["/a.jpg", "/b.jpg"], "2024-06-01", "2024-06-01"),
                _cluster("Porto — July 2024", ["/c.jpg"], "2024-07-01", "2024-07-01"),
            ]
        )
        events = self.db.list_events()
        self.assertEqual(len(events), 2)
        # Newest first.
        self.assertEqual(events[0]["name"], "Porto — July 2024")
        self.assertEqual(events[1]["photo_count"], 2)

    def test_a_rename_survives_re_detection(self):
        """The whole point of reconcile: an album a user named stays named."""
        self.db.reconcile_events(
            [_cluster("June 2024", ["/a.jpg", "/b.jpg"], "2024-06-01", "2024-06-01")]
        )
        event_id = self.db.list_events()[0]["id"]
        self.db.rename_event(event_id, "Anniversary dinner")

        # A later run finds the same occasion plus one more photo.
        counts = self.db.reconcile_events(
            [_cluster("June 2024", ["/a.jpg", "/b.jpg", "/c.jpg"], "2024-06-01", "2024-06-01")]
        )
        events = self.db.list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["id"], event_id, "the event id changed")
        self.assertEqual(events[0]["name"], "Anniversary dinner", "the rename was lost")
        self.assertEqual(events[0]["photo_count"], 3)
        self.assertEqual((counts["created"], counts["updated"]), (0, 1))

    def test_an_unrelated_cluster_is_a_new_event(self):
        self.db.reconcile_events([_cluster("A", ["/a.jpg", "/b.jpg"], "2024-06-01", "2024-06-01")])
        first = self.db.list_events()[0]["id"]

        self.db.reconcile_events(
            [
                _cluster("A", ["/a.jpg", "/b.jpg"], "2024-06-01", "2024-06-01"),
                _cluster("B", ["/x.jpg", "/y.jpg"], "2024-09-01", "2024-09-01"),
            ]
        )
        ids = {e["id"] for e in self.db.list_events()}
        self.assertIn(first, ids)
        self.assertEqual(len(ids), 2)

    def test_an_event_whose_photos_all_vanished_is_removed(self):
        self.db.reconcile_events([_cluster("Gone", ["/a.jpg"], "2024-06-01", "2024-06-01")])
        counts = self.db.reconcile_events(
            [_cluster("Other", ["/z.jpg"], "2024-07-01", "2024-07-01")]
        )
        self.assertEqual(counts["removed"], 1)
        self.assertEqual([e["name"] for e in self.db.list_events()], ["Other"])

    def test_membership_and_reverse_lookup(self):
        self.db.reconcile_events([_cluster("A", ["/a.jpg", "/b.jpg"], "2024-06-01", "2024-06-01")])
        event_id = self.db.list_events()[0]["id"]
        self.assertEqual(self.db.event_paths(event_id), {"/a.jpg", "/b.jpg"})
        self.assertEqual(self.db.event_for_path("/a.jpg"), event_id)
        self.assertIsNone(self.db.event_for_path("/never.jpg"))

    def test_renaming_an_unknown_id_reports_it(self):
        self.assertFalse(self.db.rename_event(999, "Nope"))

    def test_a_generated_name_is_replaceable_but_a_user_name_is_not(self):
        self.db.reconcile_events([_cluster("June 2024", ["/a.jpg"], "2024-06-01", "2024-06-01")])
        event_id = self.db.list_events()[0]["id"]
        self.assertEqual(len(self.db.unnamed_events()), 1)

        self.db.rename_event(event_id, "Picked by hand")
        self.assertEqual(self.db.unnamed_events(), [], "a user name was offered up for renaming")

    def test_stats_count_events_trips_and_photos(self):
        self.db.reconcile_events(
            [
                _cluster("A", ["/a.jpg", "/b.jpg"], "2024-06-01", "2024-06-01", trip=0),
                _cluster("B", ["/c.jpg"], "2024-06-02", "2024-06-02", trip=0),
                _cluster("C", ["/d.jpg"], "2024-09-01", "2024-09-01"),
            ]
        )
        stats = self.db.event_stats()
        self.assertEqual(stats["events"], 3)
        self.assertEqual(stats["photos"], 4)
        self.assertEqual(stats["trips"], 1)


class TestEventsMigration(unittest.TestCase):
    def test_a_v15_database_upgrades_cleanly(self):
        import sqlite3
        import tempfile
        from pathlib import Path

        from pyimgtag.progress_db import ProgressDB

        tmp = Path(tempfile.mkdtemp())
        db_path = tmp / "old.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE processed_images (file_path TEXT PRIMARY KEY, status TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO processed_images VALUES ('/photos/a.jpg', 'ok', '[]')")
        conn.execute("PRAGMA user_version = 15")
        conn.commit()
        conn.close()

        with ProgressDB(db_path=db_path) as db:
            self.assertGreaterEqual(db._conn.execute("PRAGMA user_version").fetchone()[0], 16)
            self.assertEqual(
                db._conn.execute("SELECT COUNT(*) FROM processed_images").fetchone()[0], 1
            )
            self.assertEqual(db.event_stats()["events"], 0)


class TestDateParsing(unittest.TestCase):
    """The DB stores capture dates in two spellings; both have to work."""

    def test_both_stored_spellings_parse(self):
        from pyimgtag.commands.events import _parse_date

        self.assertEqual(_parse_date("2024-06-01T10:30:00"), datetime(2024, 6, 1, 10, 30))
        # exiftool's form, which is what most rows actually hold.
        self.assertEqual(_parse_date("2024:06:01 10:30:00"), datetime(2024, 6, 1, 10, 30))

    def test_a_date_only_value_still_parses(self):
        from pyimgtag.commands.events import _parse_date

        self.assertEqual(_parse_date("2024-06-01"), datetime(2024, 6, 1))

    def test_junk_is_none_rather_than_an_exception(self):
        from pyimgtag.commands.events import _parse_date

        for bad in (None, "", "not a date", "0000:00:00 00:00:00"):
            with self.subTest(bad=bad):
                self.assertIsNone(_parse_date(bad))


class TestSafeDirname(unittest.TestCase):
    def test_separators_and_reserved_characters_go(self):
        from pyimgtag.commands.events import _safe_dirname

        self.assertNotIn("/", _safe_dirname("Lisbon / Porto"))
        for ch in '<>:"\\|?*':
            self.assertNotIn(ch, _safe_dirname(f"a{ch}b"))

    def test_an_empty_name_still_yields_a_directory(self):
        from pyimgtag.commands.events import _safe_dirname

        self.assertEqual(_safe_dirname("..."), "event")

    def test_it_is_length_capped(self):
        from pyimgtag.commands.events import _safe_dirname

        self.assertLessEqual(len(_safe_dirname("x" * 400)), 100)


class TestEventsCommands(unittest.TestCase):
    """The CLI, driven through main() against a fabricated library."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from pyimgtag.progress_db import ProgressDB

        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "p.db"
        self.photos = self.tmp / "photos"
        self.photos.mkdir()
        with ProgressDB(db_path=self.db_path) as db:
            self._seed(db)

    def _seed(self, db) -> None:
        """Two days in Lisbon, one afternoon in Porto, one undated photo."""
        rows = [
            ("d1a.jpg", "2024:06:03 09:00:00", 38.7223, -9.1393, "Lisbon"),
            ("d1b.jpg", "2024:06:03 10:30:00", 38.7223, -9.1393, "Lisbon"),
            ("d2a.jpg", "2024:06:04 09:00:00", 38.7223, -9.1393, "Lisbon"),
            ("d2b.jpg", "2024:06:04 11:00:00", 38.7223, -9.1393, "Lisbon"),
            ("porto.jpg", "2024:08:20 15:00:00", 41.1579, -8.6291, "Porto"),
            ("undated.jpg", None, None, None, None),
        ]
        for name, when, lat, lon, city in rows:
            (self.photos / name).write_bytes(b"x")
            db._conn.execute(
                """INSERT INTO processed_images
                       (file_path, file_size, file_mtime, status, processed_at, tags,
                        image_date, gps_lat, gps_lon, nearest_city)
                   VALUES (?, 1, 1.0, 'ok', '2024-06-05', '[]', ?, ?, ?, ?)""",
                (str(self.photos / name), when, lat, lon, city),
            )
        db._conn.commit()

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

    def test_detect_clusters_the_fixture_library(self):
        code, _, err = self._run(["events", "detect"])
        self.assertEqual(code, 0)
        self.assertIn("event(s)", err)
        # The undated photo is reported rather than silently dropped.
        self.assertIn("no capture date", err)

        _, out, _ = self._run(["events", "list", "--format", "json"])
        import json

        events = json.loads(out)
        # Two Lisbon days (a long gap between them) plus Porto.
        self.assertEqual(len(events), 3)
        self.assertEqual(sum(e["photo_count"] for e in events), 5)

    def test_the_lisbon_days_form_one_trip_and_porto_does_not(self):
        import json

        self._run(["events", "detect"])
        _, out, _ = self._run(["events", "list", "--format", "json"])
        events = json.loads(out)
        trips = [e["trip_id"] for e in events if e["trip_id"] is not None]
        self.assertEqual(len(trips), 2, "the two Lisbon days are not one trip")
        porto = next(e for e in events if (e["place"] or "") == "Porto")
        self.assertIsNone(porto["trip_id"])

    def test_detect_is_stable_across_runs(self):
        import json

        self._run(["events", "detect"])
        _, out, _ = self._run(["events", "list", "--format", "json"])
        before = {e["id"] for e in json.loads(out)}

        _, _, err = self._run(["events", "detect"])
        _, out, _ = self._run(["events", "list", "--format", "json"])
        after = {e["id"] for e in json.loads(out)}

        self.assertEqual(before, after, "re-detection renumbered the events")
        self.assertIn("0 new", err)

    def test_list_unassigned_names_the_undated_photo(self):
        self._run(["events", "detect"])
        code, out, _ = self._run(["events", "list", "--unassigned"])
        self.assertEqual(code, 0)
        self.assertIn("undated.jpg", out)
        self.assertNotIn("d1a.jpg", out)

    def test_show_lists_the_members(self):
        import json

        self._run(["events", "detect"])
        _, out, _ = self._run(["events", "list", "--format", "json"])
        event_id = json.loads(out)[0]["id"]

        code, out, _ = self._run(["events", "show", str(event_id), "--format", "json"])
        self.assertEqual(code, 0)
        self.assertIn("members", json.loads(out))

    def test_show_rejects_an_unknown_id(self):
        code, _, err = self._run(["events", "show", "999"])
        self.assertEqual(code, 1)
        self.assertIn("no event with id", err)

    def test_rename_round_trips(self):
        import json

        self._run(["events", "detect"])
        _, out, _ = self._run(["events", "list", "--format", "json"])
        event_id = json.loads(out)[0]["id"]

        code, _, _ = self._run(["events", "rename", str(event_id), "Anniversary dinner"])
        self.assertEqual(code, 0)
        _, out, _ = self._run(["events", "list", "--format", "json"])
        names = {e["id"]: e["name"] for e in json.loads(out)}
        self.assertEqual(names[event_id], "Anniversary dinner")

    def test_name_without_a_backend_keeps_the_generated_names(self):
        """Naming is optional: no model, no network, still usable."""
        import json

        self._run(["events", "detect"])
        code, _, err = self._run(["events", "name", "--fallback-only"])
        self.assertEqual(code, 0)
        _, out, _ = self._run(["events", "list", "--format", "json"])
        self.assertTrue(all(e["name"] for e in json.loads(out)))
        self.assertIn("kept their generated name", err)

    def test_name_sends_metadata_only_never_image_bytes(self):
        """The prompt is built from what the tagger stored, not from a photo."""
        import unittest.mock as mock

        self._run(["events", "detect"])
        seen: list[str] = []

        class _Client:
            def generate_text(self, prompt, max_tokens=60):
                seen.append(prompt)
                return "Lisbon weekend"

        with mock.patch("pyimgtag.ollama_client.OllamaClient", return_value=_Client()):
            code, _, _ = self._run(["events", "name"])

        self.assertEqual(code, 0)
        self.assertTrue(seen, "the backend was never called")
        for prompt in seen:
            # Metadata words, and no file path or base64 payload anywhere.
            self.assertIn("Dates:", prompt)
            self.assertNotIn(str(self.photos), prompt)
            self.assertNotIn(".jpg", prompt)

        import json

        _, out, _ = self._run(["events", "list", "--format", "json"])
        self.assertIn("Lisbon weekend", {e["name"] for e in json.loads(out)})

    def test_a_cloud_backend_says_it_is_not_supported_and_keeps_the_names(self):
        self._run(["events", "detect"])
        code, _, err = self._run(["events", "name", "--backend", "anthropic"])
        self.assertEqual(code, 0)
        self.assertIn("ollama only", err)

    def test_apply_export_dir_builds_a_folder_per_event(self):
        self._run(["events", "detect"])
        out_dir = self.tmp / "albums"
        code, _, err = self._run(["events", "apply", "--export-dir", str(out_dir)])
        self.assertEqual(code, 0)

        folders = sorted(p for p in out_dir.iterdir() if p.is_dir())
        # Three folders, not two: the two Lisbon days share a generated name
        # and must not silently merge into one album.
        self.assertEqual(len(folders), 3, f"events shared a folder: {[f.name for f in folders]}")
        linked = [p for folder in folders for p in folder.iterdir()]
        self.assertEqual(len(linked), 5)
        # Symlinks by default: an album must not double the library's size.
        self.assertTrue(all(p.is_symlink() for p in linked))

    def test_apply_export_dir_can_copy_instead(self):
        self._run(["events", "detect"])
        out_dir = self.tmp / "copies"
        self._run(["events", "apply", "--export-dir", str(out_dir), "--copy"])
        copied = [p for folder in out_dir.iterdir() for p in folder.iterdir()]
        self.assertTrue(copied)
        self.assertFalse(any(p.is_symlink() for p in copied))

    def test_apply_export_dir_is_repeatable(self):
        """Re-running must not fail on files it already made."""
        self._run(["events", "detect"])
        out_dir = self.tmp / "albums"
        self._run(["events", "apply", "--export-dir", str(out_dir)])
        code, _, err = self._run(["events", "apply", "--export-dir", str(out_dir)])
        self.assertEqual(code, 0)
        self.assertIn("5 skipped", err)

    def test_events_sharing_a_name_get_distinct_folders(self):
        """Consecutive days of one trip read alike; the folders must not collide."""
        self._run(["events", "detect"])
        out_dir = self.tmp / "albums"
        self._run(["events", "apply", "--export-dir", str(out_dir)])
        names = sorted(p.name for p in out_dir.iterdir() if p.is_dir())
        self.assertEqual(len(names), len(set(names)))
        lisbon = [n for n in names if n.startswith("Lisbon")]
        self.assertEqual(len(lisbon), 2, f"expected two Lisbon folders, got {names}")

    def test_apply_needs_a_target(self):
        self._run(["events", "detect"])
        code, _, err = self._run(["events", "apply"])
        self.assertEqual(code, 1)
        self.assertIn("--export-dir", err)

    def test_query_can_filter_to_one_event(self):
        import json

        self._run(["events", "detect"])
        _, out, _ = self._run(["events", "list", "--format", "json"])
        event = json.loads(out)[0]

        code, out, _ = self._run(["query", "--event", str(event["id"]), "--format", "paths"])
        self.assertEqual(code, 0)
        self.assertEqual(len([ln for ln in out.splitlines() if ln.strip()]), event["photo_count"])

    def test_query_rejects_an_unknown_event(self):
        code, _, err = self._run(["query", "--event", "999"])
        self.assertEqual(code, 1)
        self.assertIn("no event with id", err)


class TestEventsWebapp(unittest.TestCase):
    """The /events page and its API."""

    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
        except ImportError:  # pragma: no cover - review extra absent
            self.skipTest("fastapi not installed")
        import tempfile
        from pathlib import Path

        from pyimgtag.progress_db import ProgressDB

        self.tmp = Path(tempfile.mkdtemp())
        self.db = ProgressDB(db_path=self.tmp / "p.db")

    def tearDown(self):
        self.db.close()

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from pyimgtag.webapp.routes_events import build_events_router

        app = FastAPI()
        app.include_router(build_events_router(self.db, api_base="/events"), prefix="/events")
        return TestClient(app)

    def test_the_page_renders_with_the_nav_shell(self):
        response = self._client().get("/events/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("pyimgtag Events", response.text)
        self.assertIn('href="/events"', response.text)

    def test_an_empty_library_explains_how_to_detect(self):
        payload = self._client().get("/events/api/events").json()
        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["stats"]["events"], 0)
        self.assertIn("events detect", self._client().get("/events/").text)

    def test_events_come_back_with_a_cover_photo(self):
        self.db.reconcile_events(
            [_cluster("Lisbon", ["/b.jpg", "/a.jpg"], "2024-06-01", "2024-06-01", place="Lisbon")]
        )
        payload = self._client().get("/events/api/events").json()
        self.assertEqual(len(payload["events"]), 1)
        # Stable between loads rather than "best": first by path.
        self.assertEqual(payload["events"][0]["cover"], "/a.jpg")
        self.assertEqual(payload["events"][0]["photo_count"], 2)

    def test_one_event_returns_its_members(self):
        self.db.reconcile_events(
            [_cluster("Lisbon", ["/a.jpg", "/b.jpg"], "2024-06-01", "2024-06-01")]
        )
        event_id = self.db.list_events()[0]["id"]
        payload = self._client().get(f"/events/api/events/{event_id}").json()
        self.assertEqual(payload["members"], ["/a.jpg", "/b.jpg"])

    def test_an_unknown_event_is_a_404(self):
        self.assertEqual(self._client().get("/events/api/events/999").status_code, 404)

    def test_rename_round_trips_through_the_api(self):
        self.db.reconcile_events([_cluster("June 2024", ["/a.jpg"], "2024-06-01", "2024-06-01")])
        event_id = self.db.list_events()[0]["id"]

        response = self._client().post(
            f"/events/api/events/{event_id}/name", json={"name": "Anniversary dinner"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.list_events()[0]["name"], "Anniversary dinner")

    def test_an_empty_rename_is_rejected(self):
        self.db.reconcile_events([_cluster("June 2024", ["/a.jpg"], "2024-06-01", "2024-06-01")])
        event_id = self.db.list_events()[0]["id"]
        response = self._client().post(f"/events/api/events/{event_id}/name", json={"name": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.db.list_events()[0]["name"], "June 2024")

    def test_renaming_an_unknown_event_is_a_404(self):
        self.assertEqual(
            self._client().post("/events/api/events/999/name", json={"name": "x"}).status_code,
            404,
        )


class TestAlbumApplescript(unittest.TestCase):
    """Album creation is additive and never destructive."""

    def test_the_script_creates_the_album_only_when_missing(self):
        from pyimgtag.applescript_writer import _build_album_applescript

        script = _build_album_applescript("Lisbon weekend", ["a.jpg", "b.jpg"])
        self.assertIn('set albumName to "Lisbon weekend"', script)
        self.assertIn("make new album named albumName", script)
        self.assertIn("add matches to targetAlbum", script)

    def test_the_script_never_deletes_or_moves(self):
        """A curated Photos library must only ever gain membership."""
        from pyimgtag.applescript_writer import _build_album_applescript

        script = _build_album_applescript("Trip", ["a.jpg"]).lower()
        for verb in ("delete", "remove", "move ", "trash"):
            self.assertNotIn(verb, script, f"the album script contains {verb!r}")

    def test_a_quote_in_the_album_name_is_escaped(self):
        from pyimgtag.applescript_writer import _build_album_applescript

        script = _build_album_applescript('Anna"s party', ["a.jpg"])
        self.assertNotIn('"Anna"s party"', script)

    def test_an_empty_photo_list_does_nothing(self):
        from pyimgtag.applescript_writer import add_to_album

        self.assertEqual(add_to_album("Trip", []), (True, 0))

    def test_dry_run_reports_without_running_osascript(self):
        import unittest.mock as mock

        from pyimgtag.applescript_writer import add_to_album

        with mock.patch("pyimgtag.applescript_writer._IS_MACOS", True):
            with mock.patch("subprocess.run", side_effect=AssertionError("ran osascript")) as run:
                ok, count = add_to_album("Trip", ["/photos/a.jpg"], dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(count, 1)
        run.assert_not_called()

    def test_it_refuses_cleanly_off_macos(self):
        import unittest.mock as mock

        from pyimgtag.applescript_writer import add_to_album

        with mock.patch("pyimgtag.applescript_writer._IS_MACOS", False):
            self.assertEqual(add_to_album("Trip", ["/photos/a.jpg"]), (False, 0))
