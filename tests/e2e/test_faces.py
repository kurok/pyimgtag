"""E2E Playwright tests for the /faces/ page.

Covers:
- page load without JS errors
- person count shown in status bar
- person card with thumbnail rendered after DB seeding
- hover shows the preview overlay, mouse-out hides it
- preview API returns a valid JPEG
- preview API returns 404 for an unknown face id

The test fixture seeds one person + one face directly into the running
server's SQLite DB (path discovered via ``/health``) so no face-scan is
required in CI.
"""

from __future__ import annotations

import sqlite3

import pytest
import requests

# ---------------------------------------------------------------------------
# Helpers (keep local — avoids cross-module imports from test_smoke.py)
# ---------------------------------------------------------------------------


def _assert_no_errors(page, label: str) -> None:
    problems: list[str] = []
    if page.bad_responses:  # type: ignore[attr-defined]
        problems.append(f"5xx on {label}: {page.bad_responses}")  # type: ignore[attr-defined]
    if page.page_errors:  # type: ignore[attr-defined]
        problems.append(f"JS errors on {label}: {page.page_errors}")  # type: ignore[attr-defined]
    if page.console_errors:  # type: ignore[attr-defined]
        problems.append(f"console errors on {label}: {page.console_errors}")  # type: ignore[attr-defined]
    assert not problems, "\n".join(problems)


def _make_test_jpeg(path) -> None:
    """Write a 200×200 solid-colour JPEG the server can open."""
    from PIL import Image

    Image.new("RGB", (200, 200), color=(180, 100, 60)).save(str(path), "JPEG")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def faces_test_data(base_url: str, tmp_path_factory):
    """Seed one person + one face into the running server's DB.

    Yields a dict with ``person_id``, ``face_id``, and ``image_path``.
    Cleans up after the module finishes.
    """
    db_path = requests.get(base_url + "/health", timeout=5).json()["db"]

    img_dir = tmp_path_factory.mktemp("faces_e2e")
    img_path = img_dir / "test_face.jpg"
    _make_test_jpeg(img_path)

    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO persons (label, confirmed, source, trusted) VALUES (?,1,'auto',0)",
            ("E2E Test Person",),
        )
        person_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        # bbox in detection space (200×200 image ≤ 1280 → no scaling needed)
        con.execute(
            "INSERT INTO faces (image_path, bbox_x, bbox_y, bbox_w, bbox_h, confidence, person_id)"
            " VALUES (?,50,50,80,80,0.95,?)",
            (str(img_path), person_id),
        )
        face_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.commit()
    finally:
        con.close()

    yield {"person_id": person_id, "face_id": face_id, "image_path": str(img_path)}

    con = sqlite3.connect(db_path)
    try:
        con.execute("DELETE FROM faces WHERE id = ?", (face_id,))
        con.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_faces_page_loads(page, base_url: str) -> None:
    """Faces page renders without JS errors or 5xx responses."""
    page.goto(base_url + "/faces/")
    page.wait_for_load_state("networkidle")
    _assert_no_errors(page, "/faces/")
    body = page.locator("body").inner_text().strip()
    assert len(body) >= 40, f"/faces/ body looks blank: {body!r}"
    assert page.locator("nav.nav a.nav-link").count() > 0, "/faces/ rendered without nav"


def test_faces_status_bar_shows_count(page, base_url: str, faces_test_data) -> None:
    """Status bar updates from 'Loading…' to '<N> person(s)' after load."""
    page.goto(base_url + "/faces/")
    page.wait_for_function(
        "!document.getElementById('status').textContent.includes('Loading')",
        timeout=5000,
    )
    status = page.locator("#status").inner_text()
    assert "person(s)" in status, f"Unexpected status text: {status!r}"


def test_faces_person_card_renders_with_thumbnail(page, base_url: str, faces_test_data) -> None:
    """Test person card appears and has a base64 JPEG thumbnail."""
    page.goto(base_url + "/faces/")
    page.wait_for_function(
        "!document.getElementById('status').textContent.includes('Loading')",
        timeout=5000,
    )

    card = page.locator(
        ".person-card", has=page.locator(".person-name", has_text="E2E Test Person")
    )
    assert card.count() == 1, "E2E test person card not found on /faces/"

    thumb = card.locator(".face-thumb").first
    assert thumb.count() == 1, "No thumbnail img in E2E person card"

    src = thumb.get_attribute("src") or ""
    assert src.startswith("data:image/jpeg;base64,"), (
        f"Thumbnail src is not a base64 JPEG data URI: {src[:80]!r}"
    )


def test_faces_hover_shows_and_hides_preview(page, base_url: str, faces_test_data) -> None:
    """Hovering a thumbnail shows the preview overlay; moving away hides it."""
    page.goto(base_url + "/faces/")
    page.wait_for_function(
        "!document.getElementById('status').textContent.includes('Loading')",
        timeout=5000,
    )

    card = page.locator(
        ".person-card", has=page.locator(".person-name", has_text="E2E Test Person")
    )
    thumb = card.locator(".face-thumb").first

    # Preview div is created by JS and initially hidden
    preview_selector = "div[style*='position:fixed'][style*='z-index:9999']"
    assert page.locator(preview_selector).count() == 1, "Preview overlay element not found in DOM"
    assert page.locator(preview_selector).evaluate("el => el.style.display") == "none", (
        "Preview overlay should be hidden before hover"
    )

    # Hover → preview becomes visible
    thumb.hover()
    page.wait_for_selector(preview_selector, state="visible", timeout=3000)

    # Move to top-left corner → preview hides
    page.mouse.move(0, 0)
    page.wait_for_selector(preview_selector, state="hidden", timeout=2000)


def test_faces_preview_api_returns_jpeg(base_url: str, faces_test_data) -> None:
    """Preview API returns a valid JPEG for a known face id."""
    face_id = faces_test_data["face_id"]
    r = requests.get(f"{base_url}/faces/api/faces/{face_id}/preview", timeout=5)
    assert r.status_code == 200, f"Preview API returned {r.status_code}: {r.text[:200]}"
    assert r.headers.get("content-type", "").startswith("image/jpeg"), (
        f"Unexpected content-type: {r.headers.get('content-type')}"
    )
    assert len(r.content) > 500, "Preview response is suspiciously small"


def test_faces_preview_api_404_for_unknown_id(base_url: str) -> None:
    """Preview API returns 404 for a non-existent face id."""
    r = requests.get(f"{base_url}/faces/api/faces/999999/preview", timeout=5)
    assert r.status_code == 404
