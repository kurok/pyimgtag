"""Tests for video keyframe extraction and per-clip aggregation.

The aggregation rules are where the judgement calls are, so they are tested as
pure functions over fabricated frame results -- no ffmpeg, no model, no clip.
The handful of tests that genuinely need ffmpeg build a tiny synthetic file and
skip with a reason when it is absent.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
import unittest
from pathlib import Path

from pyimgtag.models import TagResult
from pyimgtag.video import (
    DEFAULT_FRAME_COUNT,
    VideoToolError,
    aggregate_frames,
    extract_frames,
    ffmpeg_available,
    is_live_photo_sidecar,
    probe,
    sample_positions,
)

requires_ffmpeg = unittest.skipUnless(ffmpeg_available(), "requires ffmpeg and ffprobe on PATH")


def _frame(**kw) -> TagResult:
    """A per-frame model result with sensible defaults."""
    kw.setdefault("tags", [])
    return TagResult(**kw)


class TestSamplePositions(unittest.TestCase):
    def test_three_frames_land_inside_the_clip(self):
        """Never the very first or last frame: those are usually a blur."""
        positions = sample_positions(10.0, 3)
        self.assertEqual(len(positions), 3)
        self.assertGreater(positions[0], 0.0)
        self.assertLess(positions[-1], 10.0)

    def test_positions_are_ordered(self):
        self.assertEqual(sample_positions(30.0, 3), sorted(sample_positions(30.0, 3)))

    def test_one_frame_is_the_middle(self):
        self.assertEqual(sample_positions(10.0, 1), [5.0])

    def test_an_unknown_duration_still_yields_a_frame(self):
        """A tagged opening frame beats an untagged clip."""
        for duration in (None, 0, -1):
            with self.subTest(duration=duration):
                self.assertEqual(sample_positions(duration, 3), [0.0])

    def test_a_larger_count_stays_inside_the_clip(self):
        positions = sample_positions(100.0, 5)
        self.assertEqual(len(positions), 5)
        self.assertTrue(all(0 < p < 100 for p in positions))

    def test_zero_is_treated_as_one(self):
        self.assertEqual(len(sample_positions(10.0, 0)), 1)


class TestAggregation(unittest.TestCase):
    def test_tags_are_the_union_ordered_by_agreement(self):
        frames = [
            _frame(tags=["beach", "sunset"]),
            _frame(tags=["beach", "people"]),
            _frame(tags=["beach"]),
        ]
        self.assertEqual(aggregate_frames(frames).tags[0], "beach")
        self.assertEqual(set(aggregate_frames(frames).tags), {"beach", "sunset", "people"})

    def test_a_tag_repeated_within_one_frame_does_not_outvote_agreement(self):
        """Otherwise one chatty frame decides the clip."""
        frames = [
            _frame(tags=["noise", "noise", "noise"]),
            _frame(tags=["subject"]),
            _frame(tags=["subject"]),
        ]
        self.assertEqual(aggregate_frames(frames).tags[0], "subject")

    def test_tags_are_capped(self):
        frames = [_frame(tags=[f"t{i}" for i in range(20)])]
        self.assertLessEqual(len(aggregate_frames(frames).tags), 5)

    def test_scene_and_tone_take_the_majority(self):
        frames = [
            _frame(scene_category="outdoor_travel", emotional_tone="joyful"),
            _frame(scene_category="outdoor_travel", emotional_tone="calm"),
            _frame(scene_category="indoor_home", emotional_tone="joyful"),
        ]
        result = aggregate_frames(frames)
        self.assertEqual(result.scene_category, "outdoor_travel")
        self.assertEqual(result.emotional_tone, "joyful")

    def test_a_tie_breaks_alphabetically_not_by_frame_order(self):
        """Two runs over one clip must agree."""
        forward = aggregate_frames([_frame(scene_category="beta"), _frame(scene_category="alpha")])
        backward = aggregate_frames([_frame(scene_category="alpha"), _frame(scene_category="beta")])
        self.assertEqual(forward.scene_category, backward.scene_category)
        self.assertEqual(forward.scene_category, "alpha")

    def test_cleanup_takes_the_most_conservative_verdict(self):
        """One good frame is reason enough to keep somebody's memory."""
        frames = [
            _frame(cleanup_class="delete"),
            _frame(cleanup_class="delete"),
            _frame(cleanup_class="keep"),
        ]
        self.assertEqual(aggregate_frames(frames).cleanup_class, "keep")

    def test_a_clip_is_only_delete_when_every_frame_agreed(self):
        frames = [_frame(cleanup_class="delete") for _ in range(3)]
        self.assertEqual(aggregate_frames(frames).cleanup_class, "delete")

    def test_review_beats_delete_but_loses_to_keep(self):
        self.assertEqual(
            aggregate_frames(
                [_frame(cleanup_class="delete"), _frame(cleanup_class="review")]
            ).cleanup_class,
            "review",
        )

    def test_text_in_any_frame_counts_for_the_clip(self):
        """A title card is text in the clip whether or not it fills it."""
        frames = [
            _frame(has_text=False),
            _frame(has_text=True, text_summary="Happy Birthday"),
            _frame(has_text=False),
        ]
        result = aggregate_frames(frames)
        self.assertTrue(result.has_text)
        self.assertEqual(result.text_summary, "Happy Birthday")

    def test_the_summary_comes_from_the_middle_frame(self):
        frames = [
            _frame(summary="lens cap"),
            _frame(summary="children playing in a pool"),
            _frame(summary="a blur"),
        ]
        self.assertEqual(aggregate_frames(frames).summary, "children playing in a pool")

    def test_failed_frames_are_ignored_when_others_worked(self):
        frames = [_frame(error="timeout"), _frame(tags=["beach"], summary="a beach")]
        result = aggregate_frames(frames)
        self.assertIsNone(result.error)
        self.assertEqual(result.tags, ["beach"])

    def test_a_clip_whose_every_frame_failed_is_an_error(self):
        """Not an empty success: the difference matters to the caller."""
        result = aggregate_frames([_frame(error="a"), _frame(error="b")])
        self.assertIsNotNone(result.error)
        self.assertIn("2", result.error)

    def test_no_frames_at_all_is_an_error(self):
        self.assertIsNotNone(aggregate_frames([]).error)

    def test_missing_single_valued_fields_stay_none(self):
        result = aggregate_frames([_frame(tags=["a"]), _frame(tags=["b"])])
        self.assertIsNone(result.scene_category)
        self.assertIsNone(result.cleanup_class)


class TestLivePhotoPairing(unittest.TestCase):
    def test_the_mov_half_of_a_live_photo_is_recognised(self):
        """Tagging both halves puts one moment in the library twice."""
        stills = {"img_1234", "img_5678"}
        self.assertTrue(is_live_photo_sidecar(Path("/p/IMG_1234.MOV"), stills))

    def test_a_standalone_clip_is_not(self):
        self.assertFalse(is_live_photo_sidecar(Path("/p/holiday.mov"), {"img_1234"}))

    def test_matching_ignores_case(self):
        self.assertTrue(is_live_photo_sidecar(Path("/p/IMG_1234.mov"), {"img_1234"}))


class TestProbeAndExtract(unittest.TestCase):
    """The half that shells out. Skipped with a reason when ffmpeg is absent."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(__import__("tempfile").mkdtemp())
        cls.clip = cls.tmp / "clip.mp4"
        if not ffmpeg_available():
            return
        # Two seconds of a test pattern: small, deterministic, no assets.
        subprocess.run(  # noqa: S603  # nosec B603
            [
                shutil.which("ffmpeg"),
                "-nostdin",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=2:size=160x120:rate=10",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(cls.clip),
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )

    @requires_ffmpeg
    def test_probe_reads_duration_and_codec(self):
        info = probe(self.clip)
        self.assertAlmostEqual(info.duration_sec, 2.0, delta=0.5)
        self.assertEqual((info.width, info.height), (160, 120))
        self.assertTrue(info.codec)

    @requires_ffmpeg
    def test_extract_writes_the_requested_number_of_frames(self):
        frames = extract_frames(self.clip, count=DEFAULT_FRAME_COUNT, dest_dir=self.tmp / "f")
        self.assertEqual(len(frames), DEFAULT_FRAME_COUNT)
        for frame in frames:
            self.assertGreater(frame.stat().st_size, 0)

    @requires_ffmpeg
    def test_frames_are_real_images(self):
        from PIL import Image

        frames = extract_frames(self.clip, count=1, dest_dir=self.tmp / "one")
        with Image.open(frames[0]) as img:
            self.assertEqual(img.size, (160, 120))

    @requires_ffmpeg
    def test_a_file_that_is_not_a_video_reports_cleanly(self):
        junk = self.tmp / "not-a-video.mp4"
        junk.write_bytes(b"definitely not an mp4")
        with self.assertRaises(VideoToolError):
            probe(junk)

    def test_a_missing_tool_is_a_named_error_not_a_crash(self):
        import unittest.mock as mock

        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(VideoToolError) as ctx:
                probe(self.clip)
            self.assertIn("ffprobe", str(ctx.exception))
            with self.assertRaises(VideoToolError) as ctx:
                extract_frames(self.clip)
            self.assertIn("ffmpeg", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Scanner partitioning, persistence, and the query filter
# ---------------------------------------------------------------------------


class TestPartitionMedia(unittest.TestCase):
    def test_images_and_videos_are_separated(self):
        from pyimgtag.scanner import partition_media

        paths = [Path("/p/a.jpg"), Path("/p/b.mov"), Path("/p/c.heic"), Path("/p/d.mp4")]
        images, videos = partition_media(paths)
        self.assertEqual([p.name for p in images], ["a.jpg", "c.heic"])
        self.assertEqual([p.name for p in videos], ["b.mov", "d.mp4"])

    def test_a_live_photo_sidecar_is_dropped(self):
        """One capture must not land in the library twice."""
        from pyimgtag.scanner import partition_media

        paths = [Path("/p/IMG_1234.HEIC"), Path("/p/IMG_1234.MOV")]
        images, videos = partition_media(paths)
        self.assertEqual([p.name for p in images], ["IMG_1234.HEIC"])
        self.assertEqual(videos, [])

    def test_a_standalone_clip_survives(self):
        from pyimgtag.scanner import partition_media

        paths = [Path("/p/IMG_1234.HEIC"), Path("/p/holiday.mov")]
        _, videos = partition_media(paths)
        self.assertEqual([p.name for p in videos], ["holiday.mov"])

    def test_the_extension_set_is_configurable(self):
        from pyimgtag.scanner import partition_media

        paths = [Path("/p/a.mkv"), Path("/p/b.jpg")]
        _, videos = partition_media(paths, {"mkv"})
        self.assertEqual([p.name for p in videos], ["a.mkv"])


class TestVideoPersistence(unittest.TestCase):
    """media_type and duration reach the DB and come back out."""

    def setUp(self):
        import tempfile

        from pyimgtag.progress_db import ProgressDB

        self.tmp = Path(tempfile.mkdtemp())
        self.db = ProgressDB(db_path=self.tmp / "p.db")

    def tearDown(self):
        self.db.close()

    def _store(self, name: str, media_type: str, duration=None) -> Path:
        from pyimgtag.models import ImageResult

        path = self.tmp / name
        path.write_bytes(b"x")
        self.db.mark_done(
            path,
            ImageResult(
                file_path=str(path),
                tags=["a"],
                processing_status="ok",
                media_type=media_type,
                duration_sec=duration,
            ),
        )
        return path

    def test_a_video_row_round_trips(self):
        self._store("clip.mp4", "video", 12.5)
        row = self.db.query_images()[0]
        self.assertEqual(row["media_type"], "video")
        self.assertAlmostEqual(row["duration_sec"], 12.5)

    def test_an_image_row_defaults_to_image(self):
        self._store("photo.jpg", "image")
        row = self.db.query_images()[0]
        self.assertEqual(row["media_type"], "image")
        self.assertIsNone(row["duration_sec"])

    def test_the_type_filter_separates_them(self):
        self._store("photo.jpg", "image")
        self._store("clip.mp4", "video", 3.0)

        videos = self.db.query_images(media_type="video")
        images = self.db.query_images(media_type="image")
        self.assertEqual([Path(r["file_path"]).name for r in videos], ["clip.mp4"])
        self.assertEqual([Path(r["file_path"]).name for r in images], ["photo.jpg"])
        self.assertEqual(len(self.db.query_images()), 2)

    def test_the_filter_composes_with_the_others(self):
        self._store("clip.mp4", "video", 3.0)
        self.assertEqual(len(self.db.query_images(media_type="video", tag="a")), 1)
        self.assertEqual(len(self.db.query_images(media_type="video", tag="nope")), 0)


class TestVideoMigration(unittest.TestCase):
    def test_a_v16_database_upgrades_and_backfills(self):
        """An existing library predates video; its rows are images."""
        import sqlite3
        import tempfile

        from pyimgtag.progress_db import ProgressDB

        tmp = Path(tempfile.mkdtemp())
        db_path = tmp / "old.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE processed_images (file_path TEXT PRIMARY KEY, status TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO processed_images VALUES ('/photos/a.jpg', 'ok', '[\"x\"]')")
        conn.execute("PRAGMA user_version = 16")
        conn.commit()
        conn.close()

        with ProgressDB(db_path=db_path) as db:
            self.assertGreaterEqual(db._conn.execute("PRAGMA user_version").fetchone()[0], 17)
            # Asserted in SQL rather than through query_images: this fixture is
            # a minimal v16 table and query_images selects columns a full one
            # would have. The claim under test is the backfill itself.
            row = db._conn.execute(
                "SELECT media_type, duration_sec FROM processed_images"
            ).fetchone()
            self.assertEqual(row[0], "image", "an existing row was not backfilled")
            self.assertIsNone(row[1])


class TestPreflightFfmpeg(unittest.TestCase):
    def test_a_missing_ffmpeg_is_reported_not_raised(self):
        import unittest.mock as mock

        from pyimgtag.preflight import check_ffmpeg

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            ok, message = check_ffmpeg()
        self.assertFalse(ok)
        self.assertIn("ffmpeg", message)
        self.assertIn("--include-video", message)

    @requires_ffmpeg
    def test_a_present_ffmpeg_passes(self):
        from pyimgtag.preflight import check_ffmpeg

        ok, message = check_ffmpeg()
        self.assertTrue(ok)
        self.assertIn("ffmpeg", message.lower())

    def test_ffprobe_missing_alone_is_still_a_failure(self):
        """Both tools are needed; one without the other cannot sample a clip."""
        import subprocess as _sp
        import unittest.mock as mock

        def _fake(argv, **kw):
            rc = 0 if argv[0] == "ffmpeg" else 1
            return _sp.CompletedProcess(argv, rc, stdout="ffmpeg version 7", stderr="")

        from pyimgtag.preflight import check_ffmpeg

        with mock.patch("subprocess.run", side_effect=_fake):
            ok, message = check_ffmpeg()
        self.assertFalse(ok)
        self.assertIn("ffprobe", message)
