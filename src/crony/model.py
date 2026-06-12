# This is AI generated code

"""crony's in-memory domain model.

The Job / JobGroup entities and the Config graph that ties the pending
config view to the current applied view, plus the runtime-state and
last-run value types those graphs reference. Holds the pure
config->graph construction (cascade resolution + host/platform
selection) and the model<->dict serialization that backs snapshot.json.

Everything here is pure: it operates on already-parsed config objects
and produces in-memory model values. Disk reads, locks, and scheduler
queries live in crony.runtime.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import crony.config
import crony.errors
import crony.paths
import crony.platform
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


# The schema version current code writes into every snapshot.json.
# Bump it whenever the serialized shape changes in a way that needs a
# matching `# v<N> compat` upgrade in `snapshot_from_dict`.
CURRENT_SNAPSHOT_SCHEMA: int = 5

# Every schema version current code can still load: CURRENT plus each
# older version a `# v<N> compat` block in `snapshot_from_dict` knows
# how to read forward. A snapshot whose schema is outside this set is
# rejected as needing re-apply. Drop a version here (and delete its
# tagged compat block) once no snapshots at that schema remain.
COMPAT_SNAPSHOT_SCHEMA: frozenset[int] = frozenset({4, 5})

# The per-entry log file name, kept in one place so no caller inlines
# the literal. Lives inside the entry's state dir (uuid-keyed) and is
# reachable through the short-name alias.
RUN_LOG_NAME: str = "run.log"

# Flags persist in the snapshot as one boolean per member rather than
# the bitmask value (JSON has no IntFlag), keyed by the flag's dash
# token (`keep-awake`) so the snapshot spelling matches the config one.
# `snapshot_from_dict` also reads the legacy underscore spelling
# (`keep_awake`) that older snapshots used, so they still load; that
# fallback goes once no underscore-keyed snapshots remain. Derived from
# `JobFlags.members()`, so a new flag needs no edit to `to_dict` /
# `snapshot_from_dict`.
_FLAG_SNAPSHOT_FIELDS: tuple[tuple[str, crony.config.JobFlags], ...] = tuple(
    (flag.token, flag) for flag in crony.config.JobFlags.members()
)


class EntityKind(StrEnum):
    """Whether a snapshot entry is a single job or a job-group. Pinned
    in snapshot.json under the `kind` key and used as the discriminator
    `snapshot_from_dict` branches on. A StrEnum so it serializes as its
    plain value and on-disk records round-trip unchanged."""

    JOB = "job"
    GROUP = "group"


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
    kind: EntityKind  # whether this entry is a job or a group
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

    def unit_spec(self, cmd: tuple[str, ...]) -> crony.unit.UnitSpec:
        """The platform UnitSpec the scheduler renders / drift-checks."""
        return crony.unit.UnitSpec(
            name=self.entity_name,
            cmd=cmd,
            timing=self.timing,
            priority=self._unit_priority,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this entry to its JSON dict, rendering the typed
        value-object fields back to their source strings so snapshot.json
        stays string-keyed regardless of the in-memory types. `Job`
        layers the job-only `priority` on top."""
        d = dataclasses.asdict(self)
        # The format version persists under the `schema` JSON key; the
        # in-memory field is `snapshot_schema` (unambiguous at its use
        # sites and in the STALE column).
        d["schema"] = d.pop("snapshot_schema")
        # snapshot.json stores the full `<bundle>.<short>` name; `bundle`
        # is redundant with it and recomputed on load.
        d["name"] = str(self.entity_name)
        d.pop("bundle", None)
        # The alias pair is derived disk / expected state, recomputed on
        # load -- it never belongs in the persisted snapshot.
        d.pop("state_dir_symlink", None)
        # Flags persist as one boolean per member (see
        # `_FLAG_SNAPSHOT_FIELDS`), never the bitmask value itself;
        # folded back in `snapshot_from_dict`. Emitted for jobs and
        # groups alike so a group's resolved cascade value round-trips
        # -- it has no runtime effect but stays visible for tracing
        # inheritance.
        d.pop("flags", None)
        for key, flag in _FLAG_SNAPSHOT_FIELDS:
            d[key] = flag in self.flags
        timing = self.timing
        d.pop("timing", None)
        d["schedule"] = (
            str(timing) if isinstance(timing, crony.unit.Schedule) else None
        )
        d["interval"] = (
            str(timing) if isinstance(timing, crony.unit.Interval) else None
        )
        return d


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
    ) -> Job:
        """Build a Job by applying every cascade once. `flags` is the
        entry's resolved capability bitmask (composed across the config
        levels by the caller); the runner-facing `interactive` /
        `keep_awake` booleans derive from it. When omitted, it defaults
        to the defaults composed with the job's own delta -- the
        no-ancestor-group case."""
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
        return cls(
            snapshot_schema=CURRENT_SNAPSHOT_SCHEMA,
            kind=EntityKind.JOB,
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
            timeout=config.resolved_job_timeout_sec(job),
            timing=job.timing,
            priority=config.resolved_priority(job),
            flags=flags,
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

    def to_dict(self) -> dict[str, Any]:
        """Extend the shared snapshot dict with the job-only `priority`
        (rendered to its source string)."""
        d = super().to_dict()
        d["priority"] = str(self.priority)
        # The wrapper state is derived runtime state (expected on the
        # config side, probed on the current side), recomputed on load --
        # never persisted. Job-only, so it is dropped here rather than in
        # the shared serializer.
        d.pop("fda_wrapper", None)
        return d


@dataclass(frozen=True)
class JobGroup(_JobCommon):
    """Resolved runtime parameters for a job-group. `children` are
    bundle-scoped uuids; the runner resolves each back to its
    current full name via the child's own snapshot at dispatch
    time so a rename in config doesn't flip the parent's snapshot.
    The inherited `timeout` holds the pre-padded cumulative deadline
    computed once at apply time. 0 means no cap (some child is
    uncapped); the group runner treats it as an infinite deadline.
    """

    children: list[str]
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
    ) -> JobGroup:
        """Build a JobGroup. The cumulative `timeout` budget is
        recomputed from the live config (not from children's pinned
        snapshots), so an apply pass that walks topologically still
        produces the right parent budget regardless of children's prior
        applied state.

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
        # Children are stored as uuids (not full names) so renaming a
        # child in config doesn't flip the parent's snapshot -- the
        # uuid edge is unchanged. The runner resolves each child uuid
        # to its current full name by reading the child's snapshot at
        # dispatch time.
        children: list[str] = []
        for c in group.jobs:
            if c in sel_jobs:
                children.append(config.jobs[c].uuid)
            elif c in sel_groups:
                children.append(config.job_groups[c].uuid)
        if flags is None:
            flags = config.composed_flags(group.flags)
        return cls(
            snapshot_schema=CURRENT_SNAPSHOT_SCHEMA,
            kind=EntityKind.GROUP,
            bundle=name.bundle,
            name=name.short,
            uuid=group.uuid,
            state_dir_symlink=cls.state_dir_symlink_expected(name, group.uuid),
            children=children,
            timeout=config.resolved_group_timeout_sec(target, group.name),
            trigger_timeout_sec=config.defaults.trigger_timeout_sec,
            timing=group.timing,
            flags=flags,
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


def snapshot_from_dict(
    raw: dict[str, Any],
    *,
    state_dir_symlink: tuple[Path, str] | None = None,
    fda_wrapper: FDAWrapper | None = None,
) -> Job | JobGroup:
    """Construct a snapshot from its JSON dict, parsing the typed
    value-object fields back from their source strings. Raises
    TypeError (wrong shape) or ValueError (bad typed field / unknown
    kind); callers treat both as a broken snapshot.

    `state_dir_symlink` is the on-disk alias pair the caller read for this entry
    (None when it has no link, or for a load that doesn't care about
    the alias). It is not part of `raw` -- the alias is derived disk
    state, never serialized -- so the current-graph scan passes the
    pair it read so the frozen node carries it from construction.

    `fda_wrapper` is the live Crony.app wrapper state the caller probed,
    applied only when the loaded entry carries the full-disk-access flag
    (None otherwise). Like the alias, it is derived runtime state, never
    serialized; a load that does not care about the wrapper leaves it
    None."""
    data = dict(raw)
    data["state_dir_symlink"] = state_dir_symlink
    # The format version is keyed `schema` on disk; the field is
    # `snapshot_schema`. A missing key leaves the kwarg absent so the
    # dataclass raises a clear "missing snapshot_schema" TypeError, which
    # callers treat as a broken snapshot.
    if "schema" in data:
        data["snapshot_schema"] = data.pop("schema")
    # snapshot.json stores the full `<bundle>.<short>` name; split it
    # back into the `bundle` + short `name` fields (overriding any
    # legacy `bundle` key a very old snapshot may still carry).
    if isinstance(data.get("name"), str):
        en = crony.unit.EntityName.from_str(data["name"])
        data["bundle"] = en.bundle
        data["name"] = en.short
    schedule_str = data.pop("schedule", None)
    interval_str = data.pop("interval", None)
    timing: crony.unit.Timing | None
    if schedule_str is not None:
        timing = crony.unit.Schedule.from_str(schedule_str)
    elif interval_str is not None:
        timing = crony.unit.Interval.from_str(interval_str)
    else:
        timing = None
    data["timing"] = timing
    # A job snapshot always carries `priority`; an older one stored
    # `null` for the neutral class, which now loads as NORMAL. A group
    # snapshot has no priority key, so leave it absent for them.
    if "priority" in data:
        pv = data["priority"]
        data["priority"] = (
            crony.unit.PriorityClass.from_str(pv)
            if pv is not None
            else crony.unit.PriorityClass.NORMAL
        )
    # The per-flag booleans are stored in the snapshot; fold them back
    # into the bitmask for jobs and groups alike. Current snapshots key
    # by the dash token; older ones used the underscore spelling
    # (`keep_awake`), so read both (the legacy alias goes once none
    # remain). Absent keys default off, so a pre-flags snapshot loads
    # without a schema bump.
    flags = crony.config.JobFlags(0)
    for key, flag in _FLAG_SNAPSHOT_FIELDS:
        enabled = data.pop(key, False)
        legacy = data.pop(key.replace("-", "_"), False)
        if enabled or legacy:
            flags |= flag
    data["flags"] = flags
    # v4 compat: schema 4 stored the per-entry deadline under a
    # kind-specific key (`job_timeout_sec` for jobs, `group_budget_sec`
    # for groups); schema 5 unified them into `timeout`. Map the v4 keys
    # forward so a schema-4 snapshot loads. Drop this block when 4
    # leaves COMPAT_SNAPSHOT_SCHEMA.
    legacy_timeout = data.pop("group_budget_sec", None)
    legacy_timeout = data.pop("job_timeout_sec", legacy_timeout)
    if "timeout" not in data and legacy_timeout is not None:
        data["timeout"] = legacy_timeout
    # The on-disk `kind` is the str the entry was serialized under;
    # fold it back to the typed discriminator the dataclass field holds.
    kind = data.get("kind")
    if kind == EntityKind.JOB:
        data["kind"] = EntityKind.JOB
        # The wrapper state is not serialized (job-only derived runtime
        # state); stamp the caller's probed value, gated on the
        # full-disk-access flag, so a current node carries the live
        # state from construction.
        data["fda_wrapper"] = Job._fda_wrapper_for(flags, fda_wrapper)
        return Job(**data)
    if kind == EntityKind.GROUP:
        data["kind"] = EntityKind.GROUP
        return JobGroup(**data)
    raise ValueError(f"unknown snapshot kind {kind!r}")


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


class JobStatus(StrEnum):
    """The verdict `crony status` shows in its STATUS column: the run
    outcomes that actually reach the cell plus the display-only states
    derived at read time. Shares string values with `ExitClass` for the
    outcomes it carries (so the two compare and serialize alike); it
    omits `signal` (folded to `fail`) and `dispatched` (shown as
    `unknown`), which never surface in the cell."""

    OK = ExitClass.OK
    FAIL = ExitClass.FAIL
    TIMEOUT = ExitClass.TIMEOUT
    GATED = ExitClass.GATED
    CANCELED = ExitClass.CANCELED
    CRASHED = "crashed"
    RUNNING = "running"
    PENDING = "pending"
    NEVER = "never"
    UNKNOWN = "unknown"


class ConfigStatus(StrEnum):
    """The verdict `crony status` shows in its CONFIG column: how the
    live config view relates to the applied on-disk state. A StrEnum so
    it compares against and renders as its plain value.

    `Config.config_state` produces the base verdicts that score the
    pending graph against the current one -- SYNCED / STALE / BROKEN /
    MISSING / ORPHAN. The status caller layers the two host-filter
    verdicts on top: ERROR for an entry whose bundle config was
    rejected, and MASKED for one excluded on this host with no on-disk
    remnant."""

    SYNCED = "synced"
    STALE = "stale"
    BROKEN = "broken"
    MISSING = "missing"
    ORPHAN = "orphan"
    MASKED = "masked"
    ERROR = "error"


class GateResult(StrEnum):
    """A run's gate outcome, recorded as `gate` in last-run.json. NONE
    when no gate ran (no gate configured, or --skip-gate); PASSED on a
    gate exit 0; FAILED on any non-zero or timeout. A StrEnum so it
    serializes as its plain value and reads back unchanged."""

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
    # `RuntimeState.crashed` compares it to the surviving run.pid to spot
    # a launch that started but died before writing this record.
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
    consult this single field for the group's STATUS axis instead
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
    # partial / corrupt one. `RuntimeState.crashed` compares it to the
    # scheduler's reported status to spot a launch that left no record.
    process_exit: int | None
    # The pid the recording run wrote (its own pid). None for a record
    # predating the field. `RuntimeState.crashed` compares it to the
    # surviving run.pid: a different pid means the last-started launch
    # never wrote a record.
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
    not None` is the "a config unit exists on disk" test. The platform
    unit *file* presence and the unit-drift check are captured here at
    load time; the live scheduler enable/disable state is not -- status'
    UNIT axis queries `unit_state` on demand, since the scheduler view
    can change between load and read.

    `unit_drift` holds the unit-file kinds (`config` / `timer`) whose
    platform install diverges from what the snapshot would render:
    missing or hand-edited unit file, missing uv / crony binary baked
    into the file, or the schedule-bearing unit the scheduler no longer
    has loaded. The derived `unit_is_stale` (any drift) drives the
    CONFIG=stale axis and forces apply to re-render even when the
    snapshot itself is unchanged. Set only for entries with a parseable
    snapshot; empty for broken / unit-only refs that have no snapshot to
    compare against.
    """

    state_dir: Path
    last_run: LastRun | None
    is_running: bool
    is_pending: bool
    has_user_trigger_flag: bool
    unit_config: Path | None = None
    unit_timer: Path | None = None
    unit_drift: frozenset[str] = frozenset()
    # The scheduler's last-launch outcome, captured at load alongside
    # `last_run`. None when the scheduler has no record (or wasn't
    # queried). Reconciled against `last_run` by `crashed`.
    unit_last_exit: crony.platform.UnitLastExit | None = None
    # The pid in the surviving run.pid file, or None when no run.pid is
    # present (the normal resting state -- the runner unlinks it on a
    # clean exit). A lingering run.pid is the fingerprint of a launch
    # that didn't reach its own cleanup; `crashed` compares it to the
    # pid the last record was written by.
    run_pid: int | None = None

    @property
    def unit_is_stale(self) -> bool:
        """True when any unit file has drifted from the snapshot."""
        return bool(self.unit_drift)

    @property
    def crashed(self) -> bool:
        """True when a launch ended without recording its own result --
        killed by a signal (OOM, jetsam, a manual kill, macOS
        OS_REASON_CODESIGNING, launchd unloading the unit) or exited
        before the runner wrote `last-run.json`. Two independent signals:

        - A surviving run.pid naming a different pid than the last record
          wrote: the last-started launch reached launch (wrote run.pid)
          but never wrote a record (which would have carried that pid and
          unlinked run.pid). Catches a kill even when the scheduler kept
          no exit record (e.g. the unit was unloaded).
        - The scheduler's last-launch status disagreeing with the
          recorded `process_exit`: catches a kill after the record was
          written, when run.pid was already unlinked.

        Never fires for an in-flight run (it holds the lock) or a clean
        exit (0)."""
        if self.is_running:
            return False
        if self.run_pid is not None:
            recorded_pid = self.last_run.pid if self.last_run else None
            if self.run_pid != recorded_pid:
                return True
        ule = self.unit_last_exit
        if ule is None or ule.exit_status == 0:
            return False
        if ule.exit_status == int(crony.errors.ExitCode.LOCK_BUSY):
            # A fire coalesced against an in-flight run: the loser exits
            # LOCK_BUSY and writes no record by design, so the mismatch
            # is a benign skip, not a crash.
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

    @classmethod
    def build_pending(
        cls,
        toml_config: crony.config.TomlConfig,
        host: str | None = None,
        platform: str | None = None,
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
        """
        pending = cls()
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

    def config_state(self, ref: crony.unit.EntityRef) -> ConfigStatus:
        """synced | stale | broken | missing | orphan for `ref`.

        `broken` wins over the other axes: if the on-disk
        snapshot can't be loaded the entity is reported as
        broken regardless of whether pending also defines it
        (apply will overwrite the broken snapshot with a fresh
        one). The remaining axes mirror graph membership:
        `synced` if both graphs hold the entity and the two
        instances are field-equal; `stale` if both hold it but
        differ; `missing` if only `pending` has it and nothing is
        on disk (never applied); `orphan` if only `current` /
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
