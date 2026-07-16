# This is AI generated code

"""crony's snapshot.json on-disk format.

A typed pydantic model of the snapshot.json shape (`JobSnapshot` /
`GroupSnapshot`), distinct from the in-memory `crony.model.Job` /
`JobGroup`: it carries only the persisted fields (no derived disk /
runtime state), keys the flags as their per-member booleans, and holds
the value objects as their source strings. `extra="forbid"` rejects an
unknown key, and each field is tagged with the schema version it was
introduced in, so `CURRENT_SNAPSHOT_SCHEMA` derives from the newest and
the persisted shape can't change without a version landing alongside it.

This module knows nothing about `Job` / `JobGroup`: `parse` validates raw
JSON into the model, and `crony.model` builds the nodes from it (and
`<node>._to_snapshot` builds the model from a node to dump). The model owns
the rehydration of its own value-object fields: `kind` is typed as the
`crony.unit.EntityKind` it decodes to, and `.entity_name()` /
`.entity_ref()` / `.timing()` / `.job_flags()` / `.priority_class()`
parse the stored strings / booleans back into their `crony.unit` /
`crony.config` types -- so `crony.model` consumes typed values without
knowing the on-disk encoding.
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

import crony.config
import crony.unit


@dataclass(frozen=True)
class _Since:
    """Schema version a snapshot field was introduced in, carried in the
    field's `Annotated` metadata. pydantic ignores it; only the schema
    helpers below read it off `model_fields`."""

    version: int


class _SnapshotModel(BaseModel):
    # extra="forbid": an unknown key is a broken snapshot (the schema
    # gate normally rejects a newer shape first; this catches corruption
    # within a known schema). frozen mirrors the immutable nodes.
    # populate_by_name lets `crony.model` build the model by field name
    # while the disk keys come from the validation / serialization
    # aliases.
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True
    )


# A field whose on-disk key differs from its Python name uses
# validation_alias + serialization_alias (rather than `alias`) so the
# field name stays the constructor keyword -- which keeps the
# `_to_snapshot` construction in crony.model statically type-checked
# without the pydantic mypy plugin (the plugin can't be a global mypy
# setting: it would break the non-pydantic utilities' own mypy runs).
def _disk_key(key: str, **kw: Any) -> Any:
    return Field(validation_alias=key, serialization_alias=key, **kw)


class _SnapshotCommon(_SnapshotModel):
    # `schema` on disk; `snapshot_schema` in memory (avoids shadowing
    # pydantic's deprecated BaseModel.schema()).
    snapshot_schema: Annotated[int, _Since(4)] = _disk_key("schema")
    kind: Annotated[crony.unit.EntityKind, _Since(4)]
    name: Annotated[str, _Since(4)]  # full <bundle>.<short>
    uuid: Annotated[str, _Since(4)]
    timeout: Annotated[int, _Since(5)]
    schedule: Annotated[str | None, _Since(4)] = None
    interval: Annotated[str | None, _Since(4)] = None
    # The trigger-only firing mode (`on-demand = true` in config);
    # mutually exclusive with `schedule` / `interval` above, so at most
    # one of the three timing keys is set. Persisted so the applied side
    # shows `on-demand` and a switch to / from a real schedule reads as
    # drift.
    on_demand: Annotated[bool, _Since(7)] = _disk_key(
        "on-demand", default=False
    )
    unit_disabled: Annotated[bool, _Since(6)] = False
    # One boolean per JobFlags member, keyed on disk by the flag's dash
    # token. A drift test asserts these stay in lockstep with
    # JobFlags.members().
    interactive: Annotated[bool, _Since(4)] = False
    keep_awake: Annotated[bool, _Since(4)] = _disk_key(
        "keep-awake", default=False
    )
    full_disk_access: Annotated[bool, _Since(5)] = _disk_key(
        "full-disk-access", default=False
    )

    def entity_name(self) -> crony.unit.EntityName:
        """This snapshot's full `<bundle>.<short>` name as its value
        object."""
        return crony.unit.EntityName.from_str(self.name)

    def entity_ref(self) -> crony.unit.EntityRef:
        """This snapshot's stable identity: its uuid scoped to its
        bundle (recovered from the full name)."""
        return crony.unit.EntityRef(self.entity_name().bundle, self.uuid)

    def timing(self) -> crony.unit.Timing | None:
        """The Timing value object for this snapshot's timing keys:
        OnDemand for the trigger-only mode, else the schedule / interval
        string, else None (a transit group or group-only job)."""
        if self.on_demand:
            return crony.unit.OnDemand()
        if self.schedule is not None:
            return crony.unit.Schedule.from_str(self.schedule)
        if self.interval is not None:
            return crony.unit.Interval.from_str(self.interval)
        return None

    def job_flags(self) -> crony.config.JobFlags:
        """This snapshot's per-flag booleans folded back into the
        bitmask."""
        flags = crony.config.JobFlags(0)
        if self.interactive:
            flags |= crony.config.JobFlags.INTERACTIVE
        if self.keep_awake:
            flags |= crony.config.JobFlags.KEEP_AWAKE
        if self.full_disk_access:
            flags |= crony.config.JobFlags.FULL_DISK_ACCESS
        return flags


class JobSnapshot(_SnapshotCommon):
    kind: Annotated[Literal[crony.unit.EntityKind.JOB], _Since(4)]
    command: Annotated[str | None, _Since(4)]
    script: Annotated[str | None, _Since(4)]
    args: Annotated[list[str], _Since(4)]
    gate: Annotated[str | None, _Since(4)]
    gate_script: Annotated[str | None, _Since(4)]
    gate_args: Annotated[list[str], _Since(4)]
    env: Annotated[dict[str, str], _Since(4)]
    # null in a very old snapshot loads as the NORMAL class.
    priority: Annotated[str | None, _Since(4)] = None
    success_exit_codes: Annotated[list[int], _Since(4)] = Field(
        default_factory=list
    )
    interactive_active_sec: Annotated[int, _Since(4)] = (
        crony.config.INTERACTIVE_ACTIVE_DEFAULT_SEC
    )
    interactive_delay_sec: Annotated[int, _Since(4)] = (
        crony.config.INTERACTIVE_DELAY_DEFAULT_SEC
    )

    def priority_class(self) -> crony.unit.PriorityClass:
        """This snapshot's process-priority class (NORMAL when a very
        old snapshot stored none)."""
        if self.priority is not None:
            return crony.unit.PriorityClass.from_str(self.priority)
        return crony.unit.PriorityClass.NORMAL


class GroupSnapshot(_SnapshotCommon):
    kind: Annotated[Literal[crony.unit.EntityKind.GROUP], _Since(4)]
    # Bundle-scoped child uuids on disk (bundle implicit: a group only
    # references children in its own bundle). `child_refs` pairs each
    # with that bundle into the EntityRef the node carries. Stored as
    # uuids -- not full names -- so a child rename doesn't flip the
    # parent's snapshot.
    children: Annotated[list[str], _Since(4)]
    trigger_timeout_sec: Annotated[int, _Since(4)]

    def child_refs(self) -> list[crony.unit.EntityRef]:
        """This group's children as their value objects: each on-disk
        uuid scoped to this group's own bundle."""
        bundle = self.entity_name().bundle
        return [crony.unit.EntityRef(bundle, u) for u in self.children]


# Validates a migrated dict into the right model by its `kind`
# discriminator; an unrecognized kind raises ValidationError (a
# ValueError) -- a broken snapshot.
_SNAPSHOT_ADAPTER: TypeAdapter[JobSnapshot | GroupSnapshot] = TypeAdapter(
    Annotated[JobSnapshot | GroupSnapshot, Field(discriminator="kind")]
)


def _model_field_versions() -> list[int]:
    """The `_Since` version of every field across both snapshot models."""
    return [
        meta.version
        for model in (JobSnapshot, GroupSnapshot)
        for info in model.model_fields.values()
        for meta in info.metadata
        if isinstance(meta, _Since)
    ]


# The schema version current code writes into every snapshot.json,
# derived as the newest field's `_Since` so the persisted shape can't
# gain a field without the constant moving with it. Bump a field's
# `_Since` (or add a field with the next version) for any persisted-shape
# change so an older crony rejects a newer snapshot via the schema gate.
CURRENT_SNAPSHOT_SCHEMA: int = max(_model_field_versions())

# The oldest schema `parse` still reads forward (via `_migrate` and field
# defaults). Raise it -- and drop the matching `_migrate` step -- once no
# snapshots at the old version remain.
_COMPAT_FLOOR_SCHEMA: int = 4

# Every schema version current code can still load: the floor through
# CURRENT. A snapshot outside it is rejected as needing re-apply.
COMPAT_SNAPSHOT_SCHEMA: frozenset[int] = frozenset(
    range(_COMPAT_FLOOR_SCHEMA, CURRENT_SNAPSHOT_SCHEMA + 1)
)


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Translate a legacy snapshot shape into the current one before
    strict validation, so an old snapshot.json still loads. Additive and
    idempotent on a current-shape dict. Drop a step when its source
    version leaves COMPAT_SNAPSHOT_SCHEMA.

    v4: the per-entry deadline was a kind-specific key (`job_timeout_sec`
    / `group_budget_sec`); v5 unified them into `timeout`. Pre-dash
    snapshots keyed flags by the underscore spelling (`keep_awake`). Some
    older snapshots also persist a redundant `bundle` (recomputed from
    the full `name`)."""
    data = dict(raw)
    data.pop("bundle", None)
    legacy_timeout = data.pop("group_budget_sec", None)
    legacy_timeout = data.pop("job_timeout_sec", legacy_timeout)
    if "timeout" not in data and legacy_timeout is not None:
        data["timeout"] = legacy_timeout
    for flag in crony.config.JobFlags.members():
        underscore = flag.token.replace("-", "_")
        if underscore != flag.token and underscore in data:
            data.setdefault(flag.token, data.pop(underscore))
    return data


def parse(raw: dict[str, Any]) -> JobSnapshot | GroupSnapshot:
    """Migrate a legacy snapshot shape to the current one, then validate
    it into the typed model (dispatched on its `kind`). A wrong shape /
    unknown key / unknown kind raises ValueError (a pydantic
    ValidationError); a non-mapping top-level raises TypeError or
    ValueError (from `dict(raw)`). The `crony.model` loaders treat either
    as a broken snapshot."""
    return _SNAPSHOT_ADAPTER.validate_python(_migrate(raw))
