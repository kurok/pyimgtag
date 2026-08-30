"""Generate and install the OS service unit for ``pyimgtag watch``.

Unit generation is pure text (testable on every platform); installation
writes the file into the user's service directory and shells out to
``launchctl`` (macOS) or ``systemctl --user`` (Linux). Windows gets a
printed Task Scheduler recipe — no automation in v1.
"""

from __future__ import annotations

import html
import os
import shlex
import subprocess  # nosec B404 - fixed argv, no shell
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from platform import system as get_platform_name


def _xml_escape(value: str) -> str:
    """Escape ``& < >`` for plist string bodies (no XML parsing involved)."""
    return html.escape(value, quote=False)


__all__ = [
    "LAUNCHD_LABEL",
    "SYSTEMD_UNIT_NAME",
    "install_service",
    "render_launchd_plist",
    "render_systemd_unit",
    "service_argv",
    "uninstall_service",
    "unit_path",
]

LAUNCHD_LABEL = "com.pyimgtag.watch"
SYSTEMD_UNIT_NAME = "pyimgtag-watch.service"

# Flags that only make sense for the interactive install/uninstall call and
# must not be baked into the unit's ExecStart / ProgramArguments.
_INSTALL_ONLY_FLAGS = frozenset({"--install-service", "--uninstall-service", "--force"})

Runner = Callable[[Sequence[str]], object]


def _default_runner(argv: Sequence[str]) -> object:
    return subprocess.run(list(argv), check=False, capture_output=True, text=True)  # nosec B603


def service_argv(cli_args: Sequence[str], python: str | None = None) -> list[str]:
    """Return the argv the service should run.

    ``cli_args`` are the arguments after the program name (``sys.argv[1:]``);
    install-only flags are stripped and the interpreter + ``-m pyimgtag`` are
    prefixed so the unit does not depend on ``pyimgtag`` being on PATH.
    """
    kept = [a for a in cli_args if a not in _INSTALL_ONLY_FLAGS]
    return [python or sys.executable, "-m", "pyimgtag", *kept]


def unit_path(platform: str | None = None, home: Path | None = None) -> Path | None:
    """Where the unit file lives for *platform*; ``None`` where unsupported (Windows)."""
    plat = platform or get_platform_name()
    base = home or Path.home()
    if plat == "Darwin":
        return base / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    if plat == "Linux":
        return base / ".config" / "systemd" / "user" / SYSTEMD_UNIT_NAME
    return None


def render_launchd_plist(argv: Sequence[str], log_path: Path, label: str = LAUNCHD_LABEL) -> str:
    """Return a launchd user-agent plist that keeps ``argv`` running."""
    items = "\n".join(f"        <string>{_xml_escape(a)}</string>" for a in argv)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_xml_escape(label)}</string>
    <key>ProgramArguments</key>
    <array>
{items}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYIMGTAG_NO_WEB</key>
        <string>1</string>
        <key>PYIMGTAG_NO_UPDATE_CHECK</key>
        <string>1</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{_xml_escape(str(log_path))}</string>
    <key>StandardErrorPath</key>
    <string>{_xml_escape(str(log_path))}</string>
</dict>
</plist>
"""


def render_systemd_unit(argv: Sequence[str]) -> str:
    """Return a systemd *user* unit that keeps ``argv`` running."""
    exec_start = " ".join(shlex.quote(a) for a in argv)
    return f"""[Unit]
Description=pyimgtag watch — continuous incremental photo tagging
After=default.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=30
Environment=PYIMGTAG_NO_WEB=1
Environment=PYIMGTAG_NO_UPDATE_CHECK=1

[Install]
WantedBy=default.target
"""


def _windows_recipe(argv: Sequence[str]) -> str:
    cmd = subprocess.list2cmdline(list(argv))
    return (
        "Windows: service automation is not implemented. Create a scheduled task that\n"
        "starts at logon and keeps running, e.g. from an elevated PowerShell:\n\n"
        f'  schtasks /Create /TN "pyimgtag watch" /SC ONLOGON /RL LIMITED /TR "{cmd}"\n\n'
        "Remove it with:\n\n"
        '  schtasks /Delete /TN "pyimgtag watch" /F\n'
    )


def install_service(
    cli_args: Sequence[str],
    *,
    force: bool = False,
    platform: str | None = None,
    home: Path | None = None,
    runner: Runner | None = None,
    python: str | None = None,
) -> int:
    """Write and load the service unit. Returns a process exit code."""
    plat = platform or get_platform_name()
    argv = service_argv(cli_args, python=python)
    path = unit_path(plat, home)
    if path is None:
        print(_windows_recipe(argv), file=sys.stderr)
        return 0
    if path.exists() and not force:
        print(
            f"Error: {path} already exists — re-run with --force to overwrite it "
            "(or --uninstall-service first).",
            file=sys.stderr,
        )
        return 1
    run = runner or _default_runner
    path.parent.mkdir(parents=True, exist_ok=True)
    if plat == "Darwin":
        log_path = (home or Path.home()) / "Library" / "Logs" / "pyimgtag-watch.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            run(["launchctl", "unload", str(path)])
        path.write_text(render_launchd_plist(argv, log_path), encoding="utf-8")
        cmd = ["launchctl", "load", "-w", str(path)]
    else:
        path.write_text(render_systemd_unit(argv), encoding="utf-8")
        run(["systemctl", "--user", "daemon-reload"])
        cmd = ["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT_NAME]
    run(cmd)
    print(f"Wrote {path}", file=sys.stderr)
    print(f"Ran: {' '.join(shlex.quote(c) for c in cmd)}", file=sys.stderr)
    print(f"Service command: {' '.join(shlex.quote(a) for a in argv)}", file=sys.stderr)
    return 0


def uninstall_service(
    *,
    platform: str | None = None,
    home: Path | None = None,
    runner: Runner | None = None,
) -> int:
    """Unload and remove the unit written by :func:`install_service`."""
    plat = platform or get_platform_name()
    path = unit_path(plat, home)
    if path is None:
        print('Windows: run  schtasks /Delete /TN "pyimgtag watch" /F', file=sys.stderr)
        return 0
    if not path.exists():
        print(f"Nothing to do: {path} does not exist.", file=sys.stderr)
        return 0
    run = runner or _default_runner
    if plat == "Darwin":
        cmd = ["launchctl", "unload", "-w", str(path)]
    else:
        cmd = ["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME]
    run(cmd)
    try:
        os.remove(path)
    except OSError as exc:
        print(f"Error: could not remove {path}: {exc}", file=sys.stderr)
        return 1
    if plat != "Darwin":
        run(["systemctl", "--user", "daemon-reload"])
    print(f"Removed {path}", file=sys.stderr)
    print(f"Ran: {' '.join(shlex.quote(c) for c in cmd)}", file=sys.stderr)
    return 0
