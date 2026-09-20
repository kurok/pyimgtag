"""Tests for ``pyimgtag immich-sync``.

Two kinds of check live here, and they cover different risks.

The *conformance* tests validate every request body this client builds against
``tests/fixtures/immich_openapi_subset.json`` -- a pinned slice of immich's own
published OpenAPI specification for the release named in
``immich.TESTED_IMMICH_VERSION``. They catch a payload that the server would
reject outright: a missing required field, a misspelled key under a schema that
forbids extras, a timestamp in a format the spec's pattern does not accept.

The *behaviour* tests run the whole command against a fake session and cover
what the spec cannot say anything about: which photo gets matched to which
asset, what a dry run is allowed to do, and whether running twice does
anything different from running once.

What neither kind proves is that a real immich server behaves the way its
specification says. That is why the command calls itself experimental.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pyimgtag import immich
from pyimgtag.immich import (
    MATCH_WINDOW,
    ImmichClient,
    ImmichError,
    match_asset,
    resolve_api_key,
)
from pyimgtag.models import ImageResult, JudgeResult, JudgeScores
from pyimgtag.progress_db import ProgressDB

SPEC_PATH = Path(__file__).parent / "fixtures" / "immich_openapi_subset.json"
SPEC = json.loads(SPEC_PATH.read_text())


# --- a very small OpenAPI validator ---------------------------------------
#
# Deliberately hand-written rather than pulling in ``jsonschema``: this needs
# to run in the default test environment, and the subset of draft-4 that
# immich's request bodies actually use is small enough to read in one screen.
# It raises on anything it cannot check, so a payload is never silently
# accepted because the validator did not understand its schema.


class SpecViolation(AssertionError):
    """A payload does not conform to the pinned specification."""


def _resolve(schema: dict, where: str) -> dict:
    ref = schema.get("$ref")
    if not ref:
        return schema
    name = ref.rsplit("/", 1)[-1]
    target = SPEC["components"]["schemas"].get(name)
    if target is None:
        raise SpecViolation(
            f"{where}: the pinned fixture has no schema {name!r}. Widen "
            f"tests/fixtures/immich_openapi_subset.json to cover it."
        )
    return target


def validate(value: Any, schema: dict, where: str = "$") -> None:
    """Check *value* against *schema*, raising :class:`SpecViolation`."""
    schema = _resolve(schema, where)
    if schema.get("nullable") and value is None:
        return
    kind = schema.get("type")

    if kind == "object" or "properties" in schema:
        if not isinstance(value, dict):
            raise SpecViolation(f"{where}: expected an object, got {type(value).__name__}")
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                raise SpecViolation(f"{where}: required property {name!r} is missing")
        for name, item in value.items():
            if name not in props:
                if schema.get("additionalProperties") is False:
                    raise SpecViolation(f"{where}: {name!r} is not a property of this schema")
                continue
            validate(item, props[name], f"{where}.{name}")
        return

    if kind == "array":
        if not isinstance(value, list):
            raise SpecViolation(f"{where}: expected an array, got {type(value).__name__}")
        if len(value) < schema.get("minItems", 0):
            raise SpecViolation(f"{where}: needs at least {schema['minItems']} item(s)")
        for index, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{where}[{index}]")
        return

    if kind == "string":
        if not isinstance(value, str):
            raise SpecViolation(f"{where}: expected a string, got {type(value).__name__}")
        if len(value) < schema.get("minLength", 0):
            raise SpecViolation(f"{where}: shorter than minLength")
        pattern = schema.get("pattern")
        if pattern and not re.match(pattern, value):
            raise SpecViolation(f"{where}: {value!r} does not match the spec's pattern")
        return

    if kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise SpecViolation(f"{where}: expected an integer, got {type(value).__name__}")
        if "minimum" in schema and value < schema["minimum"]:
            raise SpecViolation(f"{where}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SpecViolation(f"{where}: above maximum {schema['maximum']}")
        return

    if kind == "boolean":
        if not isinstance(value, bool):
            raise SpecViolation(f"{where}: expected a boolean, got {type(value).__name__}")
        return

    if kind is None and not schema:
        return
    raise SpecViolation(f"{where}: this validator does not understand type {kind!r}")


def body_schema(path: str, method: str) -> dict:
    """The request-body schema the spec gives for one operation."""
    operation = SPEC["paths"][path][method]
    return operation["requestBody"]["content"]["application/json"]["schema"]


# --- fake transport --------------------------------------------------------


class FakeSession:
    """Stands in for ``requests.Session``, recording everything sent."""

    def __init__(self, handler=None):
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self._handler = handler

    def request(self, method: str, url: str, json: Any = None, timeout: Any = None):
        path = url.split("/api", 1)[1] if "/api" in url else url
        self.calls.append((method, path, json))
        if json is not None:
            # Every body, in every test, is checked against the published
            # schema: a fake server is free to accept what a real one rejects.
            validate(json, body_schema(path, method.lower()), f"{method} {path}")
        return FakeResponse(*(self._handler(method, path, json) if self._handler else (200, {})))

    def sent(self, method: str, path: str) -> list[Any]:
        return [payload for m, p, payload in self.calls if m == method and p == path]


class FakeResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload
        if payload is None:
            self.text = ""
        elif payload is _UNREADABLE:
            self.text = "<not json>"
        else:
            self.text = json.dumps(payload)

    @property
    def content(self) -> bytes:
        return self.text.encode()

    def json(self) -> Any:
        if self._payload is _UNREADABLE:
            raise ValueError("not json")
        return self._payload


_UNREADABLE = object()


#: The spec constrains asset and tag ids to UUIDv4, so the fakes use real ones
#: -- ``"a1"`` would make the request bodies unvalidatable and the tests a
#: weaker check than the real server applies.
BEACH_ID = "11111111-1111-4111-8111-111111111111"
HILL_ID = "22222222-2222-4222-8222-222222222222"
OTHER_ID = "33333333-3333-4333-8333-333333333333"


def uuid_for(index: int) -> str:
    return f"{index:08x}-0000-4000-8000-000000000000"


def asset(asset_id: str, name: str, taken: str, **extra) -> dict:
    return {"id": asset_id, "originalFileName": name, "localDateTime": taken, **extra}


# --- conformance -----------------------------------------------------------


class TestRequestsConformToTheSpec(unittest.TestCase):
    """Every body this client builds is one the published spec allows."""

    def setUp(self):
        self.session = FakeSession()
        self.client = ImmichClient("https://immich.test", "k", session=self.session)

    def test_the_search_body_conforms(self):
        self.client.search_by_filename("IMG_0001.JPG", datetime(2024, 6, 1, 10, 0, 0))
        (payload,) = self.session.sent("POST", "/search/metadata")
        validate(payload, body_schema("/search/metadata", "post"))

    def test_the_search_body_conforms_without_a_date(self):
        self.client.search_by_filename("IMG_0001.JPG", None)
        (payload,) = self.session.sent("POST", "/search/metadata")
        validate(payload, body_schema("/search/metadata", "post"))
        self.assertNotIn("takenAt", payload["filter"])

    def test_the_tag_upsert_body_conforms(self):
        self.client.upsert_tags(["Places/Portugal", "sunset"])
        (payload,) = self.session.sent("PUT", "/tags")
        validate(payload, body_schema("/tags", "put"))

    def test_a_nested_tag_could_not_be_created_through_post_tags(self):
        """Why upsert and not create: ``TagCreateDto.name`` forbids a slash.

        This is the spec speaking, not a preference -- ``POST /tags`` cannot
        express ``Places/Portugal`` at all.
        """
        with self.assertRaises(SpecViolation):
            validate({"name": "Places/Portugal"}, body_schema("/tags", "post"))

    def test_the_bulk_tag_body_conforms(self):
        self.client.tag_assets([uuid_for(1), uuid_for(2)], [BEACH_ID, HILL_ID])
        (payload,) = self.session.sent("PUT", "/tags/assets")
        validate(payload, body_schema("/tags/assets", "put"))

    def test_the_favorite_body_conforms(self):
        self.client.set_favorite([BEACH_ID], True)
        (payload,) = self.session.sent("PUT", "/assets")
        validate(payload, body_schema("/assets", "put"))

    def test_no_field_deprecated_in_this_release_is_sent(self):
        """The 3.2.0 spec deprecated the flat search fields; we use ``filter``.

        Writing the deprecated form against the very release that deprecated
        it would ship code with a known expiry date.
        """
        self.client.search_by_filename("IMG_0001.JPG", datetime(2024, 6, 1, 10, 0, 0))
        (payload,) = self.session.sent("POST", "/search/metadata")
        props = SPEC["components"]["schemas"]["MetadataSearchDto"]["properties"]
        deprecated = sorted(k for k in payload if props.get(k, {}).get("deprecated"))
        self.assertEqual(deprecated, [])

    def test_timestamps_match_the_specs_date_time_pattern(self):
        """Both an aware and a naive datetime, and one carrying microseconds."""
        pattern = SPEC["components"]["schemas"]["DateFilter"]["properties"]["gte"]["pattern"]
        for value in (
            datetime(2024, 6, 1, 10, 0, 0),
            datetime(2024, 6, 1, 10, 0, 0, 123456),
            datetime(2024, 2, 29, 23, 59, 59, tzinfo=timezone(timedelta(hours=3))),
        ):
            with self.subTest(value=value):
                self.assertRegex(immich._isoformat(value), pattern)


class TestTheValidatorItselfRejectsThings(unittest.TestCase):
    """A validator that accepts everything would make the tests above empty."""

    def test_a_missing_required_field_is_caught(self):
        with self.assertRaises(SpecViolation):
            validate({"isFavorite": True}, body_schema("/assets", "put"))

    def test_an_unknown_filter_key_is_caught(self):
        """``SearchFilter`` sets ``additionalProperties: false``."""
        with self.assertRaises(SpecViolation):
            validate(
                {"filter": {"originalFilename": {"eq": "a.jpg"}}},
                body_schema("/search/metadata", "post"),
            )

    def test_a_malformed_timestamp_is_caught(self):
        with self.assertRaises(SpecViolation):
            validate(
                {"filter": {"takenAt": {"gte": "2024-06-01 10:00:00"}}},
                body_schema("/search/metadata", "post"),
            )

    def test_a_wrong_type_is_caught(self):
        with self.assertRaises(SpecViolation):
            validate(
                {"assetIds": BEACH_ID, "tagIds": [uuid_for(1)]},
                body_schema("/tags/assets", "put"),
            )

    def test_an_out_of_range_size_is_caught(self):
        with self.assertRaises(SpecViolation):
            validate({"size": 5000}, body_schema("/search/metadata", "post"))

    def test_an_unpinned_schema_is_an_error_not_a_pass(self):
        with self.assertRaises(SpecViolation):
            validate({"anything": 1}, {"$ref": "#/components/schemas/NotPinned"})


# --- matching --------------------------------------------------------------


class TestMatching(unittest.TestCase):
    """Who gets matched to what, and -- more importantly -- who does not."""

    taken = datetime(2024, 6, 1, 10, 0, 0)

    def test_an_exact_filename_and_date_matches(self):
        result = match_asset("a.jpg", self.taken, [asset("x", "a.jpg", "2024-06-01T10:00:00")])
        self.assertEqual(result.asset_id, "x")
        self.assertTrue(result.matched)

    def test_nothing_on_the_server_is_reported_not_guessed(self):
        result = match_asset("a.jpg", self.taken, [])
        self.assertIsNone(result.asset_id)
        self.assertIn("filename", result.reason)

    def test_a_photo_with_no_local_date_is_never_matched(self):
        """A filename alone is not evidence: every phone makes IMG_0001.JPG."""
        result = match_asset("a.jpg", None, [asset("x", "a.jpg", "2024-06-01T10:00:00")])
        self.assertIsNone(result.asset_id)
        self.assertIn("capture date", result.reason)

    def test_a_different_filename_does_not_match(self):
        """The server's search may be looser than an equality test."""
        result = match_asset("a.jpg", self.taken, [asset("x", "a-2.jpg", "2024-06-01T10:00:00")])
        self.assertIsNone(result.asset_id)

    def test_a_date_outside_the_window_does_not_match(self):
        far = self.taken + MATCH_WINDOW + timedelta(seconds=1)
        result = match_asset("a.jpg", self.taken, [asset("x", "a.jpg", far.isoformat())])
        self.assertIsNone(result.asset_id)
        self.assertIn("capture date did not", result.reason)

    def test_a_date_just_inside_the_window_matches(self):
        near = self.taken + MATCH_WINDOW - timedelta(seconds=1)
        result = match_asset("a.jpg", self.taken, [asset("x", "a.jpg", near.isoformat())])
        self.assertEqual(result.asset_id, "x")

    def test_two_viable_assets_are_ambiguous_never_a_coin_toss(self):
        result = match_asset(
            "a.jpg",
            self.taken,
            [
                asset("x", "a.jpg", "2024-06-01T10:00:00"),
                asset("y", "a.jpg", "2024-06-01T10:00:30"),
            ],
        )
        self.assertIsNone(result.asset_id)
        self.assertIn("ambiguous", result.reason)

    def test_an_aware_server_date_compares_with_a_naive_local_one(self):
        """immich returns offsets; EXIF usually does not."""
        result = match_asset(
            "a.jpg", self.taken, [asset("x", "a.jpg", "2024-06-01T10:00:00+02:00")]
        )
        self.assertEqual(result.asset_id, "x")

    def test_an_asset_with_no_usable_date_falls_back_to_fileCreatedAt(self):
        result = match_asset(
            "a.jpg",
            self.taken,
            [{"id": "x", "originalFileName": "a.jpg", "fileCreatedAt": "2024-06-01T10:00:00"}],
        )
        self.assertEqual(result.asset_id, "x")

    def test_an_asset_with_an_unreadable_date_is_skipped(self):
        result = match_asset("a.jpg", self.taken, [asset("x", "a.jpg", "not a date")])
        self.assertIsNone(result.asset_id)


# --- client plumbing -------------------------------------------------------


class TestClient(unittest.TestCase):
    def test_the_api_prefix_is_added_once(self):
        for given in ("https://immich.test", "https://immich.test/", "https://immich.test/api"):
            with self.subTest(url=given):
                client = ImmichClient(given, "k", session=FakeSession())
                self.assertEqual(client.base_url, "https://immich.test/api")

    def test_the_key_travels_in_the_header_not_the_url(self):
        session = FakeSession()
        ImmichClient("https://immich.test", "secret", session=session)
        self.assertEqual(session.headers["x-api-key"], "secret")

    def test_an_error_status_carries_the_servers_own_words(self):
        session = FakeSession(lambda *_: (401, {"message": "Invalid API key"}))
        client = ImmichClient("https://immich.test", "k", session=session)
        with self.assertRaises(ImmichError) as caught:
            client.list_tags()
        self.assertIn("401", str(caught.exception))
        self.assertIn("Invalid API key", str(caught.exception))

    def test_unreadable_json_is_an_immich_error_not_a_value_error(self):
        session = FakeSession(lambda *_: (200, _UNREADABLE))
        client = ImmichClient("https://immich.test", "k", session=session)
        with self.assertRaises(ImmichError):
            client.list_tags()

    def test_an_empty_bulk_call_sends_nothing(self):
        """Guards against a PUT with an empty array, which the spec forbids."""
        session = FakeSession()
        client = ImmichClient("https://immich.test", "k", session=session)
        client.tag_assets([], [BEACH_ID])
        client.tag_assets([uuid_for(1)], [])
        client.set_favorite([])
        self.assertEqual(session.calls, [])


class TestApiKeyResolution(unittest.TestCase):
    def test_an_explicit_key_wins(self):
        with unittest.mock.patch.dict("os.environ", {immich.API_KEY_ENV: "env"}):
            self.assertEqual(resolve_api_key("flag"), "flag")

    def test_the_environment_is_the_fallback(self):
        with unittest.mock.patch.dict("os.environ", {immich.API_KEY_ENV: "env"}):
            self.assertEqual(resolve_api_key(None), "env")

    def test_no_key_anywhere_names_the_environment_variable(self):
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ImmichError) as caught:
                resolve_api_key(None)
        self.assertIn(immich.API_KEY_ENV, str(caught.exception))


# --- the command, end to end against a fake server -------------------------


class FakeServer:
    """Just enough immich to run the command against."""

    def __init__(self, assets: list[dict], tags: list[dict] | None = None):
        self.assets = assets
        self.tags = list(tags or [])
        self._next = 0
        self.created: list[str] = []
        self._seen = {t["value"] for t in self.tags}

    def __call__(self, method: str, path: str, payload: Any):
        if (method, path) == ("POST", "/search/metadata"):
            wanted = payload["filter"]["originalFileName"]["eq"]
            items = [a for a in self.assets if a["originalFileName"] == wanted]
            return 200, {"assets": {"items": items, "total": len(items)}}
        if (method, path) == ("GET", "/tags"):
            return 200, list(self.tags)
        if (method, path) == ("PUT", "/tags"):
            known = {t["value"]: t for t in self.tags}
            for name in payload["tags"]:
                if name not in known:
                    self._next += 1
                    created = {"id": uuid_for(self._next), "value": name, "name": name}
                    self.tags.append(created)
                    known[name] = created
            self.created.extend(n for n in payload["tags"] if n not in self._seen)
            self._seen.update(payload["tags"])
            return 200, [known[n] for n in payload["tags"]]
        if method == "PUT":
            return 200, []
        raise AssertionError(f"unexpected call {method} {path}")


class CommandCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "p.db"
        self.db = ProgressDB(db_path=self.db_path)
        self._seed()
        self.db.close()

    def _store(self, name: str, **kw) -> Path:
        path = self.tmp / name
        path.write_bytes(b"x")
        self.db.mark_done(path, ImageResult(file_path=str(path), processing_status="ok", **kw))
        return path

    def _seed(self) -> None:
        self.beach = self._store(
            "beach.jpg", tags=["sunset", "beach"], image_date="2024-06-01T10:00:00"
        )
        self.hill = self._store("hill.jpg", tags=["hills"], image_date="2024-06-02T10:00:00")
        self.db.save_judge_result(
            JudgeResult(
                file_path=str(self.beach),
                file_name="beach.jpg",
                scores=JudgeScores(score=9, reason="light"),
                weighted_score=9,
            )
        )

    def run_sync(self, server: FakeServer, *extra: str) -> tuple[int, str, FakeSession]:
        from pyimgtag.main import main

        session = FakeSession(server)
        real_client = immich.ImmichClient

        def factory(url, api_key, **_):
            return real_client(url, api_key, session=session)

        argv = ["immich-sync", "--url", "https://immich.test", *extra, "--db", str(self.db_path)]
        err = io.StringIO()
        with unittest.mock.patch.object(immich, "ImmichClient", factory):
            with unittest.mock.patch.dict("os.environ", {immich.API_KEY_ENV: "k"}):
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    try:
                        code = main(argv)
                    except SystemExit as exc:
                        code = int(exc.code or 0)
        return code, err.getvalue(), session


class TestCommand(CommandCase):
    def _server(self) -> FakeServer:
        return FakeServer(
            [
                asset(BEACH_ID, "beach.jpg", "2024-06-01T10:00:00"),
                asset(HILL_ID, "hill.jpg", "2024-06-02T10:00:00"),
            ]
        )

    def test_a_dry_run_writes_absolutely_nothing(self):
        """The default. A tagger that writes before you asked is a bad tagger."""
        code, err, session = self.run_sync(self._server())
        self._assert_no_writes(code, err, session)

    def test_an_explicit_dry_run_writes_absolutely_nothing(self):
        code, err, session = self.run_sync(self._server(), "--dry-run")
        self._assert_no_writes(code, err, session)

    def test_a_dry_run_says_how_many_tags_are_new(self):
        """ "12 tags to push" reads very differently from "12, all of them new"."""
        server = FakeServer(
            [asset(BEACH_ID, "beach.jpg", "2024-06-01T10:00:00")],
            tags=[{"id": uuid_for(99), "value": "sunset"}],
        )
        _, err, session = self.run_sync(server)
        self.assertEqual(len(session.sent("GET", "/tags")), 1)
        self.assertIn("1 of 2 tag(s) would be created", err)

    def test_apply_and_dry_run_together_is_an_error_not_a_silent_write(self):
        code, _, session = self.run_sync(self._server(), "--apply", "--dry-run")
        self.assertEqual(code, 2)
        self.assertEqual(session.calls, [])

    def _assert_no_writes(self, code, err, session):
        self.assertEqual(code, 0)
        writes = [
            (m, p) for m, p, _ in session.calls if m in ("POST", "PUT") and p != "/search/metadata"
        ]
        self.assertEqual(writes, [])
        self.assertIn("Dry run", err)

    def test_apply_pushes_tags_grouped_one_call_per_tag(self):
        """Grouped by tag, not by photo: a 40,000-photo library would
        otherwise mean 40,000 requests."""
        code, _, session = self.run_sync(self._server(), "--apply")
        self.assertEqual(code, 0)
        pushed = session.sent("PUT", "/tags/assets")
        self.assertEqual(len(pushed), 3)  # sunset, beach, hills
        for payload in pushed:
            self.assertEqual(len(payload["tagIds"]), 1)

    def test_all_tags_are_resolved_in_one_call(self):
        _, _, session = self.run_sync(self._server(), "--apply")
        (payload,) = session.sent("PUT", "/tags")
        self.assertEqual(sorted(payload["tags"]), ["beach", "hills", "sunset"])

    def test_an_existing_tag_is_reused_not_recreated(self):
        server = FakeServer(
            [asset(BEACH_ID, "beach.jpg", "2024-06-01T10:00:00")],
            tags=[{"id": uuid_for(99), "value": "sunset"}],
        )
        self.run_sync(server, "--apply")
        self.assertNotIn("sunset", server.created)
        self.assertIn("beach", server.created)
        self.assertEqual(len([t for t in server.tags if t["value"] == "sunset"]), 1)

    def test_a_second_run_creates_no_duplicate_tags(self):
        """Re-syncing an unchanged library must be a no-op on the server."""
        server = self._server()
        self.run_sync(server, "--apply")
        names_after_first = sorted(t["value"] for t in server.tags)
        server.created.clear()
        self.run_sync(server, "--apply")
        self.assertEqual(server.created, [])
        self.assertEqual(sorted(t["value"] for t in server.tags), names_after_first)

    def test_an_unmatched_photo_is_named_with_its_reason(self):
        server = FakeServer([asset(BEACH_ID, "beach.jpg", "2024-06-01T10:00:00")])
        _, err, session = self.run_sync(server, "--apply")
        self.assertIn("hill.jpg", err)
        self.assertIn("1 unmatched", err)
        tagged = {i for p in session.sent("PUT", "/tags/assets") for i in p["assetIds"]}
        self.assertEqual(tagged, {BEACH_ID})

    def test_favorites_follow_the_threshold(self):
        _, _, session = self.run_sync(self._server(), "--apply", "--favorite-min-score", "8")
        (payload,) = session.sent("PUT", "/assets")
        self.assertEqual(payload["ids"], [BEACH_ID])  # hill.jpg has no score
        self.assertTrue(payload["isFavorite"])

    def test_without_the_threshold_nothing_is_favorited(self):
        _, _, session = self.run_sync(self._server(), "--apply")
        self.assertEqual(session.sent("PUT", "/assets"), [])

    def test_a_threshold_above_every_score_favorites_nothing(self):
        _, _, session = self.run_sync(self._server(), "--apply", "--favorite-min-score", "10")
        self.assertEqual(session.sent("PUT", "/assets"), [])

    def test_the_tag_prefix_is_applied(self):
        _, _, session = self.run_sync(self._server(), "--apply", "--tag-prefix", "pyimgtag/")
        (payload,) = session.sent("PUT", "/tags")
        self.assertTrue(all(n.startswith("pyimgtag/") for n in payload["tags"]), payload)

    def test_limit_stops_early(self):
        _, _, session = self.run_sync(self._server(), "--limit", "1")
        self.assertEqual(len(session.sent("POST", "/search/metadata")), 1)

    def test_a_server_error_is_a_message_not_a_traceback(self):
        def broken(method, path, payload):
            return 500, {"message": "boom"}

        code, err, _ = self.run_sync(broken)
        self.assertEqual(code, 1)
        self.assertIn("Error:", err)
        self.assertNotIn("Traceback", err)

    def test_the_tested_version_is_announced(self):
        """A version mismatch should be visible before it becomes a 404."""
        _, err, _ = self.run_sync(self._server())
        self.assertIn(immich.TESTED_IMMICH_VERSION, err)


if __name__ == "__main__":
    unittest.main()
