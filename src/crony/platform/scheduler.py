# This is AI generated code

"""The platform scheduler abstraction.

crony manages each entity as a per-platform scheduler unit: a launchd
LaunchAgent plist on macOS, a systemd `.service` (plus a `.timer` for
scheduled entries) on Linux. The `Scheduler` interface hides that split
behind one API over `UnitSpec`; `crony.platform.launchd` and
`crony.platform.systemd` implement it, and `get_scheduler` picks one for
the running host.
"""

from __future__ import annotations

import abc
from pathlib import Path

from crony.unit import UnitSpec

# On-disk unit-naming prefix. Existing units are named
# `org.crony.<name>.plist` (launchd) / `crony-<name>.{service,timer}`
# (systemd), so this is a fixed contract: it stays "crony" regardless of
# how the entry script is invoked, and is deliberately not derived from
# the script filename.
UNIT_PREFIX = "crony"


class Scheduler(abc.ABC):
    """Render and manage the platform units for crony entities."""

    @abc.abstractmethod
    def render(
        self, spec: UnitSpec, *, uv_path: Path, crony_path: Path
    ) -> dict[str, str]:
        """Return `{filename: content}` for `spec`'s platform units.

        `uv_path` / `crony_path` are baked into the unit's argv so it
        runs crony without relying on PATH -- platform schedulers start
        units with a minimal PATH that omits uv, and the caller resolves
        the live paths (or, for the drift check, the paths recovered from
        the installed unit) and passes them in.
        """
