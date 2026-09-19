"""Tests for local semantic search: embedding storage, indexing, and retrieval.

No test here downloads a model or runs ONNX. Every one drives a stub embedder
with fixed vectors, which is the reason
:class:`pyimgtag.search.embedder.Embedder` is a protocol at all: ranking is a
property of the vectors, not of the network.
"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

import numpy as np

from pyimgtag.progress_db import ProgressDB
from pyimgtag.search.embedder import Embedder
from pyimgtag.search.indexer import build_index


class StubEmbedder:
    """Deterministic embedder: each file/phrase maps to a fixed unit vector.

    Vectors are 4-d and axis-aligned so expected rankings are obvious by
    inspection rather than by running a model.
    """

    model_id = "stub-v1"
    dim = 4

    #: axis 0 = beach, 1 = forest, 2 = city, 3 = indoor
    _IMAGE_AXIS = {"beach.jpg": 0, "forest.jpg": 1, "city.jpg": 2, "kitchen.jpg": 3}
    _TEXT_AXIS = {"sunny beach": 0, "green forest": 1, "city street": 2, "a kitchen": 3}

    def __init__(self) -> None:
        self.embed_calls: list[Path] = []

    @staticmethod
    def _unit(axis: int) -> np.ndarray:
        vector = np.zeros(4, dtype=np.float32)
        vector[axis] = 1.0
        return vector

    def embed_image(self, path: Path) -> np.ndarray:
        self.embed_calls.append(path)
        if path.name not in self._IMAGE_AXIS:
            raise ValueError(f"stub has no vector for {path.name}")
        return self._unit(self._IMAGE_AXIS[path.name])

    def embed_text(self, text: str) -> np.ndarray:
        return self._unit(self._TEXT_AXIS[text])


def _make_images(tmp_path: Path, *names: str) -> list[Path]:
    paths = []
    for name in names:
        p = tmp_path / name
        p.write_bytes(b"not-a-real-image")
        paths.append(p)
    return paths


class TestEmbedderProtocol(unittest.TestCase):
    def test_the_stub_satisfies_the_protocol(self):
        """If this breaks, the tests below stopped testing the real contract."""
        self.assertIsInstance(StubEmbedder(), Embedder)


class TestEmbeddingStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.db = ProgressDB(db_path=self.tmp / "p.db")

    def tearDown(self):
        self.db.close()

    def test_embedding_round_trips_through_the_blob(self):
        (image,) = _make_images(self.tmp, "beach.jpg")
        vector = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)
        self.db.upsert_embedding(image, "stub-v1", vector)
        hits = self.db.search_similar(vector, limit=5)
        self.assertEqual([h[0] for h in hits], [str(image)])
        self.assertAlmostEqual(hits[0][1], 1.0, places=5)

    def test_an_unchanged_file_does_not_need_re_embedding(self):
        (image,) = _make_images(self.tmp, "beach.jpg")
        self.assertTrue(self.db.needs_embedding(image, "stub-v1"))
        self.db.upsert_embedding(image, "stub-v1", np.ones(4, dtype=np.float32))
        self.assertFalse(self.db.needs_embedding(image, "stub-v1"))

    def test_a_changed_file_needs_re_embedding(self):
        import os
        import time

        (image,) = _make_images(self.tmp, "beach.jpg")
        self.db.upsert_embedding(image, "stub-v1", np.ones(4, dtype=np.float32))
        image.write_bytes(b"different content entirely")
        # Same contract as ImageDB.is_processed: size or mtime moving is enough.
        os.utime(image, (time.time() + 10, time.time() + 10))
        self.assertTrue(self.db.needs_embedding(image, "stub-v1"))

    def test_a_different_model_invalidates_the_row(self):
        """Vectors from two models share no space, so one cannot stand in for the other."""
        (image,) = _make_images(self.tmp, "beach.jpg")
        self.db.upsert_embedding(image, "stub-v1", np.ones(4, dtype=np.float32))
        self.assertTrue(self.db.needs_embedding(image, "other-model"))

    def test_clear_embeddings_reports_what_it_removed(self):
        images = _make_images(self.tmp, "beach.jpg", "forest.jpg")
        for image in images:
            self.db.upsert_embedding(image, "stub-v1", np.ones(4, dtype=np.float32))
        self.assertEqual(self.db.clear_embeddings(), 2)
        self.assertEqual(self.db.embedding_stats()["count"], 0)

    def test_stats_name_the_model_in_use(self):
        (image,) = _make_images(self.tmp, "beach.jpg")
        self.db.upsert_embedding(image, "stub-v1", np.ones(4, dtype=np.float32))
        stats = self.db.embedding_stats()
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["model"], "stub-v1")
        self.assertIsNotNone(stats["last_indexed_at"])


class TestIndexing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.db = ProgressDB(db_path=self.tmp / "p.db")
        self.embedder = StubEmbedder()

    def tearDown(self):
        self.db.close()

    def test_index_embeds_every_image_once(self):
        paths = _make_images(self.tmp, "beach.jpg", "forest.jpg", "city.jpg")
        stats = build_index(paths, self.db, self.embedder)
        self.assertEqual((stats.embedded, stats.skipped, stats.failed), (3, 0, 0))
        self.assertEqual(self.db.embedding_stats()["count"], 3)

    def test_re_running_embeds_nothing_new(self):
        """The incremental promise: a second run does no model work."""
        paths = _make_images(self.tmp, "beach.jpg", "forest.jpg")
        build_index(paths, self.db, self.embedder)
        self.embedder.embed_calls.clear()

        stats = build_index(paths, self.db, self.embedder)
        self.assertEqual((stats.embedded, stats.skipped), (0, 2))
        self.assertEqual(self.embedder.embed_calls, [])

    def test_rebuild_re_embeds_everything(self):
        paths = _make_images(self.tmp, "beach.jpg", "forest.jpg")
        build_index(paths, self.db, self.embedder)
        self.embedder.embed_calls.clear()

        stats = build_index(paths, self.db, self.embedder, rebuild=True)
        self.assertEqual(stats.embedded, 2)
        self.assertEqual(len(self.embedder.embed_calls), 2)

    def test_one_unreadable_file_does_not_end_the_run(self):
        paths = _make_images(self.tmp, "beach.jpg", "mystery.jpg", "forest.jpg")
        stats = build_index(paths, self.db, self.embedder)
        self.assertEqual((stats.embedded, stats.failed), (2, 1))
        self.assertEqual(len(stats.errors), 1)
        self.assertIn("mystery.jpg", str(stats.errors[0][0]))
        # The two good files are still indexed.
        self.assertEqual(self.db.embedding_stats()["count"], 2)

    def test_limit_stops_after_n_new_embeddings(self):
        paths = _make_images(self.tmp, "beach.jpg", "forest.jpg", "city.jpg")
        stats = build_index(paths, self.db, self.embedder, limit=2)
        self.assertEqual(stats.embedded, 2)
        self.assertEqual(self.db.embedding_stats()["count"], 2)


class TestRetrieval(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.db = ProgressDB(db_path=self.tmp / "p.db")
        self.embedder = StubEmbedder()
        self.paths = _make_images(self.tmp, "beach.jpg", "forest.jpg", "city.jpg", "kitchen.jpg")
        build_index(self.paths, self.db, self.embedder)

    def tearDown(self):
        self.db.close()

    def _search(self, text: str, **kwargs):
        return self.db.search_similar(self.embedder.embed_text(text), **kwargs)

    def test_the_matching_photo_ranks_first(self):
        for text, expected in (
            ("sunny beach", "beach.jpg"),
            ("green forest", "forest.jpg"),
            ("city street", "city.jpg"),
            ("a kitchen", "kitchen.jpg"),
        ):
            with self.subTest(text=text):
                hits = self._search(text, limit=4)
                self.assertEqual(Path(hits[0][0]).name, expected)
                self.assertAlmostEqual(hits[0][1], 1.0, places=5)

    def test_results_are_ordered_by_descending_score(self):
        hits = self._search("sunny beach", limit=4)
        self.assertEqual([s for _, s in hits], sorted((s for _, s in hits), reverse=True))

    def test_top_k_is_respected(self):
        self.assertEqual(len(self._search("sunny beach", limit=2)), 2)

    def test_min_score_drops_weak_matches(self):
        """Orthogonal stub vectors score 0, so only the exact match survives."""
        hits = self._search("sunny beach", limit=4, min_score=0.5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(Path(hits[0][0]).name, "beach.jpg")

    def test_filters_select_the_candidates_and_similarity_orders_them(self):
        """Filter-then-rank: a photo outside the filter cannot be returned."""
        allowed = {str(self.tmp / "forest.jpg"), str(self.tmp / "city.jpg")}
        hits = self._search("sunny beach", limit=4, allowed_paths=allowed)
        self.assertEqual({h[0] for h in hits}, allowed)

    def test_a_filter_that_matches_nothing_returns_nothing(self):
        """An empty allow-set means 'filtered to nothing', not 'no filter'."""
        self.assertEqual(self._search("sunny beach", limit=4, allowed_paths=set()), [])

    def test_searching_an_empty_index_is_not_an_error(self):
        self.db.clear_embeddings()
        self.assertEqual(self._search("sunny beach", limit=4), [])


class TestQueryByExample(unittest.TestCase):
    """--similar-to: rank by resemblance to a photo instead of a description."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.db = ProgressDB(db_path=self.tmp / "p.db")
        self.embedder = StubEmbedder()
        self.paths = _make_images(self.tmp, "beach.jpg", "forest.jpg", "city.jpg")
        build_index(self.paths, self.db, self.embedder)

    def tearDown(self):
        self.db.close()

    def test_an_indexed_photo_is_answered_without_loading_a_model(self):
        """The fast path. A library photo already has its vector."""
        beach = self.tmp / "beach.jpg"
        stored = self.db.get_embedding(beach)
        self.assertIsNotNone(stored)
        self.embedder.embed_calls.clear()

        hits = self.db.search_similar(stored, limit=3, exclude_paths={str(beach)})
        self.assertEqual(self.embedder.embed_calls, [], "the fast path embedded something")
        self.assertNotIn(str(beach), [h[0] for h in hits])

    def test_an_unindexed_file_returns_no_stored_vector(self):
        self.assertIsNone(self.db.get_embedding(self.tmp / "never-seen.jpg"))

    def test_the_example_photo_is_not_its_own_best_match(self):
        """It would score 1.0 against itself and waste the top slot."""
        beach = str(self.tmp / "beach.jpg")
        vector = self.db.get_embedding(beach)

        with_it = self.db.search_similar(vector, limit=3)
        without = self.db.search_similar(vector, limit=3, exclude_paths={beach})

        self.assertEqual(with_it[0][0], beach)
        self.assertNotIn(beach, [h[0] for h in without])
        self.assertEqual(len(without), 2)

    def test_exclusion_composes_with_the_structured_filter(self):
        """Both hooks apply; neither cancels the other."""
        beach = str(self.tmp / "beach.jpg")
        forest = str(self.tmp / "forest.jpg")
        vector = self.db.get_embedding(beach)

        hits = self.db.search_similar(
            vector,
            limit=3,
            allowed_paths={beach, forest},
            exclude_paths={beach},
        )
        self.assertEqual([h[0] for h in hits], [forest])

    def test_top_k_and_min_similarity_apply_to_example_search_too(self):
        beach = str(self.tmp / "beach.jpg")
        vector = self.db.get_embedding(beach)
        # The stub's vectors are orthogonal, so everything but the example
        # itself scores 0 — and the example is excluded.
        self.assertEqual(
            self.db.search_similar(vector, limit=3, exclude_paths={beach}, min_score=0.5), []
        )
        self.assertEqual(len(self.db.search_similar(vector, limit=1, exclude_paths={beach})), 1)


class TestQueryByExampleCli(unittest.TestCase):
    """The CLI wiring around --similar-to."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.db_path = self.tmp / "p.db"
        self.embedder = StubEmbedder()
        with ProgressDB(db_path=self.db_path) as db:
            self.paths = _make_images(self.tmp, "beach.jpg", "forest.jpg", "city.jpg")
            build_index(self.paths, db, self.embedder)

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        import contextlib
        import io

        from pyimgtag.main import main

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as exc:  # argparse errors
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_a_text_query_and_similar_to_are_mutually_exclusive(self):
        code, _, err = self._run(["search", "a beach", "--similar-to", str(self.tmp / "beach.jpg")])
        self.assertNotEqual(code, 0)
        self.assertIn("cannot be combined with --similar-to", err)

    def test_one_of_them_is_required(self):
        code, _, err = self._run(["search", "--db", str(self.db_path)])
        self.assertNotEqual(code, 0)
        self.assertIn("--similar-to", err)

    def test_similar_to_an_indexed_photo_needs_no_model(self):
        """No [search] extra, no model download: the stored vector is enough."""
        import unittest.mock as mock

        with mock.patch(
            "pyimgtag.search.embedder.load_embedder",
            side_effect=AssertionError("the fast path must not load a model"),
        ):
            code, out, _ = self._run(
                [
                    "search",
                    "--db",
                    str(self.db_path),
                    "--similar-to",
                    str(self.tmp / "beach.jpg"),
                    "--format",
                    "paths",
                ]
            )

        self.assertEqual(code, 0)
        listed = [line for line in out.splitlines() if line.strip()]
        self.assertNotIn(str(self.tmp / "beach.jpg"), listed, "the example ranked itself")
        self.assertEqual(len(listed), 2)

    def test_a_missing_file_says_so(self):
        code, _, err = self._run(
            ["search", "--db", str(self.db_path), "--similar-to", str(self.tmp / "nope.jpg")]
        )
        self.assertEqual(code, 1)
        self.assertIn("neither an indexed photo nor a readable image file", err)

    def test_an_unindexed_image_is_embedded_on_the_fly(self):
        import unittest.mock as mock

        fresh = self.tmp / "beach.jpg"  # name the stub knows
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        copy = outside / "beach.jpg"
        copy.write_bytes(b"not-a-real-image")

        with mock.patch("pyimgtag.search.embedder.load_embedder", return_value=self.embedder):
            code, out, _ = self._run(
                [
                    "search",
                    "--db",
                    str(self.db_path),
                    "--similar-to",
                    str(copy),
                    "--format",
                    "paths",
                ]
            )

        self.assertEqual(code, 0)
        # It embedded the copy and ranked the indexed beach photo first.
        # Compared resolved: the embedder is handed the resolved path, and on
        # macOS a tmp_path under /var resolves to /private/var.
        self.assertIn(copy.resolve(), self.embedder.embed_calls)
        self.assertEqual(out.splitlines()[0], str(fresh))

    def test_an_unindexed_library_is_reported_before_anything_else(self):
        empty = self.tmp / "empty.db"
        code, _, err = self._run(
            ["search", "--db", str(empty), "--similar-to", str(self.tmp / "beach.jpg")]
        )
        self.assertEqual(code, 1)
        self.assertIn("pyimgtag index", err)


class TestSchemaMigration(unittest.TestCase):
    def test_a_v14_database_upgrades_cleanly(self):
        """An existing library must gain the table without losing its rows."""
        tmp = Path(__import__("tempfile").mkdtemp())
        db_path = tmp / "old.db"

        # A database as it looked before semantic search existed.
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE processed_images (
                   file_path TEXT PRIMARY KEY, file_size INTEGER, file_mtime REAL,
                   status TEXT, processed_at TEXT, tags TEXT)"""
        )
        conn.execute(
            "INSERT INTO processed_images VALUES ('/photos/a.jpg', 1, 1.0, 'ok', 'now', '[]')"
        )
        conn.execute("PRAGMA user_version = 14")
        conn.commit()
        conn.close()

        with ProgressDB(db_path=db_path) as db:
            version = db._conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertGreaterEqual(version, 15)
            rows = db._conn.execute("SELECT COUNT(*) FROM processed_images").fetchone()[0]
            self.assertEqual(rows, 1)
            # And the new table is usable.
            self.assertEqual(db.embedding_stats()["count"], 0)


if __name__ == "__main__":
    unittest.main()


class TestSearchWebapp(unittest.TestCase):
    """The /search page and its API, driven through FastAPI's TestClient.

    No model is loaded: an empty index short-circuits before the embedder, and
    the stub covers the ranked path.
    """

    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
        except ImportError:  # pragma: no cover - review extra absent
            self.skipTest("fastapi not installed")
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.db = ProgressDB(db_path=self.tmp / "p.db")

    def tearDown(self):
        self.db.close()

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from pyimgtag.webapp.routes_search import build_search_router

        app = FastAPI()
        app.include_router(build_search_router(self.db, api_base="/search"), prefix="/search")
        return TestClient(app)

    def test_the_page_renders_with_the_nav_shell(self):
        response = self._client().get("/search/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("pyimgtag Search", response.text)
        # The nav-crawler smoke walks these links, so the entry has to be there.
        self.assertIn('href="/search"', response.text)
        self.assertIn('href="/query"', response.text)

    def test_an_unindexed_library_explains_itself_instead_of_erroring(self):
        payload = self._client().get("/search/api/search", params={"q": "a beach"}).json()
        self.assertFalse(payload["available"])
        self.assertIn("No semantic index yet", payload["message"])
        self.assertIn("pyimgtag index", payload["command"])

    def test_an_unexpected_model_failure_is_not_shown_to_the_browser(self):
        """py/stack-trace-exposure: only our own written-for-humans text ships.

        An ImportError or ModelDownloadError carries text we wrote to be read.
        Anything else could put a filesystem path or internal detail in front
        of a browser, so it is logged server-side and summarised in the reply.
        """
        import unittest.mock as mock

        (image,) = _make_images(self.tmp, "beach.jpg")
        self.db.upsert_embedding(image, "stub-v1", np.ones(4, dtype=np.float32))

        boom = RuntimeError("/home/someone/secret/path exploded at line 42")
        with mock.patch("pyimgtag.search.embedder.load_embedder", side_effect=boom):
            with self.assertLogs("pyimgtag.webapp.routes_search", level="ERROR"):
                payload = self._client().get("/search/api/search", params={"q": "a beach"}).json()

        self.assertFalse(payload["available"])
        self.assertNotIn("secret", payload["message"])
        self.assertNotIn("line 42", payload["message"])
        self.assertIn("server log", payload["message"])

    def test_a_missing_extra_still_tells_the_user_what_to_install(self):
        """A recoverable setup failure stays actionable without echoing the exception."""
        import unittest.mock as mock

        (image,) = _make_images(self.tmp, "beach.jpg")
        self.db.upsert_embedding(image, "stub-v1", np.ones(4, dtype=np.float32))

        with mock.patch(
            "pyimgtag.search.embedder.load_embedder",
            side_effect=ImportError("internal detail nobody should see"),
        ):
            payload = self._client().get("/search/api/search", params={"q": "a beach"}).json()

        self.assertIn("[search] extra", payload["message"])
        self.assertIn("pip install", payload["command"])
        # The reply is built from strings the route owns, never from the exception.
        self.assertNotIn("internal detail", payload["message"])

    def test_a_failed_download_hands_back_the_fix_command(self):
        import unittest.mock as mock

        from pyimgtag.search.model_cache import ModelDownloadError

        (image,) = _make_images(self.tmp, "beach.jpg")
        self.db.upsert_embedding(image, "stub-v1", np.ones(4, dtype=np.float32))

        error = ModelDownloadError("network unreachable", "curl -L -o /models/x.onnx https://h/x")
        with mock.patch("pyimgtag.search.embedder.load_embedder", side_effect=error):
            payload = self._client().get("/search/api/search", params={"q": "a beach"}).json()

        self.assertFalse(payload["available"])
        self.assertIn("curl -L -o", payload["command"])
        self.assertNotIn("network unreachable", payload["message"])

    def test_results_come_back_ranked(self):
        embedder = StubEmbedder()
        paths = _make_images(self.tmp, "beach.jpg", "forest.jpg")
        build_index(paths, self.db, embedder)

        import unittest.mock as mock

        with mock.patch("pyimgtag.search.embedder.load_embedder", return_value=embedder):
            payload = (
                self._client()
                .get("/search/api/search", params={"q": "sunny beach", "top": 2})
                .json()
            )

        self.assertTrue(payload["available"])
        self.assertEqual(Path(payload["results"][0]["file_path"]).name, "beach.jpg")
        self.assertGreater(payload["results"][0]["score"], payload["results"][1]["score"])

    def test_an_empty_query_is_rejected(self):
        self.assertEqual(
            self._client().get("/search/api/search", params={"q": ""}).status_code, 422
        )

    def test_the_lightbox_offers_more_like_this(self):
        text = self._client().get("/search/").text
        self.assertIn("More like this", text)
        self.assertIn("searchSimilarTo", text)
        # The deep link the other pages navigate to.
        self.assertIn("similar_to", text)

    def test_similar_to_ranks_without_loading_a_model(self):
        """The fast path through the API: an indexed photo needs no embedder."""
        import unittest.mock as mock

        embedder = StubEmbedder()
        paths = _make_images(self.tmp, "beach.jpg", "forest.jpg", "city.jpg")
        build_index(paths, self.db, embedder)
        beach = str(self.tmp / "beach.jpg")

        with mock.patch(
            "pyimgtag.search.embedder.load_embedder",
            side_effect=AssertionError("the fast path must not load a model"),
        ):
            payload = (
                self._client()
                .get("/search/api/search", params={"similar_to": beach, "top": 5})
                .json()
            )

        self.assertTrue(payload["available"])
        returned = [r["file_path"] for r in payload["results"]]
        self.assertNotIn(beach, returned, "the example ranked itself")
        self.assertEqual(len(returned), 2)

    def test_similar_to_an_unindexed_photo_says_what_to_do(self):
        embedder = StubEmbedder()
        build_index(_make_images(self.tmp, "beach.jpg"), self.db, embedder)

        payload = (
            self._client()
            .get("/search/api/search", params={"similar_to": "/not/in/the/index.jpg"})
            .json()
        )
        self.assertFalse(payload["available"])
        self.assertIn("not in the semantic index", payload["message"])
        self.assertIn("pyimgtag index", payload["command"])

    def test_q_and_similar_to_are_mutually_exclusive(self):
        for params in (
            {"q": "a beach", "similar_to": "/x.jpg"},
            {},
        ):
            with self.subTest(params=params):
                response = self._client().get("/search/api/search", params=params)
                self.assertEqual(response.status_code, 400)
                self.assertIn("either q or similar_to", response.json()["detail"])
