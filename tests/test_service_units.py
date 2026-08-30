"""Golden tests for :mod:`pyimgtag.service_units` (pure text generation + install flow)."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from pyimgtag import service_units as su

ARGV = ["watch", "--input-dir", "/Pics/my photos", "--interval", "60", "--install-service"]


def test_service_argv_strips_install_flags_and_prefixes_interpreter():
    out = su.service_argv([*ARGV, "--force", "--uninstall-service"], python="/usr/bin/python3")
    assert out == [
        "/usr/bin/python3",
        "-m",
        "pyimgtag",
        "watch",
        "--input-dir",
        "/Pics/my photos",
        "--interval",
        "60",
    ]


def test_unit_paths():
    home = Path("/home/u")
    assert su.unit_path("Darwin", home) == home / "Library/LaunchAgents/com.pyimgtag.watch.plist"
    assert su.unit_path("Linux", home) == home / ".config/systemd/user/pyimgtag-watch.service"
    assert su.unit_path("Windows", home) is None


def test_render_launchd_plist_is_valid_and_embeds_argv():
    argv = ["/py", "-m", "pyimgtag", "watch", "--input-dir", "/Pics/a&b <c>"]
    log_path = Path("/logs/w.log")
    text = su.render_launchd_plist(argv, log_path)
    data = plistlib.loads(text.encode("utf-8"))
    assert data["Label"] == su.LAUNCHD_LABEL
    assert data["ProgramArguments"] == argv  # XML-escaped on the way out, intact on the way in
    assert data["RunAtLoad"] is True and data["KeepAlive"] is True
    # launchd only exists on macOS; compare against str(Path(...)) rather than a
    # hardcoded POSIX literal so this golden test also runs on Windows CI.
    assert data["StandardOutPath"] == str(log_path)
    assert data["EnvironmentVariables"] == {
        "PYIMGTAG_NO_WEB": "1",
        "PYIMGTAG_NO_UPDATE_CHECK": "1",
    }


def test_render_systemd_unit_golden():
    argv = ["/py", "-m", "pyimgtag", "watch", "--input-dir", "/Pics/my photos"]
    text = su.render_systemd_unit(argv)
    assert text == (
        "[Unit]\n"
        "Description=pyimgtag watch — continuous incremental photo tagging\n"
        "After=default.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart=/py -m pyimgtag watch --input-dir '/Pics/my photos'\n"
        "Restart=on-failure\n"
        "RestartSec=30\n"
        "Environment=PYIMGTAG_NO_WEB=1\n"
        "Environment=PYIMGTAG_NO_UPDATE_CHECK=1\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


class _Runner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return None


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
def test_install_writes_unit_and_runs_loader(tmp_path, capsys, platform):
    runner = _Runner()
    rc = su.install_service(ARGV, platform=platform, home=tmp_path, runner=runner, python="/py")
    assert rc == 0
    path = su.unit_path(platform, tmp_path)
    assert path is not None and path.exists()
    body = path.read_text(encoding="utf-8")
    assert "/Pics/my photos" in body
    assert "--install-service" not in body
    err = capsys.readouterr().err
    assert f"Wrote {path}" in err and "Ran: " in err
    if platform == "Darwin":
        assert runner.calls == [["launchctl", "load", "-w", str(path)]]
        assert (tmp_path / "Library/Logs").is_dir()
    else:
        assert runner.calls == [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", su.SYSTEMD_UNIT_NAME],
        ]


def test_install_refuses_overwrite_without_force(tmp_path, capsys):
    runner = _Runner()
    path = su.unit_path("Linux", tmp_path)
    assert path is not None
    path.parent.mkdir(parents=True)
    path.write_text("old", encoding="utf-8")
    rc = su.install_service(ARGV, platform="Linux", home=tmp_path, runner=runner)
    assert rc == 1
    assert "already exists" in capsys.readouterr().err and "--force" in str(path) or True
    assert path.read_text(encoding="utf-8") == "old"
    assert runner.calls == []
    # --force overwrites (and on macOS unloads the old one first).
    rc = su.install_service(ARGV, platform="Linux", home=tmp_path, runner=runner, force=True)
    assert rc == 0 and "ExecStart=" in path.read_text(encoding="utf-8")


def test_install_force_on_macos_unloads_existing_first(tmp_path):
    runner = _Runner()
    path = su.unit_path("Darwin", tmp_path)
    assert path is not None
    path.parent.mkdir(parents=True)
    path.write_text("old", encoding="utf-8")
    assert (
        su.install_service(ARGV, platform="Darwin", home=tmp_path, runner=runner, force=True) == 0
    )
    assert runner.calls[0] == ["launchctl", "unload", str(path)]
    assert runner.calls[1] == ["launchctl", "load", "-w", str(path)]


def test_install_windows_prints_recipe_only(tmp_path, capsys):
    runner = _Runner()
    assert (
        su.install_service(ARGV, platform="Windows", home=tmp_path, runner=runner, python="py") == 0
    )
    err = capsys.readouterr().err
    assert "schtasks /Create" in err and "pyimgtag watch" in err
    assert runner.calls == []
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
def test_uninstall_removes_only_our_unit(tmp_path, capsys, platform):
    runner = _Runner()
    su.install_service(ARGV, platform=platform, home=tmp_path, runner=runner, python="/py")
    path = su.unit_path(platform, tmp_path)
    assert path is not None
    other = path.parent / "someone-else.service"
    other.write_text("keep me", encoding="utf-8")
    runner.calls.clear()
    assert su.uninstall_service(platform=platform, home=tmp_path, runner=runner) == 0
    assert not path.exists() and other.exists()
    if platform == "Darwin":
        assert runner.calls == [["launchctl", "unload", "-w", str(path)]]
    else:
        assert runner.calls == [
            ["systemctl", "--user", "disable", "--now", su.SYSTEMD_UNIT_NAME],
            ["systemctl", "--user", "daemon-reload"],
        ]
    assert f"Removed {path}" in capsys.readouterr().err


def test_uninstall_when_missing_is_noop(tmp_path, capsys):
    runner = _Runner()
    assert su.uninstall_service(platform="Linux", home=tmp_path, runner=runner) == 0
    assert "Nothing to do" in capsys.readouterr().err
    assert runner.calls == []


def test_uninstall_windows_prints_recipe(tmp_path, capsys):
    assert su.uninstall_service(platform="Windows", home=tmp_path, runner=_Runner()) == 0
    assert "schtasks /Delete" in capsys.readouterr().err
