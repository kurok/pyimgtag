"""Tests for the extracted review router at a non-root prefix."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_review import build_review_router  # noqa: E402


def _seed_db(tmp_path):
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\x00" * 10)
    db.mark_done(
        img,
        ImageResult(
            file_path=str(img),
            file_name="a.jpg",
            tags=["sun"],
            scene_summary="sunny",
            cleanup_class="review",
        ),
    )
    return db_path


def test_review_router_mounted_at_prefix_serves_prefixed_paths(tmp_path):
    db = ProgressDB(db_path=_seed_db(tmp_path))
    app = FastAPI()
    app.include_router(build_review_router(db, api_base="/review"), prefix="/review")

    client = TestClient(app)
    r = client.get("/review/")
    assert r.status_code == 200
    assert "/review/api/stats" in r.text  # HTML uses prefixed URLs

    r = client.get("/review/api/stats")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = client.get("/review/api/images")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_review_router_mounted_at_root_serves_unprefixed_paths(tmp_path):
    db = ProgressDB(db_path=_seed_db(tmp_path))
    app = FastAPI()
    app.include_router(build_review_router(db, api_base=""))

    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_thumbnail_returns_404_when_make_thumbnail_fails(tmp_path):
    """If the DB has the image but PIL can't decode it, thumbnail must 404."""
    from unittest.mock import patch

    from pyimgtag.models import ImageResult
    from pyimgtag.progress_db import ProgressDB

    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    img = tmp_path / "broken.jpg"
    img.write_bytes(b"not an image")
    db.mark_done(
        img,
        ImageResult(file_path=str(img), file_name="broken.jpg", tags=[]),
    )

    app = FastAPI()
    app.include_router(build_review_router(db, api_base=""))
    client = TestClient(app)

    with patch("pyimgtag.webapp.routes_review._make_thumbnail", return_value=None):
        r = client.get(f"/thumbnail?path={img}")
    assert r.status_code == 404


def _seed_single_image(tmp_path, name="a.jpg", cleanup="review"):
    """Seed a DB with one image; return (db_path, img_path)."""
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    img = tmp_path / name
    img.write_bytes(b"\x00" * 10)
    db.mark_done(
        img,
        ImageResult(
            file_path=str(img),
            file_name=name,
            tags=["sun"],
            scene_summary="sunny",
            cleanup_class=cleanup,
        ),
    )
    db.close()
    return db_path, img


class TestThumbViaSips:
    """Cover the macOS sips HEIC fallback by mocking subprocess + shutil."""

    def test_returns_none_when_file_missing(self):
        from pyimgtag.webapp.routes_review import _thumb_via_sips

        assert _thumb_via_sips("/no/such/file.heic", 400) is None

    def test_returns_none_when_sips_not_on_path(self, tmp_path):
        from unittest.mock import patch

        from pyimgtag.webapp.routes_review import _thumb_via_sips

        f = tmp_path / "x.heic"
        f.write_bytes(b"\x00")
        with patch("shutil.which", return_value=None):
            assert _thumb_via_sips(str(f), 400) is None

    def test_returns_bytes_on_success(self, tmp_path):
        from unittest.mock import MagicMock, patch

        from pyimgtag.webapp import routes_review

        src = tmp_path / "x.heic"
        src.write_bytes(b"\x00")
        out = tmp_path / "out.jpg"
        out.write_bytes(b"JPEGDATA")

        tmp_handle = MagicMock()
        tmp_handle.name = str(out)
        ctx = MagicMock()
        ctx.__enter__.return_value = tmp_handle
        proc = MagicMock(returncode=0)

        with (
            patch("shutil.which", return_value="/usr/bin/sips"),
            patch("tempfile.NamedTemporaryFile", return_value=ctx),
            patch("subprocess.run", return_value=proc) as mrun,
        ):
            data = routes_review._thumb_via_sips(str(src), 400)
        assert data == b"JPEGDATA"
        assert mrun.called
        # sips consumes the temp file
        assert not out.exists()

    def test_returns_none_when_sips_nonzero(self, tmp_path):
        from unittest.mock import MagicMock, patch

        from pyimgtag.webapp import routes_review

        src = tmp_path / "x.heic"
        src.write_bytes(b"\x00")
        out = tmp_path / "out.jpg"
        out.write_bytes(b"JPEGDATA")

        tmp_handle = MagicMock()
        tmp_handle.name = str(out)
        ctx = MagicMock()
        ctx.__enter__.return_value = tmp_handle
        proc = MagicMock(returncode=1)

        with (
            patch("shutil.which", return_value="/usr/bin/sips"),
            patch("tempfile.NamedTemporaryFile", return_value=ctx),
            patch("subprocess.run", return_value=proc),
        ):
            assert routes_review._thumb_via_sips(str(src), 400) is None
        # The temp file must not be leaked on the failure path (regression).
        assert not out.exists()

    def test_exception_path_removes_temp_file(self, tmp_path):
        """A raising sips run must not leak the NamedTemporaryFile (regression)."""
        from unittest.mock import MagicMock, patch

        from pyimgtag.webapp import routes_review

        src = tmp_path / "x.heic"
        src.write_bytes(b"\x00")
        out = tmp_path / "out.jpg"
        out.write_bytes(b"JPEGDATA")

        tmp_handle = MagicMock()
        tmp_handle.name = str(out)
        ctx = MagicMock()
        ctx.__enter__.return_value = tmp_handle

        with (
            patch("shutil.which", return_value="/usr/bin/sips"),
            patch("tempfile.NamedTemporaryFile", return_value=ctx),
            patch("subprocess.run", side_effect=OSError("boom")),
        ):
            assert routes_review._thumb_via_sips(str(src), 400) is None
        assert not out.exists()

    def test_returns_none_on_subprocess_exception(self, tmp_path):
        from unittest.mock import patch

        from pyimgtag.webapp import routes_review

        src = tmp_path / "x.heic"
        src.write_bytes(b"\x00")
        with (
            patch("shutil.which", return_value="/usr/bin/sips"),
            patch("subprocess.run", side_effect=OSError("boom")),
        ):
            assert routes_review._thumb_via_sips(str(src), 400) is None


class TestMakeThumbnailBoundaries:
    def test_returns_none_when_pil_missing(self, monkeypatch):
        """If PIL import fails, _make_thumbnail returns None (CI parity)."""
        import builtins

        from pyimgtag.webapp.routes_review import _make_thumbnail

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("no PIL")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert _make_thumbnail("/whatever.jpg", 200) is None

    def test_cache_hit_returns_cached_bytes(self, tmp_path, monkeypatch):
        """A pre-existing cache file is returned without touching PIL."""
        import hashlib

        from pyimgtag.webapp import routes_review

        thumb_dir = tmp_path / "thumbs"
        thumb_dir.mkdir()
        monkeypatch.setattr(routes_review, "_THUMB_DIR", thumb_dir)

        image_path = "/some/image.jpg"
        size = 200
        cache_key = hashlib.sha256(f"{image_path}:{size}".encode()).hexdigest()
        (thumb_dir / f"{cache_key}.jpg").write_bytes(b"CACHED")

        assert routes_review._make_thumbnail(image_path, size) == b"CACHED"

    def test_heic_falls_back_to_sips(self, tmp_path, monkeypatch):
        """When PIL raises OSError on a .heic, the sips fallback supplies bytes."""
        from unittest.mock import patch

        from pyimgtag.webapp import routes_review

        thumb_dir = tmp_path / "thumbs"
        monkeypatch.setattr(routes_review, "_THUMB_DIR", thumb_dir)

        heic = tmp_path / "img.HEIC"
        heic.write_bytes(b"\x00")

        with (
            patch("PIL.Image.open", side_effect=OSError("cannot decode")),
            patch.object(routes_review, "_thumb_via_sips", return_value=b"SIPSJPEG"),
        ):
            data = routes_review._make_thumbnail(str(heic), 400)
        assert data == b"SIPSJPEG"
        # bytes are cached on success
        assert thumb_dir.exists()

    def test_returns_none_when_decode_and_sips_both_fail(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        from pyimgtag.webapp import routes_review

        thumb_dir = tmp_path / "thumbs"
        monkeypatch.setattr(routes_review, "_THUMB_DIR", thumb_dir)

        heic = tmp_path / "img.heic"
        heic.write_bytes(b"\x00")

        with (
            patch("PIL.Image.open", side_effect=OSError("cannot decode")),
            patch.object(routes_review, "_thumb_via_sips", return_value=None),
        ):
            assert routes_review._make_thumbnail(str(heic), 400) is None


class TestServeOriginal:
    def test_returns_none_for_missing_file(self):
        from pyimgtag.webapp.routes_review import _serve_original

        assert _serve_original("/no/such/file.jpg") is None

    def test_returns_bytes_and_mime_for_known_suffix(self, tmp_path):
        from pyimgtag.webapp.routes_review import _serve_original

        png = tmp_path / "x.png"
        png.write_bytes(b"PNGDATA")
        result = _serve_original(str(png))
        assert result == (b"PNGDATA", "image/png")

    def test_returns_none_on_oserror(self, tmp_path):
        from unittest.mock import patch

        from pyimgtag.webapp import routes_review

        f = tmp_path / "x.jpg"
        f.write_bytes(b"JPG")
        with patch("pathlib.Path.is_file", side_effect=OSError("io")):
            assert routes_review._serve_original(str(f)) is None

    def test_heic_decoded_to_jpeg(self, tmp_path):
        from unittest.mock import patch

        from pyimgtag.webapp import routes_review

        heic = tmp_path / "x.heic"
        heic.write_bytes(b"\x00")
        with patch.object(routes_review, "_make_thumbnail", return_value=b"JPEGBYTES"):
            assert routes_review._serve_original(str(heic)) == (b"JPEGBYTES", "image/jpeg")

    def test_heic_returns_none_when_decode_fails(self, tmp_path):
        from unittest.mock import patch

        from pyimgtag.webapp import routes_review

        heic = tmp_path / "x.heic"
        heic.write_bytes(b"\x00")
        with patch.object(routes_review, "_make_thumbnail", return_value=None):
            assert routes_review._serve_original(str(heic)) is None


class TestListImagesSingleFile:
    def test_file_query_returns_single_record(self, tmp_path):
        db_path, img = _seed_single_image(tmp_path)
        db = ProgressDB(db_path=db_path)
        app = FastAPI()
        app.include_router(build_review_router(db, api_base=""))
        client = TestClient(app)

        r = client.get(f"/api/images?file={img}")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["limit"] == 1
        assert data["items"][0]["file_path"] == str(img)

    def test_file_query_unknown_returns_empty(self, tmp_path):
        db_path, _ = _seed_single_image(tmp_path)
        db = ProgressDB(db_path=db_path)
        app = FastAPI()
        app.include_router(build_review_router(db, api_base=""))
        client = TestClient(app)

        r = client.get("/api/images?file=/no/such/path.jpg")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["items"] == []


class TestGetOriginal:
    def test_404_when_not_in_db(self, tmp_path):
        db_path, _ = _seed_single_image(tmp_path)
        db = ProgressDB(db_path=db_path)
        app = FastAPI()
        app.include_router(build_review_router(db, api_base=""))
        client = TestClient(app)

        r = client.get("/original?path=/no/such/path.jpg")
        assert r.status_code == 404

    def test_404_when_serve_original_fails(self, tmp_path):
        from unittest.mock import patch

        db_path, img = _seed_single_image(tmp_path)
        db = ProgressDB(db_path=db_path)
        app = FastAPI()
        app.include_router(build_review_router(db, api_base=""))
        client = TestClient(app)

        with patch("pyimgtag.webapp.routes_review._serve_original", return_value=None):
            r = client.get(f"/original?path={img}")
        assert r.status_code == 404

    def test_streams_original_bytes(self, tmp_path):
        from unittest.mock import patch

        db_path, img = _seed_single_image(tmp_path)
        db = ProgressDB(db_path=db_path)
        app = FastAPI()
        app.include_router(build_review_router(db, api_base=""))
        client = TestClient(app)

        with patch(
            "pyimgtag.webapp.routes_review._serve_original",
            return_value=(b"RAWBYTES", "image/png"),
        ):
            r = client.get(f"/original?path={img}")
        assert r.status_code == 200
        assert r.content == b"RAWBYTES"
        assert r.headers["content-type"] == "image/png"


class TestOpenInPhotos:
    def _client(self, tmp_path):
        db_path, img = _seed_single_image(tmp_path)
        db = ProgressDB(db_path=db_path)
        app = FastAPI()
        app.include_router(build_review_router(db, api_base=""))
        return TestClient(app), img

    def test_image_not_found(self, tmp_path):
        client, _ = self._client(tmp_path)
        r = client.post("/api/open-in-photos?path=/no/such/file.jpg")
        assert r.status_code == 200
        assert r.json() == {"ok": False, "error": "image_not_found"}

    def test_success(self, tmp_path):
        from unittest.mock import patch

        client, img = self._client(tmp_path)
        with patch("pyimgtag.applescript_writer.reveal_in_photos", return_value=None):
            r = client.post(f"/api/open-in-photos?path={img}")
        assert r.json() == {"ok": True}

    def test_platform_unsupported(self, tmp_path):
        from unittest.mock import patch

        client, img = self._client(tmp_path)
        with patch(
            "pyimgtag.applescript_writer.reveal_in_photos",
            return_value="Only supported on macOS hosts",
        ):
            r = client.post(f"/api/open-in-photos?path={img}")
        assert r.json()["error"] == "platform_unsupported"

    def test_timeout(self, tmp_path):
        from unittest.mock import patch

        client, img = self._client(tmp_path)
        with patch(
            "pyimgtag.applescript_writer.reveal_in_photos",
            return_value="osascript timed out after 10s",
        ):
            r = client.post(f"/api/open-in-photos?path={img}")
        assert r.json()["error"] == "photos_timeout"

    def test_unavailable(self, tmp_path):
        from unittest.mock import patch

        client, img = self._client(tmp_path)
        with patch(
            "pyimgtag.applescript_writer.reveal_in_photos",
            return_value="osascript not found",
        ):
            r = client.post(f"/api/open-in-photos?path={img}")
        assert r.json()["error"] == "photos_unavailable"

    def test_generic_error(self, tmp_path):
        from unittest.mock import patch

        client, img = self._client(tmp_path)
        with patch(
            "pyimgtag.applescript_writer.reveal_in_photos",
            return_value="something weird happened",
        ):
            r = client.post(f"/api/open-in-photos?path={img}")
        assert r.json()["error"] == "photos_error"


class TestBuildRouterImportError:
    def test_raises_when_fastapi_missing(self, tmp_path, monkeypatch):
        import builtins

        db_path, _ = _seed_single_image(tmp_path)
        db = ProgressDB(db_path=db_path)

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "fastapi" or name.startswith("fastapi."):
                raise ImportError("no fastapi")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match="fastapi and uvicorn are required"):
            build_review_router(db, api_base="")


class TestJudgedButUntaggedPreviews:
    """Regression: an image with a judge_scores row but no processed_images row
    must still preview. Previously the thumbnail/original endpoints resolved the
    path only via processed_images, so the whole Judge grid fell back to the
    filename-text placeholder."""

    def _seed_judge_only(self, tmp_path):
        from PIL import Image

        from pyimgtag.models import JudgeResult, JudgeScores

        db_path = tmp_path / "progress.db"
        db = ProgressDB(db_path=db_path)
        img = tmp_path / "judged.png"
        Image.new("RGB", (32, 32), "blue").save(img)  # a real, decodable image
        db.save_judge_result(
            JudgeResult(
                file_path=str(img),
                file_name="judged.png",
                weighted_score=10,
                scores=JudgeScores(verdict="great"),
            )
        )
        # Deliberately NO db.mark_done() — this image was judged, never tagged.
        assert db.get_image(str(img)) is None
        return db_path, img

    def test_thumbnail_serves_judged_but_untagged_image(self, tmp_path):
        db_path, img = self._seed_judge_only(tmp_path)
        db = ProgressDB(db_path=db_path)
        app = FastAPI()
        app.include_router(build_review_router(db, api_base=""))
        client = TestClient(app)

        r = client.get("/thumbnail", params={"path": str(img), "size": 200})
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"

    def test_original_serves_judged_but_untagged_image(self, tmp_path):
        db_path, img = self._seed_judge_only(tmp_path)
        db = ProgressDB(db_path=db_path)
        app = FastAPI()
        app.include_router(build_review_router(db, api_base=""))
        client = TestClient(app)

        r = client.get("/original", params={"path": str(img)})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/")

    def test_unknown_path_still_404s(self, tmp_path):
        db_path, _ = self._seed_judge_only(tmp_path)
        db = ProgressDB(db_path=db_path)
        app = FastAPI()
        app.include_router(build_review_router(db, api_base=""))
        client = TestClient(app)

        r = client.get("/thumbnail", params={"path": "/not/in/db.png"})
        assert r.status_code == 404


def test_review_page_template_markers(tmp_path):
    """Rendered page carries title, design CSS, and no unresolved placeholders."""
    import re

    from pyimgtag.webapp.routes_review import render_review_html

    html = render_review_html("/review")
    assert "<title>pyimgtag Review</title>" in html
    assert ":root{--bg:" in html  # nav_styles (design CSS) injected
    assert '<nav class="nav">' in html  # nav shell injected
    assert "/review/api/stats" in html  # api_base injected
    assert not re.findall(r"__[A-Z][A-Z0-9_]+__", html)
