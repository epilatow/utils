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
from pathlib import Path
from typing import Any

import crony.config
import crony.errors
import crony.paths
import crony.unit

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


SNAPSHOT_SCHEMA: int = 4


@dataclass
class Job:
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

    schema: int
    kind: str  # "job"
    name: crony.unit.EntityName  # full namespaced name `<bundle>.<short>`
    uuid: str  # the entry's identity within the bundle (matches state-dir name)
    command: str | None
    script: str | None
    args: list[str]
    gate: str | None
    gate_script: str | None
    gate_args: list[str]
    env: dict[str, str]
    job_timeout_sec: int
    # `timing` captures the per-entry schedule / interval config that
    # drove the rendered platform unit, so `crony status` can show the
    # currently-active (applied) schedule independently of any later
    # edit to the live config. Default-None (on-demand) for back-compat
    # with snapshots written before a timing was pinned; loaders rely on
    # the dataclass default rather than a schema bump.
    timing: crony.unit.Timing | None = None
    # Process-priority class baked into the platform unit (HIGH / LOW /
    # NORMAL / None). Pinned in the snapshot so a change re-renders the
    # unit on the next apply. Default-None for back-compat with
    # snapshots written before this field existed.
    priority: crony.unit.PriorityClass | None = None
    # Whether the runner wraps the command in a power assertion to
    # keep the machine awake for its duration. Read at fire time;
    # default-False for back-compat with older snapshots.
    keep_awake: bool = False
    # Non-zero exit codes the runner classifies as success (read at
    # fire time). Default-empty for back-compat with older snapshots.
    success_exit_codes: list[int] = field(default_factory=list)
    # Interactive runner controls. `interactive_active_sec` and
    # `interactive_delay_sec` are always resolved (non-None) in
    # the snapshot so the runner doesn't re-consult any defaults
    # table; the per-job `_sec` knobs cascade through the resolver
    # to the baked defaults defined alongside the TomlJob dataclass.
    interactive: bool = False
    interactive_active_sec: int = crony.config.INTERACTIVE_ACTIVE_DEFAULT_SEC
    interactive_delay_sec: int = crony.config.INTERACTIVE_DELAY_DEFAULT_SEC

    @property
    def ref(self) -> crony.unit.EntityRef:
        return crony.unit.EntityRef(self.name.bundle, self.uuid)

    def unit_spec(self) -> crony.unit.UnitSpec:
        """The platform UnitSpec the scheduler renders / drift-checks."""
        return crony.unit.UnitSpec(
            name=self.name,
            ref=self.ref,
            timing=self.timing,
            priority=self.priority,
        )

    @classmethod
    def from_config(
        cls,
        config: crony.config.TomlBundleConfig,
        job: crony.config.TomlJob,
        name: crony.unit.EntityName,
    ) -> Job:
        """Build a Job by applying every cascade once."""
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
            schema=SNAPSHOT_SCHEMA,
            kind="job",
            name=name,
            uuid=job.uuid,
            command=job.command,
            script=script,
            args=args,
            gate=job.gate,
            gate_script=gate_script,
            gate_args=gate_args,
            env=config.resolved_env(job),
            job_timeout_sec=config.resolved_job_timeout_sec(job),
            timing=job.timing,
            priority=config.resolved_priority(job),
            keep_awake=config.resolved_keep_awake(job),
            success_exit_codes=list(job.success_exit_codes),
            interactive=job.interactive,
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
        """Serialize this job to its JSON dict. The typed value-object
        fields render back to their source strings, so snapshot.json
        stays string-keyed regardless of the in-memory types."""
        d = _snapshot_base_dict(self)
        d["priority"] = (
            str(self.priority) if self.priority is not None else None
        )
        return d


@dataclass
class JobGroup:
    """Resolved runtime parameters for a job-group. `children` are
    bundle-scoped uuids; the runner resolves each back to its
    current full name via the child's own snapshot at dispatch
    time so a rename in config doesn't flip the parent's snapshot.
    `group_budget_sec` is the pre-padded cumulative deadline
    computed once at apply time. 0 means no cap (some child is
    uncapped); the group runner treats it as an infinite deadline.
    """

    schema: int
    kind: str  # "group"
    name: crony.unit.EntityName  # full namespaced name `<bundle>.<short>`
    uuid: str
    children: list[str]
    group_budget_sec: int
    trigger_timeout_sec: int
    timing: crony.unit.Timing | None = None

    @property
    def ref(self) -> crony.unit.EntityRef:
        return crony.unit.EntityRef(self.name.bundle, self.uuid)

    def unit_spec(self) -> crony.unit.UnitSpec:
        """The platform UnitSpec the scheduler renders / drift-checks.
        Groups render without a priority."""
        return crony.unit.UnitSpec(
            name=self.name,
            ref=self.ref,
            timing=self.timing,
            priority=None,
        )

    @classmethod
    def from_config(
        cls,
        config: crony.config.TomlBundleConfig,
        target: crony.config.Target | None,
        group: crony.config.TomlJobGroup,
        name: crony.unit.EntityName,
    ) -> JobGroup:
        """Build a JobGroup. `group_budget_sec` is recomputed from
        the live config (not from children's pinned snapshots), so an
        apply pass that walks topologically still produces the right
        parent budget regardless of children's prior applied state.

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
        return cls(
            schema=SNAPSHOT_SCHEMA,
            kind="group",
            name=name,
            uuid=group.uuid,
            children=children,
            group_budget_sec=config.resolved_group_timeout_sec(
                target, group.name
            ),
            trigger_timeout_sec=config.defaults.trigger_timeout_sec,
            timing=group.timing,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this group to its JSON dict. The typed value-object
        fields render back to their source strings, so snapshot.json
        stays string-keyed regardless of the in-memory types."""
        return _snapshot_base_dict(self)


def _snapshot_base_dict(snap: Job | JobGroup) -> dict[str, Any]:
    """Serialize the fields Job and JobGroup share to a JSON dict,
    rendering the typed value-object fields back to their source
    strings. `Job.to_dict` layers the job-only `priority` on top."""
    d = dataclasses.asdict(snap)
    d["name"] = str(snap.name)
    timing = snap.timing
    d.pop("timing", None)
    d["schedule"] = (
        str(timing) if isinstance(timing, crony.unit.Schedule) else None
    )
    d["interval"] = (
        str(timing) if isinstance(timing, crony.unit.Interval) else None
    )
    return d


def entity_state_dir(ref: crony.unit.EntityRef) -> Path:
    return crony.paths.STATE_DIR / ref.bundle / ref.uuid


def snapshot_path_for(ref: crony.unit.EntityRef) -> Path:
    return entity_state_dir(ref) / "snapshot.json"


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
        return Job.from_config(config, config.jobs[short], name)
    if short in config.job_groups:
        return JobGroup.from_config(
            config, target, config.job_groups[short], name
        )
    raise crony.errors.PreconditionError(f"unknown job/group: {short!r}")


def snapshot_from_dict(raw: dict[str, Any]) -> Job | JobGroup:
    """Construct a snapshot from its JSON dict, parsing the typed
    value-object fields back from their source strings. Raises
    TypeError (wrong shape) or ValueError (bad typed field / unknown
    kind); callers treat both as a broken snapshot."""
    data = dict(raw)
    # `bundle` is redundant with the name's bundle; drop it so a
    # snapshot written before it was removed still loads.
    data.pop("bundle", None)
    if isinstance(data.get("name"), str):
        data["name"] = crony.unit.EntityName.from_str(data["name"])
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
    if data.get("priority") is not None:
        data["priority"] = crony.unit.PriorityClass.from_str(data["priority"])
    kind = data.get("kind")
    if kind == "job":
        return Job(**data)
    if kind == "group":
        return JobGroup(**data)
    raise ValueError(f"unknown snapshot kind {kind!r}")


@dataclass
class NotificationResult:
    """One channel's outcome inside a JobRunResult.notifications entry."""

    sent: bool
    error: str | None = None
    error_class: str | None = None


@dataclass
class JobRunResult:
    """Recorded as last-run.json for each completed job run.

    Identity isn't repeated in the record: the file already lives at
    `STATE_DIR/<bundle>/<uuid>/last-run.json`, and the matching
    `snapshot.json` in the same dir carries the full namespaced name
    of the entity it was applied as.
    """

    host: str
    platform: str
    started_at: str
    ended_at: str
    duration_sec: float
    exit_class: str
    exit_code: int | None
    signal: int | None
    # "none" if no gate ran (no config, or --skip-gate), "passed" on
    # exit 0, "failed" on any non-zero or timeout. The numeric exit
    # code stays in run.log for diagnosis; this field is the binary
    # answer "did the gate let the job run?".
    gate: str
    log_path: str
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
    exit_class: str
    exit_code: int


@dataclass
class GroupRunResult:
    """Recorded as last-run.json for each completed group run.

    Identity isn't repeated in the record: the file already lives at
    `STATE_DIR/<bundle>/<uuid>/last-run.json`, and the matching
    `snapshot.json` in the same dir carries the full namespaced
    name of the group it was applied as.

    `exit_class` is a rollup from `jobs_run`: timeout outranks
    fail / signal (which are equally severe), and ok / gated tie
    at the bottom (gating is "intentionally not run", not a
    group-level outcome). The status / list readers
    consult this single field for the group's LAST axis instead
    of re-deriving the rollup on every query.
    """

    host: str
    platform: str
    started_at: str
    ended_at: str
    duration_sec: float
    exit_class: str
    log_path: str
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

    exit_class: str | None
    started_at: str | None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> LastRun:
        """Extract the display-relevant subset of `last-run.json`.

        The full `JobRunResult` / `GroupRunResult` shapes are the
        on-disk serialization; status only consumes `exit_class` and
        `started_at`, so we pull those out tolerantly. A partial /
        corrupt last-run.json still produces a LastRun with whatever
        fields landed, rather than disqualifying the entity from
        runtime state altogether.
        """
        exit_class = raw.get("exit_class")
        started_at = raw.get("started_at")
        return cls(
            exit_class=exit_class if isinstance(exit_class, str) else None,
            started_at=started_at if isinstance(started_at, str) else None,
        )


@dataclass
class RuntimeState:
    """Per-entity disk state outside `snapshot.json`. Populated for
    entities in `Config.current` and `Config.unit_only`;
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

    `unit_is_stale` is True when the platform install diverges
    from what the snapshot would render: missing or hand-edited
    unit file, missing uv / crony binary baked into the file, or
    a unit the scheduler no longer has loaded (a schedule-less
    entry on linux is exempt from that last check -- its static,
    on-demand `.service` has no timer to load, so being unknown to
    the scheduler is its resting state, not drift). Drives the
    CONFIG=stale axis and forces apply to re-render even when
    the snapshot itself is unchanged. Set only for entries with
    a parseable snapshot; defaults False for broken / unit-only
    refs that don't have a snapshot to compare against.
    """

    state_dir: Path
    last_run: LastRun | None
    is_running: bool
    is_pending: bool
    has_user_trigger_flag: bool
    unit_config: Path | None = None
    unit_timer: Path | None = None
    unit_is_stale: bool = False


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

    def kind_of(self, ref: crony.unit.EntityRef) -> str | None:
        if ref in self.jobs:
            return "job"
        if ref in self.groups:
            return "group"
        return None

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
        orphan path when their on-disk remnants outlive a host-filter
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
            for short in sel_jobs:
                toml_job = bundle.config.jobs.get(short)
                if toml_job is None:
                    continue
                name = crony.unit.EntityName(bundle.name, short)
                snap_j = Job.from_config(bundle.config, toml_job, name)
                pending.jobs[snap_j.ref] = snap_j
                pending.by_full_name[str(name)] = snap_j.ref
            for short in sel_groups:
                toml_group = bundle.config.job_groups.get(short)
                if toml_group is None:
                    continue
                name = crony.unit.EntityName(bundle.name, short)
                snap_g = JobGroup.from_config(
                    bundle.config, target, toml_group, name
                )
                pending.groups[snap_g.ref] = snap_g
                pending.by_full_name[str(name)] = snap_g.ref
        return pending


@dataclass(frozen=True)
class UnitOnlyOrphan:
    """A platform-side entity with no corresponding state dir.

    Populated by `load_config` for any platform unit file whose
    `<bundle>.<short>` doesn't match a current or broken entity.
    The synthetic uuid is `uuid.uuid5(NAMESPACE_DNS,
    "crony.unit-only/<full_name>")` -- deterministic so repeat
    loads address the same entity, indistinguishable downstream
    from a config-generated uuid4.
    """

    bundle: str
    uuid: str
    name: str
    has_unit_file: bool

    @property
    def ref(self) -> crony.unit.EntityRef:
        return crony.unit.EntityRef(self.bundle, self.uuid)


@dataclass(frozen=True)
class BrokenEntity:
    """An on-disk entity this crony binary can't fully interpret.

    Populated by `_build_current_graph` whenever a state-dir
    snapshot exists but can't be turned into a `Job` /
    `JobGroup`: wrong `schema`, unrecognized `kind`, dataclass
    `TypeError`, or unreadable JSON. `name` is recovered from
    `raw["name"]` when the JSON parsed far enough; `None` when
    the file itself is corrupt. `reason` is a one-line
    description suitable for surfacing in status (the
    operator needs to know *why* re-apply is required).
    `source_path` is the on-disk artifact that couldn't be
    interpreted -- today always the entity's snapshot.json, but
    the dataclass deliberately doesn't bake that assumption into
    its name so other broken-entity kinds can share it.
    """

    bundle: str
    uuid: str
    name: str | None
    reason: str
    source_path: Path

    @property
    def ref(self) -> crony.unit.EntityRef:
        return crony.unit.EntityRef(self.bundle, self.uuid)


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
    broken: dict[crony.unit.EntityRef, BrokenEntity]
    broken_by_full_name: dict[str, crony.unit.EntityRef]
    unit_only: dict[crony.unit.EntityRef, UnitOnlyOrphan]
    unit_only_by_full_name: dict[str, crony.unit.EntityRef]
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
        return (
            self.pending.refs()
            | self.current.refs()
            | set(self.broken)
            | set(self.unit_only)
        )

    def installed_full_names(self) -> set[str]:
        """Full names with something installed on this host: a
        parseable current snapshot, a leftover platform unit
        (`unit_only`), or a broken snapshot whose name was
        recovered. Computed from the one `load_config()` disk pass
        -- the addressable set status / trigger / enable / disable
        / destroy operate on, without re-walking the state-dir and
        unit trees per command.
        """
        return (
            set(self.current.by_full_name)
            | set(self.unit_only_by_full_name)
            | set(self.broken_by_full_name)
        )

    def installed_bundle_names(self) -> set[str]:
        """Bundle names with on-disk remnants -- a current or broken
        snapshot, or a leftover platform unit. The bundle-scope
        analogue of `installed_full_names`: read-side subcommands
        address these even when the bundle's config has since
        broken (shadowed losers count too, so a bundle present only
        as collision residue stays addressable).
        """
        return {
            ref.bundle
            for ref in (
                self.current.refs() | set(self.broken) | set(self.unit_only)
            )
        }

    def require_addressable(self, bundle: str | None) -> None:
        """Reject `--bundle <name>` unless `<name>` is addressable on
        this host: either a successfully parsed bundle, or one with
        on-disk remnants. Used by the subcommands that address
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

    def config_state(self, ref: crony.unit.EntityRef) -> str:
        """synced | stale | broken | missing | orphan for `ref`.

        `broken` wins over the other axes: if the on-disk
        snapshot can't be loaded the entity is reported as
        broken regardless of whether pending also defines it
        (apply will overwrite the broken snapshot with a fresh
        one). The remaining axes mirror graph membership:
        `synced` if both graphs hold the entity and the two
        instances are field-equal; `stale` if both hold it but
        differ; `missing` if only `pending` has it (never
        applied); `orphan` if only `current` or `unit_only` has
        it (config-side removed, disk-side lingers).
        """
        if ref in self.broken:
            return "broken"
        if ref in self.unit_only:
            return "orphan"
        p = self.pending.jobs.get(ref) or self.pending.groups.get(ref)
        c = self.current.jobs.get(ref) or self.current.groups.get(ref)
        if p is None and c is None:
            raise KeyError(ref)
        if p is None:
            return "orphan"
        if c is None:
            return "missing"
        return "synced" if p == c else "stale"

    def name_for(self, ref: crony.unit.EntityRef) -> str | None:
        """Recover the full namespaced name `ref` was last seen
        under -- from the pending entry, the current snapshot, the
        recovered broken-entity name, or the unit-only orphan --
        or None when no side carries a name (a broken snapshot too
        corrupt to recover one).
        """
        for graph in (self.current, self.pending):
            j = graph.jobs.get(ref)
            if j is not None:
                return str(j.name)
            g = graph.groups.get(ref)
            if g is not None:
                return str(g.name)
        broken = self.broken.get(ref)
        if broken is not None:
            return broken.name
        unit_only = self.unit_only.get(ref)
        if unit_only is not None:
            return unit_only.name
        return None

    def resolve_runnable(self, full_name: str) -> crony.unit.EntityRef | None:
        """A name with a parseable current snapshot. Source:
        `current` only, keyed by name.

        Broken / unit-only / pending-only entries are absent from
        `current` and return None. `_snapshot_says_scheduled` uses
        this to read the applied schedule shape when guessing the
        UNIT NAME for an entry whose live config no longer describes
        it. Action commands gate on the resolved uuid's presence in
        the relevant graph (see `_resolve_action_targets`) rather
        than going through this name-keyed lookup, so a rename
        addressed by its new name still resolves.

        `<bundle>:<UUID>` ref-form inputs are honored only when
        the addressed entry is in `current` -- a ref-form
        address targeting a broken / unit-only / unknown entry
        returns None on purpose.
        """
        ref = crony.unit.EntityRef.from_str(full_name)
        if ref is not None:
            return ref if ref in self.current.refs() else None
        return self.current.by_full_name.get(full_name)

    def resolve_current(self, full_name: str) -> crony.unit.EntityRef | None:
        """An entity with on-disk presence that destroy must
        clean up. Sources, in order: `current` (parseable
        snapshots), `broken` (state dirs whose snapshot can't
        be loaded), then `unit_only` (platform unit files with
        no state dir).

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
            if (
                ref in self.current.refs()
                or ref in self.broken
                or ref in self.unit_only
            ):
                return ref
            return None
        return (
            self.current.by_full_name.get(full_name)
            or self.broken_by_full_name.get(full_name)
            or self.unit_only_by_full_name.get(full_name)
        )

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
