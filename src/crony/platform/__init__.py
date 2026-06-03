# This is AI generated code

"""Per-host scheduler backends for crony.

`get_scheduler` returns the `Scheduler` for a platform string (as
produced by the entry script's platform detection). The launchd and
systemd modules are re-exported so callers can reach their pure
filename helpers without importing the submodules directly.
"""

from __future__ import annotations

from crony.platform import launchd, systemd
from crony.platform.scheduler import UNIT_PREFIX, Scheduler
from crony.platform.launchd import LaunchdScheduler
from crony.platform.systemd import SystemdScheduler

__all__ = [
    "UNIT_PREFIX",
    "LaunchdScheduler",
    "Scheduler",
    "SystemdScheduler",
    "get_scheduler",
    "launchd",
    "systemd",
]


def get_scheduler(platform: str) -> Scheduler:
    """Return the `Scheduler` backend for `platform` ('darwin' / 'linux')."""
    if platform == "darwin":
        return LaunchdScheduler()
    if platform == "linux":
        return SystemdScheduler()
    raise ValueError(f"unsupported platform: {platform!r}")
