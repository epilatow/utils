# This is AI generated code

"""Per-host platform backends for crony.

Two sibling interfaces, each selected by the same platform string (as
produced by `current_platform()`):

- `Scheduler` (get_scheduler) renders and manages the per-host scheduler
  units in a unit directory the backend resolves itself (or one the
  caller overrides), and verifies host-level scheduler health (raising
  `SchedulerWarning`).
- `HostPlatform` (get_host) brokers the non-unit host-OS services the
  runner and config tooling reach for (the runner's pid-exit wait, the
  keychain secret lookup, the keep-awake sleep-inhibitor wrap, and the
  desktop-interaction primitives for interactive jobs).

The launchd / systemd modules are re-exported so callers can reach their
pure filename helpers without importing the submodules directly; the
host backends are reached only through `get_host`, so they are exported
by class, not module.

`current_platform()` / `current_host()` detect the running host's
platform string and short hostname. Every caller routes host / platform
identity through them, so the tests redirect identity by patching them
here.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

from crony.errors import CronyError
from crony.platform import launchd, systemd
from crony.platform.darwin import DarwinHost
from crony.platform.host import HostPlatform, PidWait
from crony.platform.launchd import LaunchdScheduler
from crony.platform.linux import LinuxHost
from crony.platform.scheduler import (
    UNIT_PREFIX,
    Scheduler,
    SchedulerWarning,
    UnitState,
)
from crony.platform.systemd import SystemdScheduler

__all__ = [
    "UNIT_PREFIX",
    "DarwinHost",
    "HostPlatform",
    "LaunchdScheduler",
    "LinuxHost",
    "PidWait",
    "Scheduler",
    "SchedulerWarning",
    "SystemdScheduler",
    "UnitState",
    "current_host",
    "current_platform",
    "get_host",
    "get_scheduler",
    "launchd",
    "systemd",
]


def current_platform() -> str:
    """Return 'darwin' or 'linux'. Raise on anything else."""
    s = sys.platform
    if s == "darwin":
        return "darwin"
    if s.startswith("linux"):
        return "linux"
    raise CronyError(f"unsupported platform: {s}")


def current_host() -> str:
    """Return the short hostname (everything before the first dot)."""
    return socket.gethostname().split(".")[0]


def get_scheduler(platform: str, unit_dir: Path | None = None) -> Scheduler:
    """Return the `Scheduler` backend for `platform` ('darwin' / 'linux').

    `unit_dir` is the directory its units live in; omit it to use the
    backend's own default (its standard per-OS location under the user's
    home). Callers pass an explicit dir to redirect it -- which the
    tests do, so they never touch the real unit directory."""
    if platform == "darwin":
        return LaunchdScheduler(unit_dir)
    if platform == "linux":
        return SystemdScheduler(unit_dir)
    raise ValueError(f"unsupported platform: {platform!r}")


def get_host(platform: str) -> HostPlatform:
    """Return the `HostPlatform` backend for `platform` ('darwin' /
    'linux')."""
    if platform == "darwin":
        return DarwinHost()
    if platform == "linux":
        return LinuxHost()
    raise ValueError(f"unsupported platform: {platform!r}")
