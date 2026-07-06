# This is AI generated code

"""The platform scheduler abstraction.

crony manages each entity as a per-platform scheduler unit: a launchd
LaunchAgent plist on macOS, a systemd `.service` (plus a `.timer` for
scheduled entries) on Linux. The `Scheduler` interface hides that split
behind one API over `UnitSpec`; `crony.platform.launchd` and
`crony.platform.systemd` implement it, and `get_scheduler` picks one for
the running host.
"""

import abc
from dataclasses import dataclass
from pathlib import Path

from crony.unit import UnitSpec

# On-disk unit-naming prefix. Existing units are named
# `org.crony.<name>.plist` (launchd) / `crony-<name>.{service,timer}`
# (systemd), so this is a fixed contract: it stays "crony" regardless of
# how the entry script is invoked, and is deliberately not derived from
# the script filename.
UNIT_PREFIX = "crony"


@dataclass(frozen=True)
class UnitLastExit:
    """The scheduler's record of a unit's most recent completed launch.

    `exit_status` is the launched process's wait status: 0 or a positive
    exit code, or a negative number whose magnitude is the terminating
    signal -- the `launchctl list` convention, which the systemd backend
    normalizes to. A unit with a launch in flight, or one the scheduler
    has no readable status for, is omitted from `unit_last_exits`
    entirely (its in-flight state is the lock's job, not this).
    """

    exit_status: int


class SchedulerWarning(Exception):
    """A non-fatal scheduler-health problem worth surfacing to the
    operator. Raised by `Scheduler.verify`; its message is operator-
    facing and includes any recommended fix command, so a caller can
    emit `str(exc)` verbatim."""


class Scheduler(abc.ABC):
    """Render and manage the platform units for crony entities."""

    # Whether picking up a changed unit file forces a reload that
    # terminates an in-flight run of that same unit. True for launchd
    # (the reload is unload+load, which kills the running job's process
    # group); False for systemd (`daemon-reload` leaves running units
    # untouched). `apply_one` reads this to refuse rewriting the unit of
    # the entry whose own runner is performing the apply, so the apply
    # can't reload itself to death. Each backend sets it explicitly.
    reload_terminates_running_job: bool

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

    def render(self, spec: UnitSpec) -> dict[str, str]:
        """`{filename: content}` for `spec`'s platform units, composed
        from `render_config` plus `render_timer`.

        Embeds `spec.cmd` as the unit's command and adds the schedule /
        priority directives. Callers that need one unit specifically
        reach for `render_config` / `render_timer` directly rather than
        re-splitting this dict by filename.
        """
        fname, content = self.render_config(spec)
        units = {str(fname): content}
        timer = self.render_timer(spec)
        if timer is not None:
            units[str(timer[0])] = timer[1]
        return units

    @abc.abstractmethod
    def render_config(self, spec: UnitSpec) -> tuple[Path, str]:
        """The (filename, content) of `spec`'s config unit -- the one
        that defines and runs the job (systemd `.service`, launchd
        plist). Always present. `filename` is the bare basename; the
        caller joins it onto the unit dir."""

    @abc.abstractmethod
    def render_timer(self, spec: UnitSpec) -> tuple[Path, str] | None:
        """The (filename, content) of `spec`'s schedule-arming timer unit
        (systemd `.timer`), or None when the backend has no separate
        timer (launchd embeds the schedule in the config unit) or `spec`
        is unscheduled."""

    @abc.abstractmethod
    def installed_cmd(self, name: str) -> list[str] | None:
        """The command argv embedded in `name`'s installed config unit,
        or None when no unit in the format `render` produces is present
        (missing, unreadable, or a different shape).

        The inverse of the `spec.cmd` embedding `render` performs.
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
    def is_loaded(self, name: str) -> bool:
        """Whether the scheduler has a unit by `name` loaded / registered
        (so it can be triggered). The operator-disabled state is not a
        scheduler fact -- a disabled entry installs an ordinary
        loaded-but-schedule-less unit, so it still reads loaded; the
        disabled overlay rides on the entry's snapshot
        (`Job.unit_disabled`)."""

    @abc.abstractmethod
    def schedule_armed(self, name: str) -> bool | None:
        """Whether the scheduler will actually fire `name`'s schedule.

        `is_loaded` says the unit is registered; this says the registered
        schedule has a live next firing. The two differ: a systemd
        interval `.timer` can be loaded and enabled yet have no valid
        anchor, so it reports a next elapse of infinity and never fires --
        loaded but dead.

        `True` -- a finite next firing exists (the schedule is armed).
        `False` -- the entry carries a loaded schedule that will never
        fire (a dead timer). Status reads this as `broken`.
        `None` -- not applicable / indeterminate: the entry has no
        schedule-arming timer (a grouped / disabled entry, or a backend
        whose config unit carries its own schedule and cannot enter this
        dead state), or the scheduler could not be queried. Never flagged.
        """

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
    def activate(self, name: str, *, scheduled: bool) -> None:
        """Load the unit (whose files the caller has already written)
        into the scheduler. `scheduled` is False for a schedule-less
        entry -- a grouped / transit unit or a disabled one -- that
        registers (loaded, triggerable) but does not arm a schedule."""

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
    def trigger(self, name: str) -> None:
        """Fire `name` immediately (no-op if a run is already in
        flight)."""

    @abc.abstractmethod
    def prune_units(self, name: str, keep: set[str]) -> None:
        """Remove `name`'s installed unit files not in `keep` (disabling
        them first) -- e.g. an orphaned `.timer` after a scheduled ->
        unscheduled transition. `keep` is the filename set `render`
        currently produces."""
