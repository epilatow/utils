# This is AI generated code

"""crony's in-memory domain model.

The Job / JobGroup entities and the Config graph that ties the pending
config view to the current applied view, plus the runtime-state and
last-run value types those graphs reference. Holds the pure
config->graph construction (cascade resolution + host/platform
selection) and the translation between a node and its snapshot.json
form (`snapshot_from_dict` / `<node>.to_snapshot`). The on-disk format
itself -- the typed model, migration, and schema versions -- lives in
crony.snapshot.

The model does no I/O of its own: no disk reads, no locks, no live
scheduler-state queries -- those live in crony.runtime. It may render
platform units through crony.platform (a pure string transform of an
already-resolved entry), which it uses to bake each node's normalized
config / timer units for the `config=stale` comparison.
"""

import dataclasses
import datetime
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

import crony.config
import crony.errors
import crony.paths
import crony.platform
import crony.snapshot
import crony.unit
from crony.platform.fda import FDAWrapper

# =============================================================================
# APPLIED SNAPSHOT
# =============================================================================
# Apply pins each entry's behavior-relevant runtime parameters
# into a JSON snapshot next to the platform unit. The runner
# reads these pinned fields, so editing the toml without `apply`
# has no effect on running units. Drift detection compares the
# freshly-resolved snapshot to the on-disk one via dataclass
# equality, so any divergence between live config and applied
# state surfaces as `config=stale` in `crony status`.
#
# notify_channels are deliberately NOT pinned -- routing is
# separable from "what runs", and notify edits should take effect
# without a re-apply. The runner reads them from the live config
# at fire time.


# The per-entry log file name, kept in one place so no caller inlines
# the literal. Lives inside the entry's state dir (uuid-keyed) and is
# reachable through the short-name alias.
RUN_LOG_NAME: str = "run.log"

# The on-disk snapshot format -- the `JobSnapshot` / `GroupSnapshot`
# models and the `CURRENT_SNAPSHOT_SCHEMA` / `COMPAT_SNAPSHOT_SCHEMA`
# constants -- lives in `crony.snapshot`. The node <-> snapshot
# translation (`snapshot_from_dict`, `<node>.to_snapshot`) is below.


# Hidden crony subcommand the platform unit invokes to perform a run.
# The leading underscore marks it internal (matching `_run-guard`): end
# users fire jobs via `crony trigger`, never by calling this directly.
RUN_SUBCOMMAND = "_run"

# Temporary back-compat alias for RUN_SUBCOMMAND. Units installed before
# the `run` -> `_run` rename bake the old `run` token into their argv;
# keeping it accepted lets those units keep firing until a `crony apply`
# re-renders them. Remove once no `run`-baked units remain on any host.
RUN_SUBCOMMAND_LEGACY = "run"

# Seconds added to a capped entry's timeout to form the hard guard's
# wallclock cap. The cap is looser than the entry timeout so the runner's
# own soft timeout fires first (keeping the clean last-run.json
# reporting); the guard is the last-resort backstop for a runner that
# wedges before honoring its deadline. The padding covers crony startup
# plus the soft timeout's SIGTERM->SIGKILL grace.
_HARD_TIMEOUT_PADDING_SEC = 60

# Hidden crony subcommand that wraps a run in a hard wallclock cap: it
# launches the inner `crony _run` in its own session and kills that
# process group if the cap elapses.
GUARD_SUBCOMMAND = "_run-guard"


def _run_argv(
    uv_path: Path, crony_path: Path, ref: crony.unit.EntityRef
) -> tuple[str, ...]:
    """The argv a unit uses to invoke the runner for `ref`.

    The absolute uv / crony paths are baked in because platform
    schedulers start a unit with a minimal PATH that omits uv; the
    runner is addressed by `<bundle>:<uuid>` so it skips the name lookup.
    """
    return (
        str(uv_path),
        "run",
        "--script",
        str(crony_path),
        RUN_SUBCOMMAND,
        str(ref),
    )


def _guarded_argv(
    uv_path: Path,
    crony_path: Path,
    ref: crony.unit.EntityRef,
    timeout: int,
) -> tuple[str, ...]:
    """The unit's full run command. A positive `timeout` wraps the base
    run in the hard-timeout guard (cap = timeout + padding); an uncapped
    entry (`timeout <= 0`) runs the base argv directly, no guard."""
    base = _run_argv(uv_path, crony_path, ref)
    if timeout <= 0:
        return base
    cap = timeout + _HARD_TIMEOUT_PADDING_SEC
    return (
        str(uv_path),
        "run",
        "--script",
        str(crony_path),
        GUARD_SUBCOMMAND,
        str(cap),
        *base,
    )


def exec_path_strings(argv: list[str]) -> tuple[str | None, str | None]:
    """The `(uv, crony)` executable path strings baked into a unit's run
    argv, by name (an argument ending in `/uv` or `/crony`) rather than
    position, so any run-command shape (bare or guard-wrapped) is
    recovered. Either is None when the argv carries no such argument.
    Existence is the caller's concern; this returns the strings so they
    can be compared against disk even when the binary is gone."""
    uv = next((a for a in argv if a.endswith("/uv")), None)
    crony = next((a for a in argv if a.endswith("/crony")), None)
    return uv, crony


def _render_normalized_units(
    platform: str | None,
    name: crony.unit.EntityName,
    ref: crony.unit.EntityRef,
    timing: crony.unit.Timing | None,
    priority: crony.unit.PriorityClass,
    guard_timeout: int,
    *,
    uv: Path,
    crony_path: Path,
) -> tuple[str, str | None]:
    """Render the entry's (config unit content, timer unit content) with
    the given uv / crony executable paths.

    The model renders through the platform layer (no disk I/O); a
    config-built node renders with blank (`Path("")`) paths so the
    normalized form is path-independent -- a moved-but-present binary
    collapses to the same content as the live one. `platform` defaults
    to the running host's when omitted so tests can force a backend. The
    scheduler reports the two units separately, so the model never needs
    to know how a backend names them. Timer is None when the backend has
    no separate timer (launchd) or the entry is unscheduled.
    """
    sched = crony.platform.get_scheduler(
        platform or crony.platform.current_platform()
    )
    cmd = _guarded_argv(uv, crony_path, ref, guard_timeout)
    spec = crony.unit.UnitSpec(
        name=name, cmd=cmd, timing=timing, priority=priority
    )
    _, config = sched.render_config(spec)
    timer = sched.render_timer(spec)
    return config, timer[1] if timer is not None else None


@dataclass(frozen=True)
class _JobCommon:
    """Holds the fields both snapshot kinds carry.

    The one per-kind difference is the unit's priority: a group renders
    without one, so `unit_spec` reads it through the `_unit_priority`
    hook that `Job` overrides to bake in its resolved class.

    `state_dir_symlink` is the short-name alias as a graph knows it:
    (alias_path, target). A config-built (pending) node carries the
    expected pair (alias -> uuid); the current graph fills in the pair
    read from disk (None when no link exists, the real target when it
    does). Compared in `==` so a missing / mis-pointed alias surfaces as
    drift through the same snapshot comparison as any other field -- but
    excluded from `to_dict` / `from_dict`: it is derived disk / expected
    state, never persisted into snapshot.json. It is keyword-only so the
    subclasses can append their own non-default fields after it.
    """

    # The snapshot.json format version, serialized under the `schema`
    # JSON key. Compared in `==`, so a snapshot whose format predates the
    # current code surfaces as drift (reported `snapshot-schema`).
    snapshot_schema: int
    kind: crony.unit.EntityKind  # whether this entry is a job or a group
    bundle: str  # the bundle namespace; uuids are unique within it
    name: str  # the short name (the part after the bundle)
    uuid: str  # the entry's identity within the bundle (matches state-dir name)
    # The per-entry deadline in seconds that bounds a run: a job's
    # resolved job-timeout-sec, or a group's cumulative child budget
    # (computed once at apply time). 0 means uncapped -- the runner
    # treats it as an infinite deadline.
    timeout: int
    state_dir_symlink: tuple[Path, str] | None = field(
        default=None, kw_only=True
    )
    # The per-entry schedule / interval that drove the rendered platform
    # unit, pinned so `crony status` shows the applied schedule
    # independently of any later live-config edit. Default-None
    # (on-demand) for back-compat with snapshots written before a timing
    # was pinned; loaders rely on the dataclass default rather than a
    # schema bump. Keyword-only so the subclasses can append their own
    # non-default fields after it.
    timing: crony.unit.Timing | None = field(default=None, kw_only=True)
    # The entry's resolved capability flags as a single bitmask. `Job`
    # exposes the per-flag booleans (`interactive`, `keep_awake`) as
    # properties over it. Keyword-only so the subclasses can append
    # their own non-default fields after it.
    flags: crony.config.JobFlags = field(
        default=crony.config.JobFlags(0), kw_only=True
    )
    # Whether the operator turned this scheduled entry off (`crony
    # disable`). The schedule stays pinned in `timing` (so `enable`
    # restores it), but a disabled entry renders its unit with no
    # schedule (`unit_spec` drops the timing) -- loaded and triggerable,
    # just not firing on its own. Persisted in snapshot.json (it is real
    # applied state, not derived), and defaulted False so config-built
    # nodes start enabled (config has no disabled notion). `load_config`
    # mirrors a disabled current node's flag onto its pending node so the
    # operator overlay reads `synced`, not stale. Keyword-only.
    unit_disabled: bool = field(default=False, kw_only=True)
    # The uv / crony executable paths this entry's unit runs, baked onto
    # the node so unit rendering is self-contained -- `unit_spec` reads
    # them, and nothing re-derives the executables per render / drift
    # check. A config-built (pending) node carries the live executables
    # (resolved once at load); the current graph carries the paths
    # extracted from the on-disk unit, or None when they can't be parsed
    # or no longer exist (a gone binary, which `cfg_status` reads as
    # broken). `compare=False`: a binary that merely moved must not read
    # as drift (the normalized units render the paths blank). Derived,
    # never serialized. Keyword-only.
    uv_path: Path | None = field(default=None, compare=False, kw_only=True)
    crony_path: Path | None = field(default=None, compare=False, kw_only=True)
    # The entry's platform units (config: launchd plist / systemd
    # `.service`; timer: systemd `.timer`) rendered with blank uv / crony
    # executable paths, or None when the on-disk unit is absent or
    # diverges from what its own embedded paths would render. A pending
    # node carries what `render` would produce; the current graph carries
    # the blank-path render only when the install matches, else None. The
    # blank-path normalization makes the form independent of where the
    # binaries live, so a moved-but-present binary doesn't read as drift.
    # Compared in `==` so a hand-edited / drifted unit surfaces as
    # `config=stale` through the same snapshot comparison as any field
    # difference. Derived, never serialized. Keyword-only.
    unit_config_normalized: str | None = field(default=None, kw_only=True)
    unit_timer_normalized: str | None = field(default=None, kw_only=True)
    # Live on-disk / scheduler facts the current graph pins so
    # `cfg_status` can tell `broken` from `missing` for a unit whose
    # file is gone: whether the config unit (launchd plist / systemd
    # `.service`) is on disk, and whether the scheduler has the entry's
    # unit loaded -- the schedule-bearing one a backend arms (the
    # plist on launchd, the `.timer` on systemd, falling back to the
    # static `.service`). `compare=False` (they are runtime facts, not
    # config); a pending node leaves the defaults. Keyword-only.
    unit_config_exists: bool = field(default=False, compare=False, kw_only=True)
    unit_loaded: bool = field(default=False, compare=False, kw_only=True)

    @property
    def entity_ref(self) -> crony.unit.EntityRef:
        """The `<bundle>:<uuid>` identity."""
        return crony.unit.EntityRef(self.bundle, self.uuid)

    @property
    def entity_name(self) -> crony.unit.EntityName:
        """The human `<bundle>.<short>` name."""
        return crony.unit.EntityName(self.bundle, self.name)

    @property
    def full_name(self) -> str:
        """The `<bundle>.<short>` name as a plain string. Always set
        for a config-built node; the parallel `JobOrphan.full_name` is
        `str | None` (a too-corrupt remnant may carry no name), so the
        two read uniformly across a `Job | JobGroup | JobOrphan`."""
        return str(self.entity_name)

    @classmethod
    def state_dir_from_ref(cls, ref: crony.unit.EntityRef) -> Path:
        """The uuid-keyed state dir for a bare ref:
        `STATE_DIR/<bundle>/<uuid>`. The base case for callers that
        hold only an `EntityRef` and have no built node -- loading a
        snapshot, a not-yet-resolved group child, a ref-only destroy.
        The `state_dir` property routes through here so the layout
        join lives in one place."""
        return crony.paths.STATE_DIR / ref.bundle / ref.uuid

    @property
    def state_dir(self) -> Path:
        """The uuid-keyed state dir -- the path everything internal
        addresses."""
        return self.state_dir_from_ref(self.entity_ref)

    @property
    def snapshot_path(self) -> Path:
        """The applied-snapshot file inside this entry's state dir."""
        return self.state_dir / "snapshot.json"

    @classmethod
    def state_dir_symlink_path_from_name(
        cls, name: crony.unit.EntityName
    ) -> Path:
        """The short-name alias dir for a name:
        `STATE_DIR/<bundle>/<short>`. The base case for callers that
        hold only a name and no built node -- the current-graph scan
        reading a node's on-disk alias before constructing it. The
        `state_dir_symlink_path` property routes through here so the layout
        join lives in one place."""
        return crony.paths.STATE_DIR / name.bundle / name.short

    @property
    def state_dir_symlink_path(self) -> Path:
        """The short-name alias dir for this entry
        (`STATE_DIR/<bundle>/<short>`). apply maintains it as a
        relative symlink to `state_dir`."""
        return self.state_dir_symlink_path_from_name(self.entity_name)

    @property
    def log_path_resolved(self) -> Path:
        """The canonical (uuid-keyed) run.log path -- where the runner
        writes, independent of alias state."""
        return self.state_dir / RUN_LOG_NAME

    @classmethod
    def state_dir_symlink_expected(
        cls, name: crony.unit.EntityName, uuid: str
    ) -> tuple[Path, str]:
        """The alias pair a config-built node expects on disk: the
        short-name alias dir and its relative target (the bare uuid).
        A classmethod so `from_config` can set `state_dir_symlink` at
        construction rather than building the node and replacing it."""
        return (cls.state_dir_symlink_path_from_name(name), uuid)

    @property
    def log_path(self) -> Path:
        """Reported log path: the alias when its recorded target
        matches this node's uuid, else the uuid-keyed path. A
        config-built node carries the expected pair (so it reports the
        alias); a current node carries the pair read from disk, so a
        missing / mis-pointed link reports the uuid path -- always a
        real on-disk location."""
        sl = self.state_dir_symlink
        if sl is not None and sl[1] == self.uuid:
            return sl[0] / RUN_LOG_NAME
        return self.log_path_resolved

    @property
    def _unit_priority(self) -> crony.unit.PriorityClass:
        """The process-priority class baked into this entry's platform
        unit. NORMAL here (groups request no special scheduling, and
        NORMAL emits no platform directives); `Job` overrides to expose
        its `priority` field."""
        return crony.unit.PriorityClass.NORMAL

    @property
    def guard_timeout(self) -> int:
        """The wallclock cap the hard-timeout guard wraps the unit's run
        in (0 = no guard). The entry's `timeout` here; `Job` overrides to
        drop the guard for an interactive job, whose pending wait /
        prompt / delay phase has no wallclock bound for the guard to
        respect."""
        return self.timeout

    def unit_spec(self) -> crony.unit.UnitSpec:
        """The platform UnitSpec the scheduler renders for this node's
        real unit -- self-contained: the run command is built from the
        uv / crony executables the node carries (a pending node's live
        ones, a re-render's stamped ones). A disabled entry renders
        schedule-less (`timing` dropped) -- loaded and triggerable, but
        not firing on its own -- while keeping its schedule pinned in the
        `timing` field so `enable` restores it.

        Requires those paths; a node only ever compared, never installed
        (a bare snapshot load), has no unit to render and raises. The
        drift comparison renders with blank executable paths and builds
        its spec directly, so it does not go through here."""
        if self.uv_path is None or self.crony_path is None:
            raise crony.errors.PreconditionError(
                f"{self.entity_ref} carries no resolved uv / crony path "
                f"to render its unit"
            )
        cmd = _guarded_argv(
            self.uv_path, self.crony_path, self.entity_ref, self.guard_timeout
        )
        return crony.unit.UnitSpec(
            name=self.entity_name,
            cmd=cmd,
            timing=None if self.unit_disabled else self.timing,
            priority=self._unit_priority,
        )

    def with_unit_disabled(
        self, disabled: bool, platform: str | None = None
    ) -> Self:
        """A copy with `unit_disabled` set and its normalized units
        re-rendered (blank paths) for the resulting schedule shape, so a
        pending node mirrored onto a disabled current node reads
        `synced` rather than stale."""
        node = dataclasses.replace(self, unit_disabled=disabled)
        config, timer = _render_normalized_units(
            platform,
            node.entity_name,
            node.entity_ref,
            None if disabled else node.timing,
            node._unit_priority,
            node.guard_timeout,
            uv=Path(""),
            crony_path=Path(""),
        )
        return dataclasses.replace(
            node,
            unit_config_normalized=config,
            unit_timer_normalized=timer,
        )

    def _schedule_str(self) -> str | None:
        """This entry's schedule as its source string (None unless its
        timing is a Schedule)."""
        return (
            str(self.timing)
            if isinstance(self.timing, crony.unit.Schedule)
            else None
        )

    def _interval_str(self) -> str | None:
        """This entry's interval as its source string (None unless its
        timing is an Interval)."""
        return (
            str(self.timing)
            if isinstance(self.timing, crony.unit.Interval)
            else None
        )

    def to_snapshot(
        self,
    ) -> crony.snapshot.JobSnapshot | crony.snapshot.GroupSnapshot:
        """The on-disk snapshot model for this entry (`Job` / `JobGroup`
        each build their kind). The model holds only the persisted fields,
        the value objects rendered to their source strings, and the flags
        as their per-member booleans."""
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        """Serialize this entry to its snapshot.json dict, via the typed
        snapshot model -- which keys the fields by their on-disk names and
        drops everything the model doesn't carry (the derived,
        never-persisted state)."""
        return self.to_snapshot().model_dump(by_alias=True, mode="json")


@dataclass(frozen=True)
class Job(_JobCommon):
    """Resolved runtime parameters for a single job. Most fields are
    final values: paths are pre-expanded (`~` and `$VAR`), timeouts
    are post-cascade. `env` is the deliberate exception -- it stores
    the user's literal toml `env` dict (no parent-process overlay,
    no $VAR expansion). The runner overlays it on the inherited
    process env at fire time. Pinning the inherited env would (a)
    make the snapshot unstable across shell sessions (every apply
    from a different shell would flip the snapshot and report
    `config=stale`), (b) capture whatever env was in scope when
    `crony apply` ran rather than what the unit provides at fire
    time.
    """

    command: str | None
    script: str | None
    args: list[str]
    gate: str | None
    gate_script: str | None
    gate_args: list[str]
    env: dict[str, str]
    # Process-priority class baked into the platform unit (HIGH / LOW /
    # NORMAL). Resolution maps an unset config priority to NORMAL, so
    # this is always concrete. Pinned in the snapshot so a change
    # re-renders the unit on the next apply. Default-NORMAL so a snapshot
    # predating the field (which stored no priority) loads as NORMAL.
    priority: crony.unit.PriorityClass = crony.unit.PriorityClass.NORMAL
    # Non-zero exit codes the runner classifies as success (read at
    # fire time). Default-empty for back-compat with older snapshots.
    success_exit_codes: list[int] = field(default_factory=list)
    # Interactive runner controls. `interactive_active_sec` and
    # `interactive_delay_sec` are always resolved (non-None) in
    # the snapshot so the runner doesn't re-consult any defaults
    # table; the per-job `_sec` knobs cascade through the resolver
    # to the baked defaults defined alongside the TomlJob dataclass.
    # (Whether the job is interactive is the INTERACTIVE flag -- see the
    # `interactive` property.)
    interactive_active_sec: int = crony.config.INTERACTIVE_ACTIVE_DEFAULT_SEC
    interactive_delay_sec: int = crony.config.INTERACTIVE_DELAY_DEFAULT_SEC
    # The Crony.app Full Disk Access wrapper state as this graph knows
    # it, for a full-disk-access job; None for a job without the flag (a
    # group never carries it). A config-built (pending) node holds the
    # expected value (OK -- a built, granted wrapper); the current graph
    # fills in the live state probed at load. Compared in `==` so a
    # wrapper that isn't built / granted, or has gone stale, surfaces as
    # drift through the same snapshot comparison as any other field
    # (reported `fda-wrapper`) -- but excluded from `to_dict` /
    # `from_dict`: it is derived runtime state, never persisted into
    # snapshot.json. Keyword-only so it can follow the defaulted fields.
    fda_wrapper: FDAWrapper | None = field(default=None, kw_only=True)

    @staticmethod
    def _fda_wrapper_for(
        flags: crony.config.JobFlags, state: FDAWrapper | None
    ) -> FDAWrapper | None:
        """The `fda_wrapper` value for a job with `flags`: `state` when
        the job carries the full-disk-access flag (OK as the expected
        value on a config-built node, the live wrapper state on a
        current node), else None -- a non-FDA job has no wrapper to
        track, so the two graphs agree on None and never diverge."""
        if crony.config.JobFlags.FULL_DISK_ACCESS in flags:
            return state
        return None

    @property
    def interactive(self) -> bool:
        """Whether the runner waits for user activity and prompts before
        firing -- the INTERACTIVE flag."""
        return crony.config.JobFlags.INTERACTIVE in self.flags

    @property
    def keep_awake(self) -> bool:
        """Whether the runner holds a power assertion for the run's
        duration -- the KEEP_AWAKE flag."""
        return crony.config.JobFlags.KEEP_AWAKE in self.flags

    @property
    def full_disk_access(self) -> bool:
        """Whether the job runs through the macOS Full Disk Access
        wrapper -- the FULL_DISK_ACCESS flag. A no-op off darwin."""
        return crony.config.JobFlags.FULL_DISK_ACCESS in self.flags

    @property
    def _unit_priority(self) -> crony.unit.PriorityClass:
        """A job's platform unit bakes in its resolved priority class."""
        return self.priority

    @property
    def guard_timeout(self) -> int:
        """An interactive job has no wallclock-bounded run: its pending
        wait, prompt, and re-promptable delay can outlast any cap, so the
        hard guard would kill a healthy waiting job. The guard is dropped
        (0); the runner's own soft timeout still bounds the command once
        the user runs it. A non-interactive job is guarded by its
        timeout."""
        return 0 if self.interactive else self.timeout

    @classmethod
    def from_config(
        cls,
        config: crony.config.TomlBundleConfig,
        job: crony.config.TomlJob,
        name: crony.unit.EntityName,
        *,
        flags: crony.config.JobFlags | None = None,
        platform: str | None = None,
        uv_path: Path | None = None,
        crony_path: Path | None = None,
    ) -> Job:
        """Build a Job by applying every cascade once. `flags` is the
        entry's resolved capability bitmask (composed across the config
        levels by the caller); the runner-facing `interactive` /
        `keep_awake` booleans derive from it. When omitted, it defaults
        to the defaults composed with the job's own delta -- the
        no-ancestor-group case.

        `platform` selects the backend the normalized units render
        against (default: the running host's), baked at construction so
        a `config=stale` verdict is a pure node comparison. `uv_path` /
        `crony_path` are the live executables stamped onto the node so it
        can render its real unit self-contained (the caller resolves them
        once); None leaves the node unrenderable, fine for a node only
        compared, never installed."""
        if flags is None:
            flags = config.composed_flags(job.flags)
        args = [_expand_path_field(a) for a in job.args]
        gate_args = [_expand_path_field(a) for a in job.gate_args]
        script = (
            str(_resolve_script(job.script)) if job.script is not None else None
        )
        gate_script = (
            str(_resolve_script(job.gate_script))
            if job.gate_script is not None
            else None
        )
        priority = config.resolved_priority(job)
        timeout = config.resolved_job_timeout_sec(job)
        interactive = crony.config.JobFlags.INTERACTIVE in flags
        norm_config, norm_timer = _render_normalized_units(
            platform,
            name,
            crony.unit.EntityRef(name.bundle, job.uuid),
            job.timing,
            priority,
            0 if interactive else timeout,
            uv=Path(""),
            crony_path=Path(""),
        )
        return cls(
            snapshot_schema=crony.snapshot.CURRENT_SNAPSHOT_SCHEMA,
            kind=crony.unit.EntityKind.JOB,
            bundle=name.bundle,
            name=name.short,
            uuid=job.uuid,
            state_dir_symlink=cls.state_dir_symlink_expected(name, job.uuid),
            fda_wrapper=cls._fda_wrapper_for(flags, FDAWrapper.OK),
            command=job.command,
            script=script,
            args=args,
            gate=job.gate,
            gate_script=gate_script,
            gate_args=gate_args,
            env=config.resolved_env(job),
            timeout=timeout,
            timing=job.timing,
            priority=priority,
            flags=flags,
            uv_path=uv_path,
            crony_path=crony_path,
            unit_config_normalized=norm_config,
            unit_timer_normalized=norm_timer,
            success_exit_codes=list(job.success_exit_codes),
            interactive_active_sec=(
                job.interactive_active_sec
                if job.interactive_active_sec is not None
                else crony.config.INTERACTIVE_ACTIVE_DEFAULT_SEC
            ),
            interactive_delay_sec=(
                job.interactive_delay_sec
                if job.interactive_delay_sec is not None
                else crony.config.INTERACTIVE_DELAY_DEFAULT_SEC
            ),
        )

    def to_snapshot(self) -> crony.snapshot.JobSnapshot:
        return crony.snapshot.JobSnapshot(
            snapshot_schema=self.snapshot_schema,
            kind=crony.unit.EntityKind.JOB,
            name=str(self.entity_name),
            uuid=self.uuid,
            timeout=self.timeout,
            schedule=self._schedule_str(),
            interval=self._interval_str(),
            unit_disabled=self.unit_disabled,
            interactive=crony.config.JobFlags.INTERACTIVE in self.flags,
            keep_awake=crony.config.JobFlags.KEEP_AWAKE in self.flags,
            full_disk_access=(
                crony.config.JobFlags.FULL_DISK_ACCESS in self.flags
            ),
            command=self.command,
            script=self.script,
            args=self.args,
            gate=self.gate,
            gate_script=self.gate_script,
            gate_args=self.gate_args,
            env=self.env,
            priority=str(self.priority),
            success_exit_codes=self.success_exit_codes,
            interactive_active_sec=self.interactive_active_sec,
            interactive_delay_sec=self.interactive_delay_sec,
        )

    @classmethod
    def from_snapshot(
        cls,
        snap: crony.snapshot.JobSnapshot,
        *,
        state_dir_symlink: tuple[Path, str] | None = None,
        fda_wrapper: FDAWrapper | None = None,
        platform: str | None = None,
        unit_config_disk: str | None = None,
        unit_timer_disk: str | None = None,
        installed_uv: Path | None = None,
        installed_crony: Path | None = None,
        unit_loaded: bool = False,
    ) -> Job:
        """Build a Job from its on-disk snapshot model, parsing the typed
        value-object fields back from their source strings, plus the
        derived runtime state the caller probed (see `snapshot_from_dict`
        for what the current-graph scan supplies)."""
        en = snap.entity_name()
        timing = snap.timing()
        flags = snap.job_flags()
        priority = snap.priority_class()
        # A disabled entry installs its unit schedule-less, so a render
        # that reproduces the on-disk file must drop the timing too.
        eff_timing = None if snap.unit_disabled else timing
        interactive = crony.config.JobFlags.INTERACTIVE in flags
        config_norm, timer_norm = _current_normalized(
            name=en,
            ref=snap.entity_ref(),
            eff_timing=eff_timing,
            priority=priority,
            guard_timeout=0 if interactive else snap.timeout,
            platform=platform,
            unit_config_disk=unit_config_disk,
            unit_timer_disk=unit_timer_disk,
            installed_uv=installed_uv,
            installed_crony=installed_crony,
        )
        return cls(
            snapshot_schema=snap.snapshot_schema,
            kind=snap.kind,
            bundle=en.bundle,
            name=en.short,
            uuid=snap.uuid,
            timeout=snap.timeout,
            command=snap.command,
            script=snap.script,
            args=snap.args,
            gate=snap.gate,
            gate_script=snap.gate_script,
            gate_args=snap.gate_args,
            env=snap.env,
            priority=priority,
            success_exit_codes=snap.success_exit_codes,
            interactive_active_sec=snap.interactive_active_sec,
            interactive_delay_sec=snap.interactive_delay_sec,
            timing=timing,
            flags=flags,
            unit_disabled=snap.unit_disabled,
            # Wrapper state is derived (job-only): stamp the caller's
            # probed value, gated on the full-disk-access flag.
            fda_wrapper=cls._fda_wrapper_for(flags, fda_wrapper),
            uv_path=installed_uv,
            crony_path=installed_crony,
            unit_config_normalized=config_norm,
            unit_timer_normalized=timer_norm,
            unit_config_exists=unit_config_disk is not None,
            unit_loaded=unit_loaded,
            state_dir_symlink=state_dir_symlink,
        )


@dataclass(frozen=True)
class JobGroup(_JobCommon):
    """Resolved runtime parameters for a job-group. `children` are
    bundle-scoped `EntityRef`s; the runner resolves each back to its
    current full name via the child's own snapshot at dispatch
    time so a rename in config doesn't flip the parent's snapshot
    (the snapshot persists only the uuids -- the bundle is the
    parent's). The inherited `timeout` holds the pre-padded cumulative
    deadline computed once at apply time. 0 means no cap (some child is
    uncapped); the group runner treats it as an infinite deadline.
    """

    children: list[crony.unit.EntityRef]
    trigger_timeout_sec: int

    @classmethod
    def from_config(
        cls,
        config: crony.config.TomlBundleConfig,
        target: crony.config.Target | None,
        group: crony.config.TomlJobGroup,
        name: crony.unit.EntityName,
        *,
        flags: crony.config.JobFlags | None = None,
        platform: str | None = None,
        uv_path: Path | None = None,
        crony_path: Path | None = None,
    ) -> JobGroup:
        """Build a JobGroup. The cumulative `timeout` budget is
        recomputed from the live config (not from children's pinned
        snapshots), so an apply pass that walks topologically still
        produces the right parent budget regardless of children's prior
        applied state.

        `platform` selects the backend the normalized units render
        against (default: the running host's), baked at construction so
        a `config=stale` verdict is a pure node comparison. `uv_path` /
        `crony_path` are the live executables stamped onto the node so it
        renders its real unit self-contained.

        `flags` is the group's resolved cascade value (the defaults
        composed with its ancestor groups and its own delta), supplied
        by the caller; when omitted it defaults to the defaults composed
        with the group's own delta. A group's flags have no runtime
        effect -- the runner acts only on a job's flags -- but the
        resolved value is persisted and shown so the inheritance the
        group hands down to its children stays visible.

        Children that aren't selected on this host are dropped from
        `children`. That covers both own-filter masks (the child's
        own `platforms` / `hosts` exclude this host) and the
        empty-group cascade (a child group whose own children are all
        masked here). The reference becomes a no-op on this host, so
        a shared bundle can list a host-restricted child from a
        parent group that runs everywhere without the parent
        dispatcher trying to trigger a unit that wasn't installed.
        """
        sel_jobs, sel_groups = config.selected_jobs_and_groups(target)
        # Each child edge is the child's bundle-scoped ref (the parent's
        # bundle, since a group only references children in its own
        # bundle). Keyed by uuid -- not full name -- so renaming a child
        # in config doesn't flip the parent's snapshot; the runner
        # resolves each ref to its current full name at dispatch time.
        children: list[crony.unit.EntityRef] = []
        for c in group.jobs:
            if c in sel_jobs:
                child_uuid = config.jobs[c].uuid
            elif c in sel_groups:
                child_uuid = config.job_groups[c].uuid
            else:
                continue
            children.append(crony.unit.EntityRef(name.bundle, child_uuid))
        if flags is None:
            flags = config.composed_flags(group.flags)
        timeout = config.resolved_group_timeout_sec(target, group.name)
        norm_config, norm_timer = _render_normalized_units(
            platform,
            name,
            crony.unit.EntityRef(name.bundle, group.uuid),
            group.timing,
            crony.unit.PriorityClass.NORMAL,
            timeout,
            uv=Path(""),
            crony_path=Path(""),
        )
        return cls(
            snapshot_schema=crony.snapshot.CURRENT_SNAPSHOT_SCHEMA,
            kind=crony.unit.EntityKind.GROUP,
            bundle=name.bundle,
            name=name.short,
            uuid=group.uuid,
            state_dir_symlink=cls.state_dir_symlink_expected(name, group.uuid),
            children=children,
            timeout=timeout,
            trigger_timeout_sec=config.defaults.trigger_timeout_sec,
            timing=group.timing,
            flags=flags,
            uv_path=uv_path,
            crony_path=crony_path,
            unit_config_normalized=norm_config,
            unit_timer_normalized=norm_timer,
        )

    def to_snapshot(self) -> crony.snapshot.GroupSnapshot:
        return crony.snapshot.GroupSnapshot(
            snapshot_schema=self.snapshot_schema,
            kind=crony.unit.EntityKind.GROUP,
            name=str(self.entity_name),
            uuid=self.uuid,
            timeout=self.timeout,
            schedule=self._schedule_str(),
            interval=self._interval_str(),
            unit_disabled=self.unit_disabled,
            interactive=crony.config.JobFlags.INTERACTIVE in self.flags,
            keep_awake=crony.config.JobFlags.KEEP_AWAKE in self.flags,
            full_disk_access=(
                crony.config.JobFlags.FULL_DISK_ACCESS in self.flags
            ),
            children=[r.uuid for r in self.children],
            trigger_timeout_sec=self.trigger_timeout_sec,
        )

    @classmethod
    def from_snapshot(
        cls,
        snap: crony.snapshot.GroupSnapshot,
        *,
        state_dir_symlink: tuple[Path, str] | None = None,
        platform: str | None = None,
        unit_config_disk: str | None = None,
        unit_timer_disk: str | None = None,
        installed_uv: Path | None = None,
        installed_crony: Path | None = None,
        unit_loaded: bool = False,
    ) -> JobGroup:
        """Build a JobGroup from its on-disk snapshot model plus the
        derived runtime state the caller probed (see `snapshot_from_dict`
        for what the current-graph scan supplies)."""
        en = snap.entity_name()
        timing = snap.timing()
        flags = snap.job_flags()
        eff_timing = None if snap.unit_disabled else timing
        config_norm, timer_norm = _current_normalized(
            name=en,
            ref=snap.entity_ref(),
            eff_timing=eff_timing,
            priority=crony.unit.PriorityClass.NORMAL,
            guard_timeout=snap.timeout,
            platform=platform,
            unit_config_disk=unit_config_disk,
            unit_timer_disk=unit_timer_disk,
            installed_uv=installed_uv,
            installed_crony=installed_crony,
        )
        return cls(
            snapshot_schema=snap.snapshot_schema,
            kind=snap.kind,
            bundle=en.bundle,
            name=en.short,
            uuid=snap.uuid,
            timeout=snap.timeout,
            children=snap.child_refs(),
            trigger_timeout_sec=snap.trigger_timeout_sec,
            timing=timing,
            flags=flags,
            unit_disabled=snap.unit_disabled,
            uv_path=installed_uv,
            crony_path=installed_crony,
            unit_config_normalized=config_norm,
            unit_timer_normalized=timer_norm,
            unit_config_exists=unit_config_disk is not None,
            unit_loaded=unit_loaded,
            state_dir_symlink=state_dir_symlink,
        )


def _expand_path_field(value: str) -> str:
    """Expand `~` and `$VAR` / `${VAR}` against os.environ.

    Mirrors the `env` value expansion so script paths and argv
    elements can refer to `$HOME` / `$XDG_CONFIG_HOME` / etc. the
    same way a shell-string `command` would. Unresolved variables
    stay literal (matches `_expand_env_value`'s shell-style
    behavior). Path-resolution timing means we expand against
    os.environ, not the per-job runtime env.
    """
    return os.path.expandvars(os.path.expanduser(value))


def _resolve_script(script: str) -> Path:
    """Resolve a `script` field to an absolute path."""
    p = Path(_expand_path_field(script))
    if not p.is_absolute():
        p = (crony.paths.CONFIG_DIR / p).resolve()
    return p


def _resolve_snapshot_for(
    config: crony.config.TomlBundleConfig,
    short: str,
    bundle_name: str = crony.config.DEFAULT_BUNDLE_NAME,
) -> Job | JobGroup:
    """Resolve a snapshot for a single config entry by short name.

    Convenience for callers that have a TomlBundleConfig + short name and
    want the resolved snapshot in one step (apply pipeline + tests
    that exercise the runner without going through full apply).
    """
    name = crony.unit.EntityName(bundle_name, short)
    target = config.resolve_target()
    if short in config.jobs:
        flags = config.resolved_flags(short, target)
        return Job.from_config(config, config.jobs[short], name, flags=flags)
    if short in config.job_groups:
        flags = config.resolved_flags(short, target)
        return JobGroup.from_config(
            config, target, config.job_groups[short], name, flags=flags
        )
    raise crony.errors.PreconditionError(f"unknown job/group: {short!r}")


# =============================================================================
# SNAPSHOT <-> NODE
# =============================================================================
# `crony.snapshot` owns the on-disk format (the typed model, migration,
# and schema versions); these translate between that model and the
# in-memory Job / JobGroup. The per-kind work lives on the nodes
# (`to_snapshot` / `from_snapshot`); `snapshot_from_dict` is the load
# entry point the runtime / runner call.


def _current_normalized(
    *,
    name: crony.unit.EntityName,
    ref: crony.unit.EntityRef,
    eff_timing: crony.unit.Timing | None,
    priority: crony.unit.PriorityClass,
    guard_timeout: int,
    platform: str | None,
    unit_config_disk: str | None,
    unit_timer_disk: str | None,
    installed_uv: Path | None,
    installed_crony: Path | None,
) -> tuple[str | None, str | None]:
    """The current node's (config, timer) normalized units from the
    on-disk inputs. The config unit is the blank-path render only when a
    render with the installed paths reproduces the on-disk file; the
    timer carries no uv / crony paths, so its normalized form is the
    on-disk content as-is (None when absent)."""
    config_norm: str | None = None
    if (
        unit_config_disk is not None
        and installed_uv is not None
        and installed_crony is not None
    ):
        rendered, _ = _render_normalized_units(
            platform,
            name,
            ref,
            eff_timing,
            priority,
            guard_timeout,
            uv=installed_uv,
            crony_path=installed_crony,
        )
        if rendered == unit_config_disk:
            config_norm, _ = _render_normalized_units(
                platform,
                name,
                ref,
                eff_timing,
                priority,
                guard_timeout,
                uv=Path(""),
                crony_path=Path(""),
            )
    return config_norm, unit_timer_disk


def snapshot_from_dict(
    raw: dict[str, Any],
    *,
    state_dir_symlink: tuple[Path, str] | None = None,
    fda_wrapper: FDAWrapper | None = None,
    platform: str | None = None,
    unit_config_disk: str | None = None,
    unit_timer_disk: str | None = None,
    installed_uv: Path | None = None,
    installed_crony: Path | None = None,
    unit_loaded: bool = False,
) -> Job | JobGroup:
    """Build a snapshot's node from its JSON dict: parse the on-disk shape
    (`crony.snapshot.parse`), then construct the Job / JobGroup, baking in
    the derived runtime state the caller probed. A wrong shape / unknown
    key / unknown kind / bad typed field raises ValueError (a pydantic
    ValidationError); a non-mapping top-level raises TypeError or
    ValueError (from `dict()`). Callers treat either as a broken snapshot.

    `state_dir_symlink` is the on-disk alias pair the caller read for this
    entry (None when it has no link, or for a load that doesn't care about
    the alias). It is not part of `raw` -- the alias is derived disk state,
    never serialized -- so the current-graph scan passes the pair it read
    so the frozen node carries it from construction.

    `fda_wrapper` is the live Crony.app wrapper state the caller probed,
    applied only when the loaded entry carries the full-disk-access flag
    (None otherwise; ignored for a group). Like the alias, it is derived
    runtime state, never serialized.

    The current-graph scan passes the on-disk unit contents
    (`unit_config_disk` / `unit_timer_disk`), the uv / crony executable
    paths it extracted from the installed run command and confirmed still
    exist (`installed_uv` / `installed_crony`, None when a baked binary is
    gone), and whether the scheduler has the unit loaded (`unit_loaded`).
    The node's normalized config unit is the blank-path render only when a
    render with those installed paths reproduces the on-disk file -- so a
    hand-edited / drifted unit, or one whose binary is gone, gets None and
    reads `stale` against the pending node; the extracted paths, file
    presence, and loaded flag let `cfg_status` tell `broken` from
    `missing`. A load that doesn't supply them (the runner reading its own
    snapshot) leaves the derived fields empty."""
    model = crony.snapshot.parse(raw)
    if isinstance(model, crony.snapshot.JobSnapshot):
        return Job.from_snapshot(
            model,
            state_dir_symlink=state_dir_symlink,
            fda_wrapper=fda_wrapper,
            platform=platform,
            unit_config_disk=unit_config_disk,
            unit_timer_disk=unit_timer_disk,
            installed_uv=installed_uv,
            installed_crony=installed_crony,
            unit_loaded=unit_loaded,
        )
    return JobGroup.from_snapshot(
        model,
        state_dir_symlink=state_dir_symlink,
        platform=platform,
        unit_config_disk=unit_config_disk,
        unit_timer_disk=unit_timer_disk,
        installed_uv=installed_uv,
        installed_crony=installed_crony,
        unit_loaded=unit_loaded,
    )


@dataclass
class NotificationResult:
    """One channel's outcome inside a JobRunResult.notifications entry."""

    sent: bool
    error: str | None = None
    error_class: str | None = None


class ExitClass(StrEnum):
    """The recorded outcome of a run: written to last-run.json as
    `exit_class` and rolled up across a group's children. A StrEnum so
    it serializes as its plain value and on-disk records round-trip
    unchanged."""

    OK = "ok"
    FAIL = "fail"
    TIMEOUT = "timeout"
    SIGNAL = "signal"
    GATED = "gated"
    CANCELED = "canceled"
    DISPATCHED = "dispatched"

    @classmethod
    def parse(cls, value: object) -> ExitClass | None:
        """The member for `value`, or None when it isn't a known
        outcome -- tolerant of a partial / corrupt on-disk record."""
        if not isinstance(value, str):
            return None
        try:
            return cls(value)
        except ValueError:
            return None


class DescribedStrEnum(StrEnum):
    """StrEnum whose members are ``(value, description)`` pairs, where
    the description is the human-facing meaning of the value.

    Subclasses assign members as ``(value, description)`` tuples (e.g.
    ``SYNCED = "synced", "..."``); the value may be a plain string or
    another StrEnum member (`JobStatus` reuses `ExitClass` members as its
    values). A member assigned a bare value with no description gets an
    empty one. The base has no members of its own, which is what lets it
    be subclassed. Giving the description one home on the member keeps
    the `crony status` `--help` reference from drifting across
    hand-maintained copies -- mirroring `common.exitcodes.ExitCodeBase`."""

    description: str

    def __new__(cls, value: str, description: str = "") -> Self:
        text = str(value)
        obj = str.__new__(cls, text)
        obj._value_ = text
        obj.description = description
        return obj


class JobStatus(DescribedStrEnum):
    """The verdict `crony status` shows in its STATUS column: the run
    outcomes that actually reach the cell plus the display-only states
    derived at read time. Shares string values with `ExitClass` for the
    outcomes it carries (so the two compare and serialize alike); it
    omits `signal` (folded to `fail`) and `dispatched` (shown as
    `unknown`), which never surface in the cell."""

    OK = ExitClass.OK, "The job's last run completed successfully."
    FAIL = (
        ExitClass.FAIL,
        "The job's last run failed (exited with a non-zero status).",
    )
    TIMEOUT = (
        ExitClass.TIMEOUT,
        "The job was killed after exceeding its wallclock execution timeout.",
    )
    GATED = (
        ExitClass.GATED,
        "The job was skipped due to an execution gate. This is not "
        "considered as a job failure.",
    )
    CANCELED = (
        ExitClass.CANCELED,
        "An interactive job run that was canceled / skipped by the user.",
    )
    CRASHED = (
        "crashed",
        "The scheduler failed to launch a job, or the job was "
        "killed/crashed before it could save its exit status to disk.",
    )
    RUNNING = "running", "The job is currently running."
    PENDING = (
        "pending",
        "An interactive job is either waiting for an active user, or "
        "waiting for that user to confirm execution (via a pop-up "
        "dialog).",
    )
    NEVER = "never", "A newly deployed job that hasn't been run yet."
    UNKNOWN = "unknown", "We're unable to determine the job status."


class ConfigStatus(DescribedStrEnum):
    """The verdict `crony status` shows in its CONFIG column: how the
    live config view relates to the applied on-disk state.

    `Config.cfg_status` produces the base verdicts that score the
    pending graph against the current one -- SYNCED / STALE / BROKEN /
    MISSING / ORPHAN. The status caller layers the two host-filter
    verdicts on top: ERROR for an entry whose bundle config was
    rejected, and MASKED for one excluded on this host with no on-disk
    remnant."""

    SYNCED = "synced", "A deployed job's configuration is up-to-date."
    STALE = (
        "stale",
        "A deployed job's configuration has diverged from its "
        "configuration file definition, but the job is still runnable. "
        "Run `apply` to update the deployed configuration.",
    )
    BROKEN = (
        "broken",
        "A deployed job's configuration is broken and un-runnable. Run "
        "`apply` to fix the deployed configuration.",
    )
    MISSING = (
        "missing",
        "A job exists in the config file but has not yet been deployed.",
    )
    ORPHAN = (
        "orphan",
        "A deployed job (or some job-related resource) is not defined in "
        "any configuration file. Run `destroy --orphans` to clean up the "
        "deployed configuration.",
    )
    MASKED = (
        "masked",
        "A job can't be deployed on the current host due to "
        "configuration filters (usually a mismatched `platform` or "
        "`host` directive).",
    )
    ERROR = (
        "error",
        "The job configuration file definition (i.e. the pending or "
        "requested configuration) is broken, or its dependencies can't "
        "be met (e.g. full-disk-access has been requested on "
        "macOS/darwin, but the Crony.app wrapper doesn't have "
        "full-disk-access). If the job was previously deployed, it will "
        "continue to run and can be managed, but the deployed "
        "configuration can't be updated with `apply` until the pending "
        "configuration issue is fixed.",
    )


class ScheduleValue(DescribedStrEnum):
    """The kinds of value the `crony status` SCHEDULE column shows. Each
    member's value is its `--help` display label. GROUPED and DISABLED's
    values are also the literal cell strings the renderer emits;
    INTERVAL's value is the cell template, whose `<x>` the renderer
    replaces with the actual time span. All three thus have a single home
    here. SCHEDULE is a pure category label -- a scheduled entry renders
    its raw OnCalendar string."""

    SCHEDULE = (
        "OnCalendar schedule",
        "A (restricted) systemd OnCalendar schedule for job execution.",
    )
    INTERVAL = (
        "interval=<x>",
        "A systemd time-span interval for job execution.",
    )
    GROUPED = (
        "grouped",
        "A job/group with no schedule of its own, it runs when triggered "
        "by a parent job group.",
    )
    DISABLED = (
        "disabled",
        "A job that has been disabled via the `disable` subcommand; it "
        "will not be run via any schedule. It can be run manually via the "
        "`trigger` subcommand, and re-enabled via the `enable` "
        "subcommand.",
    )


class GateResult(StrEnum):
    """A run's gate outcome, recorded as `gate` in last-run.json. NONE
    when no gate ran (none configured); PASSED on a gate exit 0; FAILED
    on any non-zero or timeout. A StrEnum so it serializes as its plain
    value and reads back unchanged."""

    NONE = "none"
    PASSED = "passed"
    FAILED = "failed"


@dataclass
class CommonRunResult:
    """The fields every completed run records, shared by JobRunResult
    and GroupRunResult.

    Identity isn't repeated in the record: the file already lives at
    `STATE_DIR/<bundle>/<uuid>/last-run.json`, and the matching
    `snapshot.json` in the same dir carries the full namespaced name
    of the entity it was applied as. The two record kinds extend this
    with their own fields -- a job's exit detail / gate / notifications,
    a group's child rollup.
    """

    host: str
    platform: str
    started_at: str
    ended_at: str
    duration_sec: float
    exit_class: ExitClass
    # The code the runner exits the process with -- what the platform
    # scheduler records as this launch's wait status. Status reconciles
    # it against the scheduler's report so a launch that ended without
    # writing this record reads as `crashed` rather than showing the
    # stale prior outcome. The per-kind value semantics live in each
    # subclass's docstring.
    process_exit: int
    log_path: str
    # The runner's pid -- the same value it wrote to run.pid at launch.
    # Captured at construction (in the runner process), so a recorded
    # result always carries the pid of the launch that produced it.
    # Keyword-only so the subclasses can append their own non-default
    # fields after it.
    pid: int = field(default_factory=os.getpid, kw_only=True)


@dataclass
class JobRunResult(CommonRunResult):
    """Recorded as last-run.json for each completed job run. Its
    `process_exit` is 0 for ok / gated / canceled, the job's own code
    for fail, the timeout code, or 128+sig for a signal-killed child."""

    exit_code: int | None
    signal: int | None
    # The gate outcome; the numeric exit code stays in run.log for
    # diagnosis, this field is the binary answer "did the gate let the
    # job run?".
    gate: GateResult
    log_bytes_this_run: int
    # Per-channel outcomes. Keys are channel names that were
    # attempted (e.g. "email", "ntfy"); values are NotificationResult
    # records. Empty dict means no external dispatch was attempted
    # (notify_channels resolved to []).
    notifications: dict[str, NotificationResult] = field(default_factory=dict)


@dataclass
class GroupChildResult:
    """One child's outcome inside a group run."""

    name: str
    exit_class: ExitClass
    exit_code: int


@dataclass
class GroupRunResult(CommonRunResult):
    """Recorded as last-run.json for each completed group run. Its
    `process_exit` is always 0 -- a group's rollup lives in `exit_class`,
    not its process exit -- but it is still reconciled against the
    scheduler so a group whose parent launch was killed reads as
    `crashed`.

    `exit_class` is a rollup from `jobs_run`: timeout outranks
    fail / signal (which are equally severe), and ok / gated tie
    at the bottom (gating is "intentionally not run", not a
    group-level outcome). The status / list readers
    consult this single field for the group's STATUS value instead
    of re-deriving the rollup on every query.
    """

    jobs_run: list[GroupChildResult]


@dataclass
class LastRun:
    """Display-side view of `last-run.json`. Captures the fields
    status consumes regardless of whether the underlying
    record was a `JobRunResult` (the per-job runner output) or a
    `GroupRunResult` (the group-level rollup); the full records
    stay as the on-disk serialization shape. Fields are optional
    because last-run.json can be partial / corrupt and we'd rather
    surface "unknown" than have load_config abort.
    """

    exit_class: ExitClass | None
    started_at: str | None
    # The process exit the run recorded (JobRunResult / GroupRunResult
    # `process_exit`). None for a record predating the field or a
    # partial / corrupt one.
    process_exit: int | None
    # The pid the recording run wrote (its own pid). None for a record
    # predating the field.
    pid: int | None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> LastRun:
        """Extract the display-relevant subset of `last-run.json`.

        The full `JobRunResult` / `GroupRunResult` shapes are the
        on-disk serialization; status only consumes `exit_class`,
        `started_at`, and `process_exit`, so we pull those out
        tolerantly. A partial / corrupt last-run.json still produces a
        LastRun with whatever fields landed, rather than disqualifying
        the entity from runtime state altogether.
        """
        exit_class = ExitClass.parse(raw.get("exit_class"))
        started_at = raw.get("started_at")
        process_exit = raw.get("process_exit")
        pid = raw.get("pid")
        return cls(
            exit_class=exit_class,
            started_at=started_at if isinstance(started_at, str) else None,
            process_exit=(
                process_exit if isinstance(process_exit, int) else None
            ),
            pid=pid if isinstance(pid, int) else None,
        )


@dataclass
class RuntimeState:
    """Per-entity disk state outside `snapshot.json`. Populated for
    entities in `Config.current` and `Config.orphans`;
    pending-only entities don't have one. Built once at load and
    never re-read.

    `unit_config` is the path of the platform config unit -- the unit
    that defines and runs the job -- when present, else None.
    `unit_timer` is the path of the separate schedule-arming timer unit
    when the platform has one, else None (None too on a platform whose
    config unit carries its own schedule, and for an unscheduled entry).
    Both are captured here so subcommands read them from Config rather
    than walking the platform unit directory themselves; `unit_config is
    not None` is the "a config unit exists on disk" test. No scheduler
    state lives on RuntimeState: the current `Job` / `JobGroup` node pins
    both the unit-file drift (`unit_config_normalized` /
    `unit_timer_normalized`) and the load-time scheduler-loaded fact
    (`unit_loaded`) that `cfg_status` scores, so a config verdict is a
    pure node comparison that needs no RuntimeState lookup.
    """

    state_dir: Path
    last_run: LastRun | None
    is_running: bool
    is_pending: bool
    has_user_trigger_flag: bool
    unit_config: Path | None = None
    unit_timer: Path | None = None
    # The scheduler's last-launch outcome, captured at load alongside
    # `last_run`. None when the scheduler has no record (or wasn't
    # queried). Reconciled against `last_run` by `crashed`.
    unit_last_exit: crony.platform.UnitLastExit | None = None
    # The pid in run.pid -- the last launch's pid -- or None when the
    # job has never run (run.pid persists across runs, so absence means
    # no launch ever wrote it).
    run_pid: int | None = None
    # When the job last started, or None when it has never run.
    last_started_at: datetime.datetime | None = None

    @property
    def crashed(self) -> bool:
        """True when a launch ended without recording its own result --
        killed by a signal (OOM, jetsam, a manual kill, macOS
        OS_REASON_CODESIGNING, launchd unloading the unit) or exited
        before the runner wrote `last-run.json`. Two independent signals:

        - run.pid naming a different pid than the last record wrote: the
          last-started launch reached launch (wrote run.pid) but never
          wrote a record. A clean run overwrites run.pid here and records
          the same pid, so a disagreement is a launch that never
          recorded. Catches a kill even when the scheduler kept no exit
          record (e.g. the unit was unloaded).
        - The scheduler's last-launch status disagreeing with the
          recorded `process_exit`: catches a kill the scheduler saw
          after the runner wrote its own record (so run.pid matches the
          record and the first signal stays quiet).

        Never fires for an in-flight run (it holds the lock) or a clean
        exit (0). A scheduler-recorded exit is effectively never
        LOCK_BUSY: the scheduler coalesces a repeat fire of one unit
        into a no-op, and crony only ever invokes `_run` from a platform
        unit (group dispatch goes through the scheduler too), so a
        scheduled fire is normally the sole contender for its run.lock. A
        degenerate case that records one (a SIGKILLed guard leaving an
        orphaned `_run` holding the lock) still reconciles correctly: the
        orphan keeps the lock so `is_running` short-circuits, or has
        finished so the mismatch is a genuine `crashed`."""
        if self.is_running:
            return False
        if self.run_pid is not None:
            recorded_pid = self.last_run.pid if self.last_run else None
            if self.run_pid != recorded_pid:
                return True
        ule = self.unit_last_exit
        if ule is None or ule.exit_status == 0:
            return False
        recorded = self.last_run.process_exit if self.last_run else None
        return ule.exit_status != recorded


@dataclass
class Graph:
    """One world view: either the pending config (built from
    `TomlConfig` + cascade resolution) or the current applied state
    (built from `snapshot.json` files). The two graphs share an
    identity space (`EntityRef`) but their node sets and edge sets
    are independent: each can list entities the other doesn't, and
    a paired entity's fields may diverge between sides.
    """

    jobs: dict[crony.unit.EntityRef, Job] = field(default_factory=dict)
    groups: dict[crony.unit.EntityRef, JobGroup] = field(default_factory=dict)
    by_full_name: dict[str, crony.unit.EntityRef] = field(default_factory=dict)

    def refs(self) -> set[crony.unit.EntityRef]:
        return set(self.jobs) | set(self.groups)

    def nodes(self) -> list[Job | JobGroup]:
        """Every job and group node this graph holds (jobs first, then
        groups). The node-level analogue of `refs()`, for callers that
        want the entities themselves rather than their identities."""
        return [*self.jobs.values(), *self.groups.values()]

    def job_from_ref(self, ref: crony.unit.EntityRef) -> Job | JobGroup | None:
        """The job / group `ref` names in THIS graph, or None when
        this graph doesn't carry it. A single-source lookup: it never
        consults the other graph or the orphan map. Callers that want a
        cross-source order compose it explicitly so the preference
        shows at the call site -- e.g.
        `config.current.job_from_ref(r) or config.orphans.get(r)` for
        "current then orphans, never pending"."""
        return self.jobs.get(ref) or self.groups.get(ref)

    def replace_node(self, node: Job | JobGroup) -> None:
        """Store `node` under its ref, in whichever of `jobs` / `groups`
        matches its kind -- so a caller holding a `Job | JobGroup` from
        `job_from_ref` can write the updated node back without branching
        on type itself."""
        if isinstance(node, Job):
            self.jobs[node.entity_ref] = node
        else:
            self.groups[node.entity_ref] = node

    @classmethod
    def build_pending(
        cls,
        toml_config: crony.config.TomlConfig,
        host: str | None = None,
        platform: str | None = None,
        *,
        uv_path: Path | None = None,
        crony_path: Path | None = None,
    ) -> Graph:
        """Walk every bundle's TomlConfig, run cascade resolution +
        host/platform selection, and produce the pending graph. Entries
        masked out for this host don't appear in the graph -- they're
        not "what apply would install here", and they pair via the
        orphan path when their on-disk orphans outlive a host-filter
        edit.

        `host` / `platform` default to the current machine when
        omitted -- `resolve_target` self-resolves them. Tests pass
        explicit values to force selection for another host.

        `uv_path` / `crony_path` are the live executables the caller
        (`runtime.load_config`) resolves once and stamps onto every
        pending node so each can render its real unit self-contained.
        """
        pending = cls()
        # Resolve the platform once so every pending node's normalized
        # units render against the same backend the entries are selected
        # for (a test forcing another host's selection renders that
        # host's units too).
        resolved_platform = platform or crony.platform.current_platform()
        for bundle in toml_config.bundles:
            target = bundle.config.resolve_target(host, platform)
            sel_jobs, sel_groups = bundle.config.selected_jobs_and_groups(
                target
            )
            flag_map = bundle.config.resolved_flags_by_name(target)
            for short in sel_jobs:
                toml_job = bundle.config.jobs.get(short)
                if toml_job is None:
                    continue
                name = crony.unit.EntityName(bundle.name, short)
                snap_j = Job.from_config(
                    bundle.config,
                    toml_job,
                    name,
                    flags=flag_map.get(short, crony.config.JobFlags(0)),
                    platform=resolved_platform,
                    uv_path=uv_path,
                    crony_path=crony_path,
                )
                pending.jobs[snap_j.entity_ref] = snap_j
                pending.by_full_name[str(name)] = snap_j.entity_ref
            for short in sel_groups:
                toml_group = bundle.config.job_groups.get(short)
                if toml_group is None:
                    continue
                name = crony.unit.EntityName(bundle.name, short)
                snap_g = JobGroup.from_config(
                    bundle.config,
                    target,
                    toml_group,
                    name,
                    flags=flag_map.get(short, crony.config.JobFlags(0)),
                    platform=resolved_platform,
                    uv_path=uv_path,
                    crony_path=crony_path,
                )
                pending.groups[snap_g.entity_ref] = snap_g
                pending.by_full_name[str(name)] = snap_g.entity_ref
        return pending


@dataclass(frozen=True)
class JobOrphan:
    """Leftover on-disk junk for one entity that no live config
    selects -- a stray platform unit file, a stray short-name alias
    symlink, and/or a state dir whose snapshot won't parse. Never a
    usable current entity; the next apply / destroy reconciles it
    (re-render if config still wants it, otherwise remove).

    The kind is derived, not a separate type:

    - When a state-dir snapshot exists but can't be loaded (wrong
      schema, unrecognized kind, dataclass `TypeError`, unreadable
      JSON), `reason` (and `source_path`) are set; `is_broken` is True
      and `crony status` reports `config=broken` -- re-apply territory.
      The `uuid` is the real state-dir uuid; `name` is recovered from
      `raw["name"]` when the JSON parsed far enough, else None.
    - Otherwise it is a pure leftover (stray unit / alias, no parseable
      snapshot): `is_broken` is False and status reports
      `config=orphan`. `name` is always recovered, and `uuid` is a
      deterministic `uuid5(NAMESPACE_DNS, "crony.unit-only/<full>")` so
      repeat loads address the same entity -- with a unit and an alias
      under one name resolving to one ref.

    `has_unit_file` / `has_symlink` flag which stray artifacts are
    present (independently; some names carry both).
    """

    bundle: str
    uuid: str
    name: str | None
    has_unit_file: bool = False
    has_symlink: bool = False
    reason: str | None = None
    source_path: Path | None = None

    @property
    def is_broken(self) -> bool:
        """True when an unparseable on-disk snapshot is the orphan
        (status `broken`); False for a pure leftover (status `orphan`)."""
        return self.reason is not None

    @property
    def entity_ref(self) -> crony.unit.EntityRef:
        return crony.unit.EntityRef(self.bundle, self.uuid)

    @property
    def state_dir(self) -> Path:
        return _JobCommon.state_dir_from_ref(self.entity_ref)

    @property
    def full_name(self) -> str | None:
        """The recovered `<bundle>.<short>` name, or None when the
        remnant is too corrupt to carry one. The `str | None` (vs the
        node's always-set `str`) is the only shape difference when a
        `Job | JobGroup | JobOrphan` is read uniformly."""
        return self.name

    @property
    def state_dir_symlink_path(self) -> Path | None:
        """The short-name alias dir this orphan's name occupies, or
        None when no name was recovered (so destroy can still reclaim
        the alias whenever the name survived)."""
        if self.name is None:
            return None
        short = self.name.partition(".")[2]
        return crony.paths.STATE_DIR / self.bundle / short

    @property
    def log_path(self) -> Path:
        """Reported log path: the alias path when a stray alias is the
        orphan, else the uuid-keyed path."""
        if self.has_symlink and self.state_dir_symlink_path is not None:
            return self.state_dir_symlink_path / RUN_LOG_NAME
        return self.state_dir / RUN_LOG_NAME


@dataclass
class Config:
    """Whole-process state. Built once at startup by `load_config()`;
    treated as read-only by the rest of crony. Apply / destroy plan
    against the loaded Config, perform disk mutations, and either
    exit (the typical one-shot CLI case) or the caller re-invokes
    `load_config()` if it needs the post-mutation view.
    """

    toml_config: crony.config.TomlConfig
    pending: Graph
    current: Graph
    # Leftover on-disk junk keyed by ref -- broken snapshots and pure
    # orphans alike (`JobOrphan.is_broken` distinguishes them).
    orphans: dict[crony.unit.EntityRef, JobOrphan]
    orphans_by_full_name: dict[str, crony.unit.EntityRef]
    runtime: dict[crony.unit.EntityRef, RuntimeState]
    host: str
    platform: str
    # Current entries whose full name collides with another current
    # entry (uuid-edit residue that escaped cleanup, or a hand-mucked
    # state dir). The name in `current.by_full_name` resolves to the
    # config-matching ref; these shadowed refs keep their own
    # snapshot but surface by `<bundle>:<UUID>` in status so they
    # stay addressable for `crony destroy`.
    shadowed: set[crony.unit.EntityRef] = field(default_factory=set)

    def all_refs(self) -> set[crony.unit.EntityRef]:
        return self.pending.refs() | self.current.refs() | set(self.orphans)

    def installed_full_names(self) -> set[str]:
        """Full names with something installed on this host: a
        parseable current snapshot, or an orphan whose name was
        recovered (a leftover platform unit / alias symlink, or a
        broken snapshot). Computed from the one `load_config()` disk
        pass -- the addressable set status / trigger / enable /
        disable / destroy operate on, without re-walking the state-dir
        and unit trees per command.
        """
        return set(self.current.by_full_name) | set(self.orphans_by_full_name)

    def installed_bundle_names(self) -> set[str]:
        """Bundle names with on-disk orphans -- a current or broken
        snapshot, or a leftover platform unit / alias symlink. The
        bundle-scope analogue of `installed_full_names`: read-side
        subcommands
        address these even when the bundle's config has since
        broken (shadowed losers count too, so a bundle present only
        as collision residue stays addressable).
        """
        return {ref.bundle for ref in (self.current.refs() | set(self.orphans))}

    def require_addressable(self, bundle: str | None) -> None:
        """Reject `--bundle <name>` unless `<name>` is addressable on
        this host: either a successfully parsed bundle, or one with
        on-disk orphans. Used by the subcommands that address
        installed units (`status` / `destroy` / `enable` / `disable`
        / `trigger`); the on-disk fallback lets them scope to a bundle
        whose config has since broken -- exactly when the operator
        most needs to inspect, disarm, or tear it down. Commands that
        parse the pending config (`apply` / `validate`) use
        `TomlConfig.require_known` instead.
        """
        if bundle is None:
            return
        if self.toml_config.by_name(bundle) is not None:
            return
        if bundle in self.installed_bundle_names():
            return
        raise crony.errors.UsageError(f"unknown bundle: {bundle!r}")

    def cfg_status(self, ref: crony.unit.EntityRef) -> ConfigStatus:
        """synced | stale | broken | missing | orphan for `ref`.

        `broken` wins over the other states: an on-disk snapshot that
        can't be loaded, or an applied entry whose installed unit can't
        run -- its baked uv / crony binary is gone, its config unit file
        was deleted while the scheduler still has it loaded (it works now
        but dies on reboot), the scheduler has no unit loaded for it (it
        can't be triggered, by schedule, group, or hand), or a scheduled
        entry's schedule-arming timer file is gone (it will never fire) --
        is reported broken regardless of whether pending also defines it
        (apply re-renders / re-installs / reloads it).

        The remaining states mirror graph membership and on-disk health:
        `synced` if both graphs hold the entity and the two instances are
        field-equal (the normalized units included); `stale` if both hold
        it but differ -- including a hand-edited or moved-away unit whose
        normalized form no longer matches; `missing` if only `pending`
        has it and nothing usable is on disk (never applied, or the unit
        file is gone and unloaded); `orphan` if only `current` /
        `orphans` has it (config-side removed, disk-side lingers).

        A non-broken on-disk remnant (a snapshot-less / wiped dir)
        whose ref is still a live pending entry reads `stale`, not
        `orphan`: there is on-disk state, so re-apply -- not "never
        applied." A *broken* snapshot is `broken` even for a live
        entry (apply overwrites it).
        """
        orphan = self.orphans.get(ref)
        p = self.pending.job_from_ref(ref)
        c = self.current.job_from_ref(ref)
        if orphan is not None:
            if orphan.is_broken:
                return ConfigStatus.BROKEN
            if p is None:
                return ConfigStatus.ORPHAN
            return ConfigStatus.STALE
        if p is None and c is None:
            raise KeyError(ref)
        if p is None:
            return ConfigStatus.ORPHAN
        if c is None:
            return ConfigStatus.MISSING
        # Both graphs hold the entity (a live applied entry): score the
        # installed unit's on-disk health, pinned on the current node at
        # load. A unit that can't run is `broken`; a unit whose file is
        # gone and unloaded is `missing` (apply re-installs it). These
        # rank ahead of the snapshot field comparison.
        if not c.unit_config_exists:
            return (
                ConfigStatus.BROKEN if c.unit_loaded else ConfigStatus.MISSING
            )
        if c.uv_path is None or c.crony_path is None:
            return ConfigStatus.BROKEN
        # A unit the scheduler has no record of can't be triggered -- by
        # schedule, as part of a group, or by hand -- so it is broken
        # regardless of whether it is scheduled or disabled (a disabled
        # entry installs a loaded-but-schedule-less unit, which still
        # reads loaded). Re-apply reloads it.
        if not c.unit_loaded:
            return ConfigStatus.BROKEN
        # A scheduled, enabled entry fires through its schedule-arming
        # timer: when the pending node renders one (only systemd does --
        # launchd carries the schedule in the config unit) but it is gone
        # from disk, the entry never fires on its schedule -- broken
        # (re-apply re-renders it). A disabled / grouped entry renders no
        # timer, so this does not trip for them.
        if (
            p.unit_timer_normalized is not None
            and c.unit_timer_normalized is None
        ):
            return ConfigStatus.BROKEN
        return ConfigStatus.SYNCED if p == c else ConfigStatus.STALE

    def name_for(self, ref: crony.unit.EntityRef) -> str | None:
        """Recover the full namespaced name `ref` was last seen
        under -- from the pending entry, the current snapshot, or the
        on-disk orphan (a leftover unit / alias symlink or a recovered
        broken snapshot) -- or None when no side carries a name (a
        broken snapshot too corrupt to recover one).
        """
        for graph in (self.current, self.pending):
            node = graph.job_from_ref(ref)
            if node is not None:
                return str(node.entity_name)
        orphan = self.orphans.get(ref)
        if orphan is not None:
            return orphan.name
        return None

    def resolve_runnable(self, full_name: str) -> crony.unit.EntityRef | None:
        """A name with a parseable current snapshot. Source:
        `current` only, keyed by name.

        Broken / orphan / pending-only entries are absent from
        `current` and return None. `_snapshot_says_scheduled` uses
        this to read the applied schedule shape when guessing the
        UNIT NAME for an entry whose live config no longer describes
        it. Action commands gate on the resolved uuid's presence in
        the relevant graph (see `_resolve_action_targets`) rather
        than going through this name-keyed lookup, so a rename
        addressed by its new name still resolves.

        `<bundle>:<UUID>` ref-form inputs are honored only when
        the addressed entry is in `current` -- a ref-form
        address targeting a broken / orphan / unknown entry
        returns None on purpose.
        """
        ref = crony.unit.EntityRef.from_str(full_name)
        if ref is not None:
            return ref if ref in self.current.refs() else None
        return self.current.by_full_name.get(full_name)

    def resolve_current(self, full_name: str) -> crony.unit.EntityRef | None:
        """An entity with on-disk presence that destroy must
        clean up. Sources, in order: `current` (parseable
        snapshots), then `orphans` (a state dir whose snapshot
        can't be loaded, or a leftover platform unit / alias
        symlink with no snapshot at all).

        No `pending` fallback: a pending-only entry has no
        on-disk state to wipe. The current-first order matches
        what the installed unit's argv actually addresses
        (`<bundle>:<uuid>` resolved at apply time), so destroy
        cleans the dir the unit is reading from -- not a
        phantom dir produced by a pending uuid edit that hasn't
        been applied yet.
        """
        ref = crony.unit.EntityRef.from_str(full_name)
        if ref is not None:
            if ref in self.current.refs() or ref in self.orphans:
                return ref
            return None
        return self.current.by_full_name.get(
            full_name
        ) or self.orphans_by_full_name.get(full_name)

    def resolve_pending(self, full_name: str) -> crony.unit.EntityRef | None:
        """An entry defined in the parsed TOML config but not
        necessarily applied yet. Source: `pending` only.

        Callers that want a broader lookup (status, logs, the
        kind label for an orphan row) compose explicitly:
        `config.resolve_current(n) or config.resolve_pending(n)`
        for "current-first, fall back to pending," or
        `config.resolve_pending(n) or config.resolve_current(n)`
        for "pending-first." No single bias is baked in here so
        the chosen direction shows up at the call site.

        `<bundle>:<UUID>` ref-form inputs are honored only when
        the addressed entry is in `pending`.
        """
        ref = crony.unit.EntityRef.from_str(full_name)
        if ref is not None:
            return ref if ref in self.pending.refs() else None
        return self.pending.by_full_name.get(full_name)
