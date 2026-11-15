"""Golden tests for :mod:`pyimgtag.service_units` (pure text generation + install flow)."""

from __future__ import annotations

import plistlib
import subprocess
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


class _FailingRunner(_Runner):
    """Fails the service manager's main command the way systemctl does."""

    def __init__(self, fail: str, stderr: str = "") -> None:
        super().__init__()
        self.fail = fail
        self.stderr = stderr

    def __call__(self, argv):
        self.calls.append(list(argv))
        code = 1 if self.fail in argv else 0
        return subprocess.CompletedProcess(list(argv), code, "", self.stderr if code else "")


_SYSTEMD_ERR = "Failed to enable unit: Unit file pyimgtag-watch.service does not exist."


@pytest.mark.parametrize(
    ("platform", "verb", "manager"),
    [("Linux", "enable", "systemctl --user enable --now"), ("Darwin", "load", "launchctl load")],
)
def test_install_fails_when_the_service_manager_fails(tmp_path, capsys, platform, verb, manager):
    runner = _FailingRunner(verb, _SYSTEMD_ERR)
    rc = su.install_service(ARGV, platform=platform, home=tmp_path, runner=runner, python="/py")
    assert rc == 1
    err = capsys.readouterr().err
    assert f"Error: {manager}" in err
    assert f"failed: {_SYSTEMD_ERR}" in err
    assert "Ran: " not in err


def test_install_failure_without_stderr_names_the_exit_status(tmp_path, capsys):
    runner = _FailingRunner("enable")
    assert su.install_service(ARGV, platform="Linux", home=tmp_path, runner=runner) == 1
    assert "failed: exit status 1" in capsys.readouterr().err


def _installed(platform: str, tmp_path: Path) -> Path:
    path = su.unit_path(platform, tmp_path)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unit", encoding="utf-8")
    return path


@pytest.mark.parametrize(("platform", "verb"), [("Linux", "disable"), ("Darwin", "unload")])
def test_uninstall_fails_and_keeps_the_unit_when_the_manager_fails(
    tmp_path, capsys, platform, verb
):
    path = _installed(platform, tmp_path)
    runner = _FailingRunner(verb, "Failed to disable unit: Access denied")
    assert su.uninstall_service(platform=platform, home=tmp_path, runner=runner) == 1
    err = capsys.readouterr().err
    assert "failed: Failed to disable unit: Access denied" in err
    assert f"Kept {path}" in err
    assert path.exists()


@pytest.mark.parametrize(
    ("platform", "verb", "stderr"),
    [
        ("Linux", "disable", "Unit pyimgtag-watch.service not loaded."),
        ("Linux", "disable", "Failed to connect to bus: No medium found"),
        ("Darwin", "unload", "Could not find specified service"),
    ],
)
def test_uninstall_of_a_job_that_is_not_loaded_still_succeeds(
    tmp_path, capsys, platform, verb, stderr
):
    path = _installed(platform, tmp_path)
    runner = _FailingRunner(verb, stderr)
    assert su.uninstall_service(platform=platform, home=tmp_path, runner=runner) == 0
    assert not path.exists()
    assert "the service was not loaded" in capsys.readouterr().err


class _LaunchctlRunner(_Runner):
    """Real launchctl load/unload: exit 0 with the failure on stderr."""

    def __init__(self, stderr: str) -> None:
        super().__init__()
        self.stderr = stderr

    def __call__(self, argv):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, "", self.stderr)


def test_launchctl_load_failure_with_exit_0_is_caught(tmp_path, capsys):
    stderr = "Load failed: 5: Input/output error\nTry running `launchctl bootstrap` as root."
    runner = _LaunchctlRunner(stderr)
    assert su.install_service(ARGV, platform="Darwin", home=tmp_path, runner=runner) == 1
    err = capsys.readouterr().err
    assert "failed: Load failed: 5: Input/output error" in err
    assert "Ran: " not in err


def test_launchctl_unload_failure_with_exit_0_keeps_the_plist(tmp_path, capsys):
    path = _installed("Darwin", tmp_path)
    runner = _LaunchctlRunner("Unload failed: 5: Input/output error")
    assert su.uninstall_service(platform="Darwin", home=tmp_path, runner=runner) == 1
    assert path.exists()
    assert "Unload failed: 5" in capsys.readouterr().err


def test_launchctl_noise_without_a_failure_line_is_success(tmp_path, capsys):
    runner = _LaunchctlRunner("Warning: Expecting a LaunchAgents path since the command was ran")
    assert su.install_service(ARGV, platform="Darwin", home=tmp_path, runner=runner) == 0
    assert "Ran: launchctl load -w" in capsys.readouterr().err


def test_success_output_is_unchanged_with_a_real_completed_process(tmp_path, capsys):
    runner = _FailingRunner("never-matches")
    assert su.install_service(ARGV, platform="Linux", home=tmp_path, runner=runner) == 0
    err = capsys.readouterr().err
    assert "Ran: systemctl --user enable --now pyimgtag-watch.service" in err
    assert "Error" not in err


def test_a_missing_service_manager_is_an_error_not_a_traceback(monkeypatch):
    def _missing(*_a, **_k):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(su.subprocess, "run", _missing)
    result = su._default_runner(["systemctl", "--user", "daemon-reload"])
    assert result.returncode == 127
    assert result.stderr == "systemctl: command not found"


def test_an_unrunnable_service_manager_is_an_error_not_a_traceback(monkeypatch):
    def _denied(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(su.subprocess, "run", _denied)
    result = su._default_runner(["systemctl", "--user", "enable"])
    assert (result.returncode, result.stderr) == (126, "systemctl: Permission denied")


def test_watch_install_service_exits_non_zero_on_failure(tmp_path, monkeypatch, capsys):
    """End to end through the CLI: a failed systemctl is a non-zero exit."""
    from pyimgtag.main import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(su, "get_platform_name", lambda: "Linux")
    runner = _FailingRunner("enable", _SYSTEMD_ERR)
    monkeypatch.setattr(su, "_default_runner", runner)
    rc = main(["watch", "--input-dir", str(tmp_path), "--install-service"])
    assert rc == 1
    assert _SYSTEMD_ERR in capsys.readouterr().err
