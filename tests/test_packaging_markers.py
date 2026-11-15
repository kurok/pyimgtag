"""macOS-only requirements carry a darwin marker in every extra (#435).

Without it ``pip install "pyimgtag[all]"`` pulled photoscript/osxphotos (and,
through them, pyobjc) onto Linux and Windows, where pyobjc cannot build.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.markers import Marker
from packaging.requirements import Requirement

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = tomllib.loads((_ROOT / "pyproject.toml").read_text("utf-8"))
_EXTRAS: dict[str, list[str]] = _PYPROJECT["project"]["optional-dependencies"]

# Packages that only install (or only work) on macOS. Add any new Apple-only
# dependency here so the marker checks below cover it.
_MAC_ONLY = {"photoscript", "osxphotos"}


def _is_mac_only(name: str) -> bool:
    name = name.lower()
    return name in _MAC_ONLY or name.startswith("pyobjc")


_MAC_REQS = [
    pytest.param(extra, Requirement(spec), id=f"{extra}:{Requirement(spec).name}")
    for extra, specs in _EXTRAS.items()
    for spec in specs
    if _is_mac_only(Requirement(spec).name)
]


def test_the_mac_only_packages_are_all_found():
    names = {req.name.lower() for _, req in (p.values for p in _MAC_REQS)}
    assert {"photoscript", "osxphotos"} <= names
    extras = {extra for extra, _ in (p.values for p in _MAC_REQS)}
    assert {"photos", "photos-db", "ocr", "all"} <= extras


@pytest.mark.parametrize(("extra", "req"), _MAC_REQS)
@pytest.mark.parametrize(
    ("platform", "installed"), [("darwin", True), ("linux", False), ("win32", False)]
)
def test_mac_only_requirements_install_only_on_macos(extra, req, platform, installed):
    assert req.marker is not None, f"[{extra}] {req} has no marker"
    assert req.marker.evaluate({"sys_platform": platform, "extra": extra}) is installed


def test_the_lock_keeps_the_markers():
    lock = tomllib.loads((_ROOT / "uv.lock").read_text("utf-8"))
    (project,) = [p for p in lock["package"] if p["name"] == "pyimgtag"]
    for extra in ("photos", "photos-db", "ocr", "all"):
        for dep in project["optional-dependencies"][extra]:
            if _is_mac_only(dep["name"]):
                marker = Marker(dep.get("marker", ""))
                assert marker.evaluate({"sys_platform": "darwin"}), (extra, dep)
                assert not marker.evaluate({"sys_platform": "linux"}), (extra, dep)
                assert not marker.evaluate({"sys_platform": "win32"}), (extra, dep)


def test_classifiers_name_every_supported_platform():
    classifiers = set(_PYPROJECT["project"]["classifiers"])
    assert {
        "Operating System :: MacOS",
        "Operating System :: POSIX :: Linux",
        "Operating System :: Microsoft :: Windows",
    } <= classifiers
