# This is AI generated code

"""The platform scheduler layer.

crony manages each entity as a per-platform scheduler unit: a launchd
LaunchAgent plist on macOS, a systemd `.service` (plus a `.timer` for
scheduled entries) on Linux. The `Scheduler` interface hides that split
behind one API over `UnitSpec`; `crony.platform.launchd` and
`crony.platform.systemd` implement it, and `get_scheduler` picks one for
the running host.

This layer translates one entity's platform-neutral `UnitSpec` into a
specific host scheduler's unit files and live state, and reports back
what that scheduler currently holds. Its whole responsibility, and its
whole vocabulary:

  - render a `UnitSpec` into unit file(s) (filename + content);
  - write, load, reload, trigger, and remove those files with the
    scheduler;
  - read back what is on disk and what the scheduler reports (paths,
    content, loaded / armed state, last exit).

It works from exactly two inputs -- the single `UnitSpec` it is handed,
and what it reads from the scheduler and disk -- and that is the entire
allowed surface. If a task needs anything beyond it, the task does not
belong here.

Concretely, this layer never decides whether one state matches, or
should differ from, another: it renders one spec, or reports one on-disk
/ scheduler fact, at a time. The types it defines are plain data
carriers; they hold no method that judges one instance against another
or decides whether two states match. (Their frozen-dataclass value
equality is fine -- a caller may compare two of them with `==`; what that
equality *means* is decided above this layer.) Any "does A match B /
should this change" question is answered above this layer, which hands
this layer a finished `UnitSpec` and interprets whatever this layer
reports.
"""

import abc
import subprocess
from dataclasses import dataclass
from pathlib import Path

import crony.errors
from crony.unit import UnitSpec, is_scheduled

# On-disk unit-naming prefix. Existing units are named
# `org.crony.<name>.plist` (launchd) / `crony-<name>.{service,timer}`
# (systemd), so this is a fixed contract: it stays "crony" regardless of
# how the entry script is invoked, and is deliberately not derived from
# the script filename.
UNIT_PREFIX = "crony"


@dataclass(frozen=True)
class RenderedUnit:
    """One platform unit -- its bare `filename` and its `content`. Both a
    `render_units` unit and an `ondisk_units` unit carry real content (a
    fresh render, or a file's bytes): a `RenderedUnit` exists only where
    there is something, so an absent file has no unit rather than an empty
    one. A plain data holder; the backend fills it, and what a unit's
    content means (a fresh render, a normalized form) is the caller's
    concern, not this layer's."""

    filename: Path
    content: str


@dataclass(frozen=True)
class RenderedUnits:
    """An ordered set of `RenderedUnit`s for one crony entity, config unit
    first. A plain data holder: callers read `.units`. `render_units`
    emits only the units the spec actually produces (a systemd `.timer`
    is present only for a scheduled entry); `ondisk_units` emits one per
    file actually present. A caller relating a render to disk aligns the
    two by `filename`."""

    units: tuple[RenderedUnit, ...] = ()


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

    @abc.abstractmethod
    def render_units(self, spec: UnitSpec) -> RenderedUnits:
        """`spec`'s platform units, in slot order (the config / service
        unit first, the companion next where the backend has one) -- only
        the slots this spec actually produces, each carrying a real render
        (a systemd `.timer` is included only for a scheduled entry). Each
        `content` is the render of `spec` for that unit, with whatever
        executable paths `spec.cmd` carries. To relate a render to what is
        on disk, align by `filename` against `ondisk_units`."""

    @abc.abstractmethod
    def config_filename(self, name: str) -> Path:
        """`name`'s primary (config / service) unit filename -- the one
        unit every entity has, independent of schedule or companions. The
        on-disk views lead with it when it is present; the model reads its
        presence as `unit_config_exists`."""

    @abc.abstractmethod
    def _discover_unit_files(self, name: str) -> list[Path]:
        """Every crony unit file for `name` currently on disk, in any
        order. A backend finds them by matching its filename namespace for
        `name` -- `<label>.<*>` -- not by enumerating a fixed slot list, so
        a leftover from a renamed companion scheme (an unknown suffix) is
        still found and can be cleaned. The dotted-prefix naming rule is
        what makes that namespace match only this entity's files."""

    def ondisk_units(self, name: str) -> RenderedUnits:
        """`name`'s on-disk units: one `RenderedUnit` per file actually
        present (the config unit first, then the discovered rest sorted),
        each carrying its file content. A pure disk read -- the on-disk
        counterpart to `render_units` -- with no comparison; the caller
        relates it to a render by `filename`. An absent file has no unit."""
        units: list[RenderedUnit] = []
        for filename in self._ondisk_slots(name):
            path = self.unit_dir / filename
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                # Vanished between discovery and read -- treat as absent.
                continue
            units.append(RenderedUnit(filename, content))
        return RenderedUnits(tuple(units))

    def unit_paths(self, name: str) -> tuple[Path | None, ...]:
        """`name`'s on-disk unit paths in slot order: the config unit
        first (its path, or None when absent), then every other discovered
        file sorted. A plain locate of the unit dir -- no content read, no
        comparison. Keeping the config slot lets status anchor it in the
        first path column even when its file is gone; the caller decides
        what to do with the paths."""
        paths: list[Path | None] = []
        for filename in self._ondisk_slots(name):
            path = self.unit_dir / filename
            paths.append(path if path.is_file() else None)
        return tuple(paths)

    def _ondisk_slots(self, name: str) -> list[Path]:
        """The on-disk-view slot order shared by `ondisk_units` and
        `unit_paths`: the config unit first (always, so `unit_paths` can
        anchor it in the first display column even when its file is
        absent), then every other discovered file sorted. `ondisk_units`
        skips a slot whose file is absent (present-only content);
        `unit_paths` reports None for it."""
        config = self.config_filename(name)
        rest = sorted(f for f in self._discover_unit_files(name) if f != config)
        return [config, *rest]

    def install(self, spec: UnitSpec, *, activate: bool = True) -> None:
        """Write `spec`'s live unit files, prune any stale unit files a
        prior install left that this spec no longer produces, and -- when
        `activate` -- load / re-arm every unit together so none carries a
        stale anchor. All files are written before any reload. `activate`
        is False only for a self-reload that fell through because the unit
        did not change (reloading would kill the running job for nothing).
        Whether the entry arms a schedule is `is_scheduled(spec.timing)`
        -- a disabled, on-demand, or transit entry renders dormant."""
        rendered = self.render_units(spec)
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        keep = {str(u.filename) for u in rendered.units}
        self.prune_units(str(spec.name), keep)
        for u in rendered.units:
            (self.unit_dir / u.filename).write_text(u.content, encoding="utf-8")
        if activate:
            self.activate(str(spec.name), scheduled=is_scheduled(spec.timing))

    @abc.abstractmethod
    def installed_cmd(self, name: str) -> list[str] | None:
        """The command argv embedded in `name`'s installed config unit,
        or None when no unit in the format `render` produces is present
        (missing, unreadable, or a different shape).

        The inverse of the `spec.cmd` embedding `render` performs.
        """

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
    def schedule_armed(self, name: str) -> bool:
        """Whether the scheduler has a live firing scheduled for `name`.

        `is_loaded` says the unit is registered; this says the registered
        schedule will actually fire. An entry with a separate arming unit
        (a systemd `.timer`) is armed only when that unit confirms a live
        next firing -- such a timer can be loaded and enabled yet have no
        valid anchor, reporting a next elapse of infinity so it never
        fires. An entry whose schedule rides on the loaded config unit
        (launchd, or a grouped / disabled entry with no timer) has no such
        state and is armed whenever loaded. Each backend hides that
        distinction and returns a plain armed / not-armed answer.
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

    # B027: the empty body is an intentional default no-op hook, not a
    # forgotten @abstractmethod -- most backends render no companion.
    def deactivate_jitter(self, _name: str) -> None:  # noqa: B027
        """Unload only the entity's jitter companion, leaving the service
        loaded -- the runner's self-unload of a fired companion. Defaults
        to a no-op: a backend that renders no jitter companion (its
        jitter, if any, is native) has nothing to unload. A backend that
        phases via a separate unit overrides this to unload that unit,
        tolerant of an already-absent companion."""

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
        """Fire `name` immediately (no-op if a run is already in flight).

        Raises `crony.errors.SubprocessError` when the scheduler rejects
        the fire, so a failed fire surfaces a proper crony exit code (the
        jitter companion and `crony trigger` both propagate it). A backend
        routes its fire through `_run_checked`, which converts a command
        failure to that type."""

    @staticmethod
    def _run_checked(argv: list[str]) -> None:
        """Run `argv`, raising `crony.errors.SubprocessError` on a non-zero
        exit so a failed scheduler command surfaces `ExitCode.SUBPROCESS`
        through the CLI instead of a bare `CalledProcessError` (which the
        CLI does not recognize, so it crashes with a traceback). This is
        the shared fire path for a backend's mutating scheduler commands
        (load, enable, restart, trigger); best-effort probes and teardowns
        that tolerate failure run their own `subprocess` calls directly."""
        result = subprocess.run(argv)
        if result.returncode != 0:
            raise crony.errors.SubprocessError(result.returncode, argv)

    @abc.abstractmethod
    def prune_units(self, name: str, keep: set[str]) -> None:
        """Remove `name`'s installed unit files not in `keep` (disabling
        them first) -- e.g. an orphaned `.timer` after a scheduled ->
        unscheduled transition. `keep` is the filename set `install`
        currently writes."""
