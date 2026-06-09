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
import enum
from dataclasses import dataclass
from pathlib import Path

from crony.unit import UnitSpec

# On-disk unit-naming prefix. Existing units are named
# `org.crony.<name>.plist` (launchd) / `crony-<name>.{service,timer}`
# (systemd), so this is a fixed contract: it stays "crony" regardless of
# how the entry script is invoked, and is deliberately not derived from
# the script filename.
UNIT_PREFIX = "crony"


class UnitState(enum.Enum):
    """The platform scheduler's enable/disable view of a unit by name.

    ENABLED: the scheduler will fire it (loaded on launchd; `enabled` or
    `static` on systemd). DISABLED: instantiated but held off. NONE: the
    scheduler knows no unit by that name -- nothing to flip on or off.
    (Group-only entries, which have no own unit to enable, are the
    caller's concern, not a value this reports.)
    """

    ENABLED = "enabled"
    DISABLED = "disabled"
    NONE = "none"


@dataclass(frozen=True)
class UnitLastExit:
    """The scheduler's record of a unit's most recent completed launch.

    `exit_status` is the launched process's wait status: 0 or a positive
    exit code, or a negative number whose magnitude is the terminating
    signal -- the `launchctl list` convention, which the systemd backend
    normalizes to. A unit with a launch in flight, or one the scheduler
    has no readable status for, is omitted from `unit_last_exits`
    entirely (its in-flight state is the lock's job, not this).

    Reconciled against the run record by `RuntimeState.crashed`: the
    runner writes `last-run.json` and exits the process with that same
    code on every path it controls, so a status matching the recorded
    exit is a normal result. Anything else -- a signal (OOM, jetsam, a
    manual kill, macOS OS_REASON_CODESIGNING) or a nonzero exit reached
    before the runner recorded (e.g. a missing uv -> 127) -- is a launch
    that left no matching record, and whatever `last-run.json` holds is
    stale from an earlier launch.
    """

    exit_status: int


class SchedulerWarning(Exception):
    """A non-fatal scheduler-health problem worth surfacing to the
    operator. Raised by `Scheduler.verify`; its message is operator-
    facing and includes any recommended fix command, so a caller can
    emit `str(exc)` verbatim."""


def exec_paths_from_argv(argv: list[str]) -> tuple[Path, Path] | None:
    """Validate a crony unit's argv and return its `(uv, crony)` paths.

    Returns None unless `argv` is the expected
    `[uv, "run", "--script", crony, "run", "<bundle>:<uuid>"]` shape.
    The backends recover `argv` from their own file format (plist
    ProgramArguments / systemd ExecStart) and share this check.
    """
    if len(argv) != 6:
        return None
    if argv[1] != "run" or argv[2] != "--script" or argv[4] != "run":
        return None
    return Path(argv[0]), Path(argv[3])


class Scheduler(abc.ABC):
    """Render and manage the platform units for crony entities."""

    def __init__(self, unit_dir: Path | None = None) -> None:
        # Directory the host's units live in. Defaults to the backend's
        # `default_unit_dir()` (its standard per-OS location); a caller
        # may pass an explicit dir to redirect it -- which the tests do,
        # so they never touch the real unit directory.
        self.unit_dir = (
            unit_dir if unit_dir is not None else self.default_unit_dir()
        )

    @staticmethod
    @abc.abstractmethod
    def default_unit_dir() -> Path:
        """The backend's standard on-disk unit directory under the
        user's home. Used when no explicit dir is given."""

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

    @abc.abstractmethod
    def unit_config_path(self, name: str) -> Path | None:
        """The on-disk config unit file backing `name` -- the unit that
        defines and runs the job (systemd `.service`, launchd plist) --
        or None if absent."""

    @abc.abstractmethod
    def unit_timer_path(self, name: str) -> Path | None:
        """The on-disk timer unit that arms `name`'s schedule (systemd
        `.timer`), or None when the backend has no separate timer
        (launchd) or the entry is unscheduled."""

    @abc.abstractmethod
    def dispatch_unit_path(self, name: str) -> Path:
        """The unit file `trigger` fires for `name` (may not exist)."""

    @abc.abstractmethod
    def unit_name(self, name: str, scheduled: bool | None, /) -> str:
        """The unit identifier shown in status' UNIT NAME column -- the
        scheduler's own naming for `name`, independent of whether a file
        is on disk. `scheduled` selects the schedule-bearing unit where
        a backend installs more than one (systemd `.timer` vs
        `.service`); None means the caller couldn't determine it, so a
        backend that needs it to choose returns "" while one whose name
        is schedule-independent ignores it."""

    @abc.abstractmethod
    def installed_names(self) -> set[str]:
        """Every full name with a crony-shaped unit file in `unit_dir`.

        The name is the raw string embedded in the filename. A name
        that isn't a valid `<bundle>.<short>` (a hand-created or
        legacy stray) is still returned so status / destroy can reach
        and clean it up -- the scheduler keys on the unit name, not on
        entity identity.
        """

    @abc.abstractmethod
    def state(self, name: str) -> UnitState:
        """The scheduler's enable/disable state for the unit `name`."""

    @abc.abstractmethod
    def unit_last_exits(self) -> dict[str, UnitLastExit]:
        """Map every crony unit the scheduler knows to its last-launch
        outcome (`UnitLastExit`), in one bulk query.

        Keyed by the full `<bundle>.<short>` name. A unit the scheduler
        has no record for is simply absent from the map. Status reads
        this to tell a launch that ended without recording a result
        (killed, or exited before the runner wrote `last-run.json`)
        from a clean run, so a stale `last-run.json` isn't reported as
        the live outcome."""

    @abc.abstractmethod
    def is_stale(self, spec: UnitSpec) -> bool:
        """True when the installed units diverge from `spec` -- a file
        missing or not matching what `render` would produce, or a unit
        the scheduler has unloaded."""

    @abc.abstractmethod
    def activate(
        self, name: str, *, prior_disabled: bool, scheduled: bool
    ) -> None:
        """Load the unit (whose files the caller has already written)
        into the scheduler. `prior_disabled` restores a hand-disabled
        state across the reload; `scheduled` is False for a
        schedule-less entry that registers but does not arm."""

    @abc.abstractmethod
    def deactivate(self, name: str) -> None:
        """Remove the unit from the scheduler. Tolerant of an
        already-absent unit so destroy never fails on a missing one."""

    @abc.abstractmethod
    def remove_files(self, name: str) -> None:
        """Deactivate `name` and unlink every unit file backing it.
        Tolerant of an already-absent unit / missing files so destroy
        never fails on a partial install."""

    @abc.abstractmethod
    def verify(self) -> None:
        """Check host-level scheduler health. Returns None when healthy;
        raises `SchedulerWarning` (carrying an operator-facing message,
        with any recommended fix) for a non-fatal problem that would let
        scheduled jobs silently misbehave. Status / validate call this
        and surface the message."""

    @abc.abstractmethod
    def enable(self, name: str) -> None:
        """Move the scheduler to the `enabled` state for `name`."""

    @abc.abstractmethod
    def disable(self, name: str) -> None:
        """Move the scheduler to the `disabled` state for `name`."""

    @abc.abstractmethod
    def trigger(self, name: str) -> None:
        """Fire `name` immediately (no-op if a run is already in
        flight)."""

    @abc.abstractmethod
    def prune_units(self, name: str, keep: set[str]) -> None:
        """Remove `name`'s installed unit files not in `keep` (disabling
        them first) -- e.g. an orphaned `.timer` after a scheduled ->
        unscheduled transition. `keep` is the filename set `render`
        currently produces."""
