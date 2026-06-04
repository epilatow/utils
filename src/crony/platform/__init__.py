# This is AI generated code

"""Per-host platform backends for crony.

Two sibling interfaces, each selected by the same platform string (as
produced by the entry script's platform detection):

- `Scheduler` (get_scheduler) renders and manages the per-host scheduler
  units, bound to the unit directory the caller manages, and verifies
  host-level scheduler health (raising `SchedulerWarning`).
- `HostPlatform` (get_host) brokers the non-unit host-OS services the
  runner and config tooling reach for (the runner's pid-exit wait, the
  keychain secret lookup, the keep-awake sleep-inhibitor wrap, and the
  desktop-interaction primitives for interactive jobs).

The launchd / systemd modules are re-exported so callers can reach their
pure filename helpers without importing the submodules directly; the
host backends are reached only through `get_host`, so they are exported
by class, not module.
"""

from __future__ import annotations

from pathlib import Path

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
    "get_host",
    "get_scheduler",
    "launchd",
    "systemd",
]


def get_scheduler(platform: str, unit_dir: Path) -> Scheduler:
    """Return the `Scheduler` backend for `platform` ('darwin' / 'linux'),
    managing units under `unit_dir`."""
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
