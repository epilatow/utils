# This is AI generated code

"""Per-host scheduler backends for crony.

`get_scheduler` returns the `Scheduler` for a platform string (as
produced by the entry script's platform detection), bound to the unit
directory the caller manages. The launchd and systemd modules are
re-exported so callers can reach their pure filename helpers without
importing the submodules directly.
"""

from __future__ import annotations

from pathlib import Path

from crony.platform import launchd, systemd
from crony.platform.launchd import LaunchdScheduler
from crony.platform.scheduler import UNIT_PREFIX, Scheduler, UnitState
from crony.platform.systemd import SystemdScheduler

__all__ = [
    "UNIT_PREFIX",
    "LaunchdScheduler",
    "Scheduler",
    "SystemdScheduler",
    "UnitState",
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
