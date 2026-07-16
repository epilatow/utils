# This is AI generated code

"""crony's command handlers.

The do_* verbs behind the CLI -- apply / destroy / enable / disable /
trigger / status / logs / config / validate / notify-test -- plus the
name-resolution and apply-ordering helpers and the status renderer (its
column model, divergence and color handling, and per-column state
derivation). This is the in-process API a caller drives instead of
shelling out to the crony CLI; it orchestrates the lower layers
(config, model, runtime, notify, runner). The per-entry on-disk unit
lifecycle itself (apply_one / destroy_one) lives in crony.runtime.
"""

import argparse
import dataclasses
import datetime
import importlib.resources
import logging
import os
import re
import subprocess as subprocess  # noqa: PLC0414  re-exported for tests
import sys as sys  # noqa: PLC0414  re-exported for tests
import time as time  # noqa: PLC0414  re-exported for tests
import uuid
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

import tomlkit
import tomlkit.exceptions

import crony.config
import crony.errors
import crony.model
import crony.notify
import crony.paths
import crony.platform
import crony.runner
import crony.runtime
import crony.unit
from common.helpref import ReferenceSection, reference_section_text

logger = logging.getLogger(__name__)


# =============================================================================
# DEFAULT CONFIG TEMPLATE
# =============================================================================
# `crony config init` writes the starting bundle config from the
# shipped `default_config.toml` package-data file (every section
# commented out, so a user uncomments the bits they want without
# touching the explanatory prose). Read lazily so a packaging mishap
# surfaces on `config init` rather than at import.


def _default_config_template() -> str:
    """Contents of the shipped `default_config.toml`, the starting
    bundle config `crony config init` writes."""
    return (
        importlib.resources.files("crony")
        .joinpath("default_config.toml")
        .read_text(encoding="utf-8")
    )


def _schedule_display(timing: crony.unit.Timing | None) -> str:
    """Render a unit's timing into one status cell value.

    A Schedule shows its OnCalendar source; an Interval shows
    `interval=<spec>`. A trigger-only OnDemand entry displays as
    `on-demand`. An entry with no timing -- a transit group or a
    group-only job -- displays as `grouped`. This renders only the
    schedule shape; the caller overrides the cell with `disabled` for an
    operator-disabled entry (it has the snapshot's disable flag).
    """
    if isinstance(timing, crony.unit.Schedule):
        return str(timing)
    if isinstance(timing, crony.unit.Interval):
        return crony.model.ScheduleValue.INTERVAL.value.replace(
            "<x>", str(timing)
        )
    if isinstance(timing, crony.unit.OnDemand):
        return crony.model.ScheduleValue.ON_DEMAND.value
    return crony.model.ScheduleValue.GROUPED.value


def _timeout_display(
    node: crony.model.Job | crony.model.JobGroup | None,
) -> str | None:
    """The entry's wallclock cap as a status cell (`none` for the
    uncapped 0 sentinel, else `<n>s`): a job's job-timeout-sec or a
    group's cumulative budget. None for an absent node (a blank cell)."""
    if node is None:
        return None
    sec = node.timeout
    return "none" if sec == 0 else f"{sec}s"


def _priority_display(
    node: crony.model.Job | crony.model.JobGroup | None,
) -> str | None:
    """A job's scheduling priority as a status cell (`normal`, `high`,
    or `low`), or None for a group or absent node."""
    if not isinstance(node, crony.model.Job):
        return None
    return str(node.priority)


# Snapshot fields whose STALE-column label isn't a plain underscore-to-
# dash translation of the dataclass attribute. `timing` serializes as
# `schedule`/`interval`; `timeout` is the `job-timeout-sec` config knob;
# the interactive `_sec` snapshot fields store resolved seconds but the
# config knobs take a time-span string (`interactive-active` /
# `interactive-delay`). Any field not listed falls back to its
# dash-translated attribute name (so `snapshot_schema` reads
# `snapshot-schema`, `state_dir_symlink` reads `state-dir-symlink`).
_STALE_FIELD_LABELS: dict[str, str] = {
    "timing": "schedule",
    "timeout": "job-timeout-sec",
    "interactive_active_sec": "interactive-active",
    "interactive_delay_sec": "interactive-delay",
}


def _stale_fields(
    pending: crony.model.Job | crony.model.JobGroup | None,
    current: crony.model.Job | crony.model.JobGroup | None,
) -> str:
    """Comma-joined reasons an entry diverges from its applied version:
    the snapshot fields that differ between the pending and applied
    versions.

    Only `compare=True` fields are diffed, mirroring the dataclass `==`
    the stale verdict itself uses. A config knob is reported by the name
    the config file uses for it (see `_STALE_FIELD_LABELS`); any other
    field falls back to its dash-spelled attribute (`snapshot_schema` ->
    `snapshot-schema`, `state_dir_symlink` -> `state-dir-symlink`). The
    `flags` bitmask is expanded to the individual capability flags that
    changed (e.g. `keep-awake`), and `rendered_units` to the per-unit
    labels that drifted (`unit-config-1` / `unit-config-2`), rather than
    the field name. A dead schedule reports `unit-armed` -- so a `broken`
    dead-timer entry, whose content is otherwise synced, still says why it
    broke. Empty for a synced entry, or a verdict with no current snapshot
    to diff (a missing / unparseable remnant). A kind flip between job and
    group reports `kind`.
    """
    parts: list[str] = []
    if pending is not None and current is not None:
        if type(pending) is not type(current):
            parts.append("kind")
        else:
            for f in dataclasses.fields(pending):
                pv, cv = getattr(pending, f.name), getattr(current, f.name)
                if not f.compare or pv == cv:
                    continue
                if f.name == "flags":
                    parts.extend(
                        m.token
                        for m in crony.config.JobFlags.members()
                        if (m in pv) != (m in cv)
                    )
                elif f.name == "rendered_units":
                    parts.extend(
                        _UNIT_SLOT_LABELS[i]
                        for i in crony.model.rendered_drifted_indices(pv, cv)
                        if i < len(_UNIT_SLOT_LABELS)
                    )
                else:
                    parts.append(
                        _STALE_FIELD_LABELS.get(
                            f.name, f.name.replace("_", "-")
                        )
                    )
    return ",".join(sorted(parts))


# =============================================================================
# RUNTIME STATE (enable / disable / status)
# =============================================================================
# Runtime state is the operator-disable overlay crony records on the
# snapshot. It's orthogonal to CONFIG state: a unit can be `synced` with
# config while the operator has paused it (`disabled`, surfaced in
# status' SCHEDULE cell). apply preserves the disable across re-renders
# so a paused job stays off when the user runs `crony apply` to push
# other changes.


def _job_status(
    config: crony.model.Config, full_name: str
) -> crony.model.JobStatus:
    """The STATUS-column value (a `JobStatus`) for a stamped entity, read
    from `RuntimeState.job_status`.

    Resolution is current-first, pending-fallback. Runtime is uuid-keyed,
    and a rename keeps the uuid, so the run history lives at the same
    state dir under the new name. The current graph is keyed by the
    applied (old) name, so a not-yet-applied rename misses there; the
    pending graph carries the new name at the unchanged uuid and recovers
    the record. A genuinely new pending-only entry resolves to a uuid
    with no state dir, so `runtime.get` is None and it reports "never".
    """
    ref = config.resolve_current(full_name) or config.resolve_pending(full_name)
    if ref is None:
        return crony.model.JobStatus.NEVER
    rt = config.runtime.get(ref)
    return rt.job_status if rt is not None else crony.model.JobStatus.NEVER


def _format_elapsed(secs: int) -> str:
    """A compact magnitude label ("3s" / "5m" / "2h" / "8d") for a
    non-negative span of `secs` seconds, coarsened to its largest whole
    unit."""
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _last_ran_at(config: crony.model.Config, full_name: str) -> str:
    """Return a compact "when did this job last start" string for status
    (e.g. "5m ago"), from `RuntimeState.last_started_at`.

    Returns "never" when the job has never run, "unknown" when a record
    exists but yields no usable start, and "future" for a start ahead of
    now (clock skew). Resolution is current-first, pending-fallback:
    runtime is uuid-keyed, so a not-yet-applied rename (new name,
    unchanged uuid) is recovered via the pending graph when the
    applied-name lookup misses.
    """
    ref = config.resolve_current(full_name) or config.resolve_pending(full_name)
    if ref is None:
        return "never"
    rt = config.runtime.get(ref)
    if rt is None:
        return "never"
    if rt.last_started_at is None:
        # A record with an unreadable start is "unknown"; with no record
        # at all the job has never run.
        return "unknown" if rt.last_run is not None else "never"
    now = datetime.datetime.now(datetime.UTC).astimezone()
    secs = int((now - rt.last_started_at).total_seconds())
    if secs < 0:
        # Clock skew or a future-dated start; surface explicitly rather
        # than rendering "0s ago" which would mislead.
        return "future"
    return f"{_format_elapsed(secs)} ago"


def _cfg_status(
    config: crony.model.Config,
    full: str,
    entry: crony.config.TomlJob | crony.config.TomlJobGroup | None,
    bn: str,
) -> crony.model.ConfigStatus:
    """CONFIG-column value (synced / stale / broken / missing / orphan)
    for `full`, derived entirely from the in-memory `Config` --
    no filesystem or scheduler re-query. `error` is handled by
    the caller (it's name-based; an errored entry has no ref).

    The ref to score is the entry's own `<bundle>:<uuid>` when it
    is still in config, otherwise whatever the on-disk side
    recovered (current snapshot, broken snapshot, or unit-only
    orphan). `Config.cfg_status` reduces the pending and current
    graphs -- both built once at load -- to a single verdict. Each
    node carries its normalized config / timer units, so a hand-edited
    / missing / drifted unit already surfaces as `stale` (and a gone
    binary or unloaded unit as `broken`) through that node comparison;
    this function layers the FDA-wrapper and lingering-unit signals on
    top.
    """
    if entry is not None:
        ref: crony.unit.EntityRef | None = crony.unit.EntityRef(bn, entry.uuid)
    else:
        ref = config.resolve_current(full) or config.resolve_pending(full)
    if ref is None or ref not in config.all_refs():
        # Nothing the in-memory model knows by this ref: an
        # in-config-but-host-masked entry (absent from the pending
        # graph) or a bare name with no on-disk state. "missing"
        # is the pre-mask base the mask layer turns into "masked".
        return crony.model.ConfigStatus.MISSING
    state = config.cfg_status(ref)
    # A full-disk-access entry whose shared Crony.app wrapper can't serve
    # the grant -- not built, or built but ungranted (`is_missing`) --
    # can't run as configured. The live wrapper state rides on the
    # current node's compared `fda_wrapper` field, so an unrunnable
    # wrapper (which never equals the pending node's expected `OK`)
    # already made `state` stale; lift those cases to error. A
    # stale-but-runnable wrapper stays stale (reported `fda-wrapper`). An
    # entry that isn't applied (no current node) or already broken /
    # orphan / missing keeps its base verdict.
    if state == crony.model.ConfigStatus.STALE:
        current = config.current.job_from_ref(ref)
        if (
            isinstance(current, crony.model.Job)
            and current.fda_wrapper is not None
            and current.fda_wrapper.is_missing
        ):
            return crony.model.ConfigStatus.ERROR
    lingering = config.orphans_by_full_name.get(full)
    if (
        state == crony.model.ConfigStatus.MISSING
        and entry is not None
        and lingering is not None
        and not config.orphans[lingering].is_broken
    ):
        # In config and never cleanly applied (no current
        # snapshot) but a platform unit / alias lingers from a prior
        # apply whose state dir was wiped -- re-apply territory,
        # surfaced as drift rather than a clean "not applied." (A
        # broken-snapshot remnant under the name is `broken`, not this
        # case.)
        return crony.model.ConfigStatus.STALE
    return state


# =============================================================================
# COMMAND HANDLERS
# =============================================================================
# Each handler's signature must match its argparse subparser's argument
# `dest` names exactly; the shared CmdCallbacksBase test enforces this.


def do_init(force: bool, bundle: str | None) -> None:
    """Generate a default config file.

    With `--bundle <name>`, writes `config/<name>.toml` (creating
    the dropin dir if missing). Otherwise writes `config.toml`.
    """
    template = _default_config_template()
    if bundle is not None:
        try:
            crony.config.validate_bundle_name(bundle, "--bundle")
        except crony.errors.ConfigError as e:
            raise crony.errors.UsageError(str(e)) from e
        target = crony.paths.CONFIG_DROPIN_DIR / f"{bundle}.toml"
        if target.exists() and not force:
            raise crony.errors.UsageError(
                f"{target} already exists; pass --force to overwrite"
            )
        crony.paths.CONFIG_DROPIN_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text(template, encoding="utf-8")
        logger.info("wrote bundle config to %s", target)
        return
    if crony.paths.CONFIG_FILE.exists() and not force:
        raise crony.errors.UsageError(
            f"{crony.paths.CONFIG_FILE} already exists; "
            "pass --force to overwrite"
        )
    crony.paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    crony.paths.CONFIG_FILE.write_text(template, encoding="utf-8")
    logger.info("wrote default config to %s", crony.paths.CONFIG_FILE)


def do_generate_uuid() -> None:
    """Print one freshly-minted UUID to stdout.

    Convenience for users hand-editing a config: when adding a new
    job or group, pipe the output into the editor instead of
    looking up the canonical format separately. `crony config update`
    does the same insertion automatically for missing UUIDs, but
    that path can't help when the user wants to seed a new entry
    before the file is otherwise valid.
    """
    print(uuid.uuid4())


def _bundle_files_for_update(
    bundle: str | None,
) -> list[tuple[str, Path]]:
    """Enumerate the (bundle_name, path) pairs that `config update`
    should scan, honoring `--bundle` scoping.

    Mirrors `TomlConfig.load_all` so the same files reachable to
    apply are reachable to update. Returns paths even when the file is
    syntactically broken -- the caller surfaces parse failures
    per-file rather than aborting the whole pass.
    """
    candidates: list[tuple[str, Path]] = []
    if crony.paths.CONFIG_FILE.exists():
        candidates.append(
            (crony.config.DEFAULT_BUNDLE_NAME, crony.paths.CONFIG_FILE)
        )
    if crony.paths.CONFIG_DROPIN_DIR.exists():
        for path in sorted(crony.paths.CONFIG_DROPIN_DIR.glob("*.toml")):
            candidates.append((path.stem, path))
    if bundle is not None:
        candidates = [
            (name, path) for (name, path) in candidates if name == bundle
        ]
    return candidates


def _insert_missing_uuids_in_section(
    doc: tomlkit.TOMLDocument, section: str
) -> int:
    """Assign a fresh UUID to every subtable of `[<section>.*]`
    that lacks one. Returns the count of insertions made.

    Tables that already have a `uuid` key are left untouched; we
    don't re-canonicalize or re-roll, since the assigned UUID is
    the durable identity.
    """
    added = 0
    parent = doc.get(section)
    if parent is None:
        return 0
    if not isinstance(parent, tomlkit.items.Table):
        return 0
    for _short, subtable in parent.items():
        if not isinstance(subtable, tomlkit.items.Table):
            continue
        if "uuid" in subtable:
            continue
        subtable["uuid"] = str(uuid.uuid4())
        added += 1
    return added


def do_config_update(bundle: str | None) -> None:
    """Assign UUIDs to every `[job.*]` and `[job-group.*]` that
    lacks one, rewriting the bundle file in place.

    tomlkit preserves comments, whitespace, and key order, so a
    user-curated config keeps its layout after the update. Files
    that already have all UUIDs are left untouched (no rewrite,
    no logged change). Per-file parse failures are reported and
    skipped so one broken bundle doesn't block updates to other
    bundles.
    """
    if bundle is not None:
        try:
            crony.config.validate_bundle_name(bundle, "--bundle")
        except crony.errors.ConfigError as e:
            raise crony.errors.UsageError(str(e)) from e
    files = _bundle_files_for_update(bundle)
    if not files:
        if bundle is None:
            raise crony.errors.ConfigError(
                f"no config: expected {crony.paths.CONFIG_FILE} or "
                f"{crony.paths.CONFIG_DROPIN_DIR}/*.toml"
            )
        raise crony.errors.UsageError(
            f"no config file for bundle {bundle!r} "
            f"(expected {crony.paths.CONFIG_DROPIN_DIR}/{bundle}.toml)"
        )
    for bundle_name, path in files:
        try:
            doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        except tomlkit.exceptions.ParseError as e:
            logger.error("%s: TOML parse error: %s", path, e)
            continue
        added_jobs = _insert_missing_uuids_in_section(doc, "job")
        added_groups = _insert_missing_uuids_in_section(doc, "job-group")
        total = added_jobs + added_groups
        if total == 0:
            logger.info(
                "%s (bundle %s): no missing uuids",
                path,
                bundle_name,
            )
            continue
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        logger.info(
            "%s (bundle %s): added %d uuid%s",
            path,
            bundle_name,
            total,
            "" if total == 1 else "s",
        )


def _errored_full_names(
    bundles: crony.config.TomlConfig, bundle: str | None
) -> set[str]:
    """Full-namespaced names of entries whose bundle config rejected them.

    Walks every bundle (or just `bundle` when scoped). Errored
    entries are pulled from each bundle's `errored_jobs` and
    `errored_job_groups` maps -- they were never selected by any
    target, so the regular selection / mask walks don't surface
    them.
    """
    out: set[str] = set()
    for b in bundles.bundles:
        if bundle is not None and b.name != bundle:
            continue
        for short in b.config.errored_jobs:
            out.add(b.full_name(short))
        for short in b.config.errored_job_groups:
            out.add(b.full_name(short))
    return out


def _selected_full_names_per_bundle(
    bundles: crony.config.TomlConfig,
) -> tuple[dict[str, tuple[crony.config.TomlBundle, str]], set[str]]:
    """Return (full_name -> (bundle, short)) and full_names set,
    spanning every bundle's selection on the current host.

    `by_full`'s key set is the same set as `selected`; this is the
    contract `do_apply` / `_expand_apply_subtree` rely on to
    distinguish "names known to this host's selection" from
    "everything else".  Callers that also need to inspect masked
    entries use `_selected_and_masked_full_names_per_bundle`.
    """
    by_full: dict[str, tuple[crony.config.TomlBundle, str]] = {}
    for b in bundles.bundles:
        target = b.config.resolve_target()
        sel_jobs, sel_groups = b.config.selected_jobs_and_groups(target)
        for short in sel_jobs | sel_groups:
            by_full[b.full_name(short)] = (b, short)
    return by_full, set(by_full.keys())


def _selected_and_masked_full_names_per_bundle(
    bundles: crony.config.TomlConfig,
) -> tuple[
    dict[str, tuple[crony.config.TomlBundle, str]], set[str], dict[str, str]
]:
    """Spans every bundle, returning selected and masked names.

    `by_full` maps full_name -> (bundle, short) for every entry
    that is selected OR masked OR defined-but-unused on this host
    -- the wider set is needed only by status callers that surface
    masked rows. `selected` is the set of full_names that pass the
    host's `platforms` / `hosts` filters. `masked_by_full` maps
    everything else: entries reached through `target.jobs` but
    excluded by per-entry filters get the axis reason from
    `crony.config._mask_reason` (`host`, `platform`, ...); entries defined in
    a bundle's config but not reached by the host's resolved
    target get the literal reason `unused`.
    """
    by_full: dict[str, tuple[crony.config.TomlBundle, str]] = {}
    selected: set[str] = set()
    masked_by_full: dict[str, str] = {}
    for b in bundles.bundles:
        target = b.config.resolve_target()
        sel_jobs, sel_groups, masked = (
            b.config.selected_and_masked_jobs_and_groups(target)
        )
        for short in sel_jobs | sel_groups:
            full = b.full_name(short)
            by_full[full] = (b, short)
            selected.add(full)
        for short, reason in masked.items():
            full = b.full_name(short)
            by_full[full] = (b, short)
            masked_by_full[full] = reason
        defined = (
            set(b.config.jobs)
            | set(b.config.job_groups)
            | set(b.config.errored_jobs)
            | set(b.config.errored_job_groups)
        )
        accounted = sel_jobs | sel_groups | set(masked.keys())
        for short in defined - accounted:
            full = b.full_name(short)
            by_full[full] = (b, short)
            # Errored entries get the same `unused` label as
            # genuinely-unused ones at this layer; `_resolve_states`
            # promotes them back to `error` so the user sees the
            # actual problem instead of a generic mask.
            masked_by_full[full] = crony.config.MaskReason.UNUSED.value
    return by_full, selected, masked_by_full


def _expand_apply_subtree(
    by_full: dict[str, tuple[crony.config.TomlBundle, str]],
    full_names: list[str],
) -> list[str]:
    """Expand each name to include its transitive group children.

    `crony apply <group>` cascades to the group's children so the
    group's snapshot reflects each child's freshly-applied state.
    Children outside the current host's selection are silently
    skipped (they shouldn't have units installed here).
    """
    seen: set[str] = set()

    def walk(full: str) -> None:
        if full in seen or full not in by_full:
            return
        seen.add(full)
        bundle, short = by_full[full]
        group = bundle.config.job_groups.get(short)
        if group is None:
            return
        for child_short in group.jobs:
            walk(bundle.full_name(child_short))

    for f in full_names:
        walk(f)
    return sorted(seen)


def _topo_apply_order(
    by_full: dict[str, tuple[crony.config.TomlBundle, str]],
    full_names: list[str],
) -> list[str]:
    """Order names so each group's children are applied first.

    Groups depend on their children (the group's snapshot pulls
    its cumulative `timeout` budget from the children's resolved
    timeouts via the live config; ordering leaves-first keeps both
    pinned and in-progress state consistent within the same apply
    pass).

    `crony.config._validate_config` rejects cycles, so a real cycle is
    defensive: if the in-degree never drops to zero, fall back to
    input order (the apply itself will fail loudly on whatever
    inconsistency the cycle implies).
    """
    pool = set(full_names)
    indeg: dict[str, int] = {n: 0 for n in pool}
    parents: dict[str, list[str]] = {n: [] for n in pool}
    for full in pool:
        bundle, short = by_full[full]
        group = bundle.config.job_groups.get(short)
        if group is None:
            continue
        for child_short in group.jobs:
            child_full = bundle.full_name(child_short)
            if child_full in pool:
                indeg[full] += 1
                parents[child_full].append(full)
    queue = sorted(n for n, d in indeg.items() if d == 0)
    out: list[str] = []
    while queue:
        n = queue.pop(0)
        out.append(n)
        for parent in sorted(parents[n]):
            indeg[parent] -= 1
            if indeg[parent] == 0:
                queue.append(parent)
    if len(out) != len(pool):
        return list(full_names)
    return out


def _reclaim_entity(
    config: crony.model.Config,
    entity: crony.model.Job | crony.model.JobGroup | crony.model.JobOrphan,
    *,
    raise_on_lock: bool,
) -> bool:
    """Remove one on-disk entity: a full removal (platform unit, state
    dir, alias), or state-dir-only for a shadowed collision loser -- a
    *different* current entry is the live winner of the name and owns
    the name-keyed unit / alias. Returns True when it acted, False when
    a run in progress left it in place. With `raise_on_lock` a held
    run.lock raises (a targeted `destroy`); otherwise it warns and
    skips (a bulk sweep / apply reconcile shouldn't abort on one busy
    entry).
    """
    sd = entity.state_dir
    on_disk = sd if sd.is_dir() else None
    label = entity.full_name or str(entity.entity_ref)
    if on_disk is not None and crony.runtime.run_in_progress(on_disk):
        if raise_on_lock:
            raise crony.errors.LockBusyError(
                f"{label}: run in progress; will not destroy"
            )
        logger.warning("%s: left in place (run in progress)", label)
        return False
    if entity.entity_ref in config.shadowed:
        crony.runtime.destroy_one(None, on_disk, None)
    else:
        crony.runtime.destroy_one(
            entity.full_name, on_disk, entity.state_dir_symlink_path
        )
    return True


def _reclaim(
    config: crony.model.Config, bundle: str | None, *, only_unselected: bool
) -> None:
    """Reclaim on-disk entities the live config no longer wants. With
    `only_unselected` (apply's full sync, `destroy --orphans`) that is
    every current node or orphan whose ref is not in pending; otherwise
    (a factory reset) every on-disk entity. The single source of
    "which entities are orphans," shared so apply and destroy agree.
    """
    live = config.pending.refs()
    on_disk: list[
        crony.model.Job | crony.model.JobGroup | crony.model.JobOrphan
    ] = [
        *config.current.nodes(),
        *config.orphans.values(),
    ]
    targets = sorted(
        (
            e
            for e in on_disk
            if (not only_unselected or e.entity_ref not in live)
            and (bundle is None or e.bundle == bundle)
        ),
        key=lambda e: (e.bundle, e.uuid),
    )
    for e in targets:
        if _reclaim_entity(config, e, raise_on_lock=False):
            logger.info("%s: removed", e.full_name or str(e.entity_ref))


def do_apply(jobs: list[str], verbose: bool, bundle: str | None) -> None:
    """Render and activate platform units to match config.

    No args: full sync across every bundle. Install missing, fix
    drift, and reconcile by identity -- every on-disk entity
    (current snapshot, broken snapshot, or unit-only orphan) whose
    ref the live config no longer selects is removed, including
    superseded uuid-edit residue. With names: surgical update of
    those entries only -- unrelated orphans are left alone so a
    one-off apply doesn't have side effects, but each applied
    entry's own superseded same-name residue (from a uuid edit) is
    still reclaimed. An orphan with a run in progress is left in
    place and reclaimed on a later apply.

    Names on the CLI are full namespaced names
    (`<bundle>.<short>`). Bare input is shorthand for
    `default.<short>` and only ever resolves to the default bundle.

    With `--bundle <name>`, scopes the apply to that bundle: bare
    CLI input resolves there instead of `default`, qualified names
    must match `<name>`, and a no-args sync only touches that
    bundle's selected jobs and its own orphans -- other bundles'
    state is untouched.

    `unchanged` results are suppressed by default so the output
    only shows the entries the apply actually touched. `-v` /
    `--verbose` prints them too.
    """
    # One in-memory model for the whole command: selection comes
    # from the parsed bundles, drift / orphan reconciliation from
    # the current + broken + unit-only graphs. apply_one mutates
    # disk per entry, but the orphan plan is derived from this one
    # load -- no second `load_config()` after the apply loop.
    config = crony.runtime.load_config()
    bundles = config.toml_config
    bundles.require_known(bundle)
    # Apply needs pending-side data for every entry it considers
    # selected; if some bundles failed to parse, the unscoped
    # orphan-removal sweep would treat every stamped name as
    # un-selected and wipe it. Refuse only the unscoped full-sync
    # sweep when bundles are errored; the operator must either fix
    # the config or narrow scope with explicit names / `--bundle`
    # so the wipe scope is intentional. A `--bundle` sweep is
    # already safe: its orphan removal is scoped to that one bundle
    # (which `require_known` has confirmed parsed cleanly),
    # so an unrelated broken bundle can't derail it.
    if not jobs and bundle is None and bundles.errored_bundles:
        affected = sorted(bundles.errored_bundles)
        raise crony.errors.UsageError(
            "refusing the full-sync apply: one or more config "
            f"files failed to parse ({affected}). Fix the config "
            "or pass explicit job names / `--bundle <name>` so "
            "the orphan-removal scope is intentional."
        )
    by_full, selected = _selected_full_names_per_bundle(bundles)
    if bundle is not None:
        selected = crony.config.bundle_prefix_filter(selected, bundle)

    if jobs:
        normalized = [
            crony.config.resolve_cli_name(arg, bundle) for arg in jobs
        ]
        errored = _errored_full_names(bundles, bundle)
        errored_in_args = sorted(n for n in normalized if n in errored)
        if errored_in_args:
            # An errored entry has no parsed TomlBundleConfig fields, so we
            # can't render its plist / unit. Bail before any
            # partial apply happens.
            raise crony.errors.UsageError(
                f"config error -- fix and re-run apply: {errored_in_args}"
            )
        unknown = [n for n in normalized if n not in by_full]
        if bundle is not None:
            # Under `-b`, a name that exists in `by_full` but is
            # outside the scoped `selected` set means the entry
            # belongs to that bundle but isn't selected on this
            # host; surface it the same as unknown.
            unknown.extend(
                n
                for n in normalized
                if n in by_full and n not in selected and n not in unknown
            )
        if unknown:
            raise crony.errors.UsageError(
                f"unknown or unselected on this host: {sorted(unknown)}"
            )
        # Cascade `crony apply <group>` to the group's transitive
        # children so the group's snapshot reflects each child's
        # freshly-applied state.
        full_names_to_apply = _expand_apply_subtree(by_full, normalized)
        remove_orphans = False
    else:
        full_names_to_apply = sorted(selected)
        remove_orphans = True

    # Topological sort: leaves first, so each group's snapshot is
    # computed against children whose own snapshots have already
    # landed in this apply pass.
    full_names_to_apply = _topo_apply_order(by_full, full_names_to_apply)

    # Clean-first reconcile: before installing the pending units, drop
    # whatever the config no longer selects so the apply lays down a
    # clean replacement. A full sync reclaims every unselected on-disk
    # entity (identical to `destroy --orphans`); a targeted apply
    # supersedes only the old uuid of each name it installs. A uuid
    # change is delete-old + create-new -- we carry no state across it,
    # the shared name is incidental -- so the old job is removed in
    # full and the pending job is built fresh.
    if remove_orphans:
        _reclaim(config, bundle, only_unselected=True)
    else:
        for full in full_names_to_apply:
            pending_ref = config.pending.by_full_name.get(full)
            current_ref = config.current.by_full_name.get(full)
            if current_ref is None or current_ref == pending_ref:
                continue
            old = config.current.job_from_ref(current_ref)
            if old is not None and _reclaim_entity(
                config, old, raise_on_lock=False
            ):
                logger.info("%s: superseded uuid removed", full)

    deferred = False
    _ensure_fda_wrapper(config, full_names_to_apply)

    for full in full_names_to_apply:
        ref = config.pending.by_full_name[full]
        result = crony.runtime.apply_one(config, ref)
        if result == crony.runtime.ApplyResult.DEFERRED:
            # apply_one already logged the why at warning level.
            deferred = True
        if verbose or result != crony.runtime.ApplyResult.UNCHANGED:
            logger.info("%s: %s", full, result)
    # A deferred apply leaves the entry's on-disk unit and snapshot stale
    # relative to config (they stay mutually consistent at the old state)
    # until a later apply reconciles them; exit WARNING so an operator (or
    # a wrapping script) sees the apply was not fully carried out.
    if deferred:
        raise SystemExit(int(crony.errors.ExitCode.WARNING))


def _ensure_fda_wrapper(
    config: crony.model.Config, full_names_to_apply: list[str]
) -> None:
    """Build the Full Disk Access wrapper when any entry being applied
    needs it, logging any grant / toolchain warning.

    Building is the only point the wrapper is (re)compiled -- a run uses
    whatever binary is already present (rebuilding changes its cdhash and
    would void the grant). The host backend decides what the work is: off
    darwin it is a no-op.
    """
    fda_flag = crony.config.JobFlags.FULL_DISK_ACCESS
    needs_fda = any(
        (job := config.pending.job_from_ref(config.pending.by_full_name[full]))
        is not None
        and fda_flag in job.flags
        for full in full_names_to_apply
    )
    if not needs_fda:
        return
    warning = crony.runtime.host().prepare_full_disk_access()
    if warning is not None:
        logger.warning("%s", warning)


def do_destroy(
    jobs: list[str],
    bundle: str | None,
    orphans: bool,
) -> None:
    """Remove platform units. Always a full wipe -- the platform
    unit files and the entry's state dir both go away.

    With `--all`: factory reset -- every crony-managed remnant on
    this host. Discovery covers state dirs plus platform unit files.

    With `--all` plus `--bundle <name>`: scope the reset to that
    bundle's discovered names. Other bundles' remnants stay intact.

    With `--orphans`: limit removal to entries with on-disk
    remnants that no bundle's live config selects to install on
    this host. Combinable with `--bundle` (orphans within that
    bundle namespace). Mutually exclusive with positional names and
    `--all`.

    With names: surgical removal by full namespaced name. Refuses
    if a name is in neither any bundle's config nor the
    discovered set; refuses if a name's run.lock is currently
    held. Under `--bundle`, bare names resolve in that bundle and
    qualified names must match it. A rename (same uuid, new config
    name) is removable by either name -- resolution is by uuid, so
    the installed unit and the shared state dir are cleaned up; a
    name mapping to different uuids in config vs on disk is rejected
    until `crony apply`.

    Also accepts the `<bundle>:<UUID>` input form so an operator can
    copy the JOB cell from a status row for an entity with no
    recoverable name (a broken snapshot or a snapshot-less leftover
    dir) and paste it here. The input validates iff it names a
    modeled on-disk entity -- a current node or an orphan. The
    entity's own name keys the platform-unit cleanup; a too-corrupt
    orphan carries none, so only its state dir is wiped.
    """
    config = crony.runtime.load_config()
    bundles = config.toml_config
    config.require_addressable(bundle)
    if jobs:
        by_full, _ = _selected_full_names_per_bundle(bundles)
        # Defined names span every bundle's full names plus any
        # name with an on-disk remnant (so a leftover state dir or
        # unit can still be destroyed after the bundle is gone).
        defined = set(by_full.keys()) | bundles.all_full_names()
        known = defined | config.installed_full_names()
        if bundle is not None:
            known = crony.config.bundle_prefix_filter(known, bundle)
        normalized = [
            crony.config.resolve_cli_name(arg, bundle) for arg in jobs
        ]
        unknown = []
        for n in normalized:
            if n in known:
                continue
            # A `<bundle>:<UUID>` paste is known iff it names a modeled
            # on-disk entity -- a current node or an orphan (the only
            # refs `crony status` renders for pasting back).
            syn = crony.unit.EntityRef.from_str(n)
            if syn is not None and (
                config.current.job_from_ref(syn) is not None
                or syn in config.orphans
            ):
                continue
            unknown.append(n)
        if unknown:
            raise crony.errors.UsageError(f"unknown name(s): {sorted(unknown)}")
        for full in normalized:
            # Resolve to the on-disk entity to wipe -- current node or
            # orphan, never pending (a not-yet-applied rename still
            # addresses the installed uuid; a name-swap raises). A name
            # with no on-disk state but a stray same-name unit falls
            # back to a name-keyed unit sweep. A held run.lock raises.
            ref = _resolve_addressable(config, full)
            entity = (
                config.current.job_from_ref(ref) or config.orphans.get(ref)
                if ref is not None
                else None
            )
            if entity is None:
                crony.runtime.destroy_one(full, None, None)
            else:
                _reclaim_entity(config, entity, raise_on_lock=True)
            logger.info("%s: destroyed", full)
    else:
        # `--all` factory-resets every crony-managed remnant; `--orphans`
        # limits it to entities the live config no longer selects. Both
        # go through the one reclamation `apply` shares.
        _reclaim(config, bundle, only_unselected=orphans)


def _installed_refs(config: crony.model.Config) -> set[crony.unit.EntityRef]:
    """The uuids with on-disk presence (a current snapshot, a broken
    snapshot, or a leftover platform unit) -- the set an action
    command can act on. Excludes pending-only entries (never applied).
    """
    return config.current.refs() | set(config.orphans)


def _resolve_addressable(
    config: crony.model.Config, full: str
) -> crony.unit.EntityRef | None:
    """Resolve a user-supplied name to the single uuid it addresses
    for an action command (enable / disable / trigger / destroy).

    A rename keeps the uuid, so a not-yet-applied new name resolves to
    the same uuid as its installed old name; either is accepted.
    Raises `UsageError` when the name maps to different uuids in the
    pending and current graphs -- an unreconciled name swap that can't
    be disambiguated until `crony apply`. Returns None when neither
    graph knows the name (and it isn't a `<bundle>:<UUID>` address).
    """
    direct = crony.unit.EntityRef.from_str(full)
    if direct is not None:
        return direct
    pending_ref = config.resolve_pending(full)
    current_ref = config.resolve_current(full)
    if (
        pending_ref is not None
        and current_ref is not None
        and pending_ref != current_ref
    ):
        raise crony.errors.UsageError(
            f"{full!r} addresses {current_ref} on disk but {pending_ref} "
            f"in config; run `crony apply` to reconcile before addressing "
            f"it by name"
        )
    return current_ref or pending_ref


def _resolve_action_targets(
    config: crony.model.Config, names: list[str], *, runnable_only: bool = False
) -> list[tuple[str, crony.unit.EntityRef, str]]:
    """Map normalized names to `(input, ref, unit-name)` targets for an
    action command, rejecting any that can't be acted on here.

    Accepts a renamed entry by its new name (the uuid is stable) and
    errors on an ambiguous name swap via `_resolve_addressable`. The
    unit name is the entity's applied (current) name -- the label the
    installed platform unit actually carries -- so the action targets
    the unit that exists, while the uuid pins the state dir.

    `runnable_only` narrows the acceptable set to entities with a
    parseable current snapshot (`current.refs()`) -- the gate `trigger`
    needs, since firing a broken or unit-only entry would launch a unit
    whose snapshot can't load. A rename's uuid is still in `current`
    (under its old-name snapshot), so renames pass either way. Without
    it, the set is every on-disk remnant (`_installed_refs`), matching
    enable / disable, which act on the platform unit by name.
    """
    acceptable = (
        config.current.refs() if runnable_only else _installed_refs(config)
    )
    targets: list[tuple[str, crony.unit.EntityRef, str]] = []
    unknown: list[str] = []
    for full in names:
        ref = _resolve_addressable(config, full)
        if ref is None or ref not in acceptable:
            unknown.append(full)
            continue
        targets.append((full, ref, config.name_for(ref) or full))
    if unknown:
        hint = (
            "not runnable here (apply may be stale)"
            if runnable_only
            else "not stamped on this host"
        )
        raise crony.errors.UsageError(
            f"{hint}: {sorted(unknown)} (run `crony apply` first)"
        )
    return targets


def _resolve_bulk_names(
    jobs: list[str],
    bundle: str | None,
    stamped: set[str],
) -> list[str]:
    """Derive the operate-on name set for enable / disable / trigger.

    With positional `jobs`, normalize each via `resolve_cli_name`
    (which honors `-b`'s bundle-scoping rules). With no positionals
    (the `--all` path the parser requires), the set is every stamped
    name -- every kind (scheduled, grouped, group container), since a
    disable / enable / trigger applies to all of them -- narrowed to
    `bundle` when one is given.
    """
    if jobs:
        return [crony.config.resolve_cli_name(arg, bundle) for arg in jobs]
    if bundle is not None:
        return sorted(crony.config.bundle_prefix_filter(stamped, bundle))
    return sorted(stamped)


def do_enable(jobs: list[str], bundle: str | None) -> None:
    """Re-arm the named jobs' schedules (clear the operator-disable).

    Re-renders each installed unit with its pinned schedule and reloads
    it, then clears `unit_disabled` on its snapshot. Names are full
    namespaced (`<bundle>.<short>`); bare input is shorthand for
    `default.<short>`. A rename (same uuid, new config name) is
    addressable by either name; a name mapping to different uuids in
    config vs on disk is rejected until `crony apply`. Refuses names not
    stamped on this host.

    A grouped entry (no schedule of its own) can be enabled / disabled
    too: its `unit_disabled` flag gates whether the parent group's
    dispatch skips it, so the operator-disable is meaningful even with
    no timer to disarm.

    With `--all`, enables every stamped entry, narrowed to one bundle
    when `--bundle <name>` is also given. With `--bundle` plus
    positional args, bare names resolve in `<name>` and qualified
    names must match it.
    """
    config = crony.runtime.load_config()
    config.require_addressable(bundle)
    installed = config.installed_full_names()
    normalized = _resolve_bulk_names(jobs, bundle, installed)
    targets = _resolve_action_targets(config, normalized)
    for full, ref, _unit_name in targets:
        crony.runtime.set_disabled(config, ref, disabled=False)
        logger.info("%s: enabled", full)


def do_disable(jobs: list[str], bundle: str | None) -> None:
    """Disarm the named jobs' schedules (operator-disable).

    Re-renders each installed unit with no schedule -- loaded and
    triggerable, but not firing on its own -- and records `unit_disabled`
    on its snapshot so a later `crony apply` preserves it. Names are full
    namespaced (`<bundle>.<short>`); bare input is shorthand for
    `default.<short>`. A rename (same uuid, new config name) is
    addressable by either name; a name mapping to different uuids in
    config vs on disk is rejected until `crony apply`. Refuses names not
    stamped on this host.

    A grouped entry (no schedule of its own) can be disabled too: with
    no timer to disarm, its `unit_disabled` flag instead makes the
    parent group's dispatch skip it, so the child stays installed but
    no longer runs as part of the group.

    With `--all`, disables every stamped entry, narrowed to one bundle
    when `--bundle <name>` is also given. With `--bundle` plus
    positional args, bare names resolve in `<name>` and qualified
    names must match it.
    """
    config = crony.runtime.load_config()
    config.require_addressable(bundle)
    installed = config.installed_full_names()
    normalized = _resolve_bulk_names(jobs, bundle, installed)
    targets = _resolve_action_targets(config, normalized)
    for full, ref, _unit_name in targets:
        crony.runtime.set_disabled(config, ref, disabled=True)
        logger.info("%s: disabled", full)


def do_trigger(
    jobs: list[str],
    wait: bool,
    trigger_timeout: int | None,
    bundle: str | None,
) -> None:
    """Ask the platform scheduler to fire the named jobs.

    `trigger` exercises the same platform-scheduler path a
    scheduled fire would, so the run uses the exact execution
    context (env, working dir, signal handling) the next
    scheduled invocation would use. This is the user-facing way
    to do an immediate run; the underlying `run` subcommand is
    the platform unit's entry point and not user-facing. Works
    on every stamped entry, including transit groups and group-
    only jobs (every entry installs a platform unit; schedule-
    less entries' units just sit dormant until kickstarted).

    Default mode is async (the scheduler fires and returns): the
    trigger returns as soon as the platform has accepted it. With
    `--wait`, blocks until each named entry's next completion and exits
    with that exit code (the worst exit code across multiple names if
    more than one).

    `--trigger-timeout <sec>` (only with `--wait`) overrides the
    default 15s "trigger never produced a run" deadline.

    Names are full namespaced (`<bundle>.<short>`); bare input is
    shorthand for `default.<short>`. A rename (same uuid, new config
    name) is addressable by either name; a name mapping to different
    uuids in config vs on disk is rejected until `crony apply`.
    Refuses names not stamped on this host (run apply first).

    With `--all`, triggers every stamped entry (including schedule-less
    ones, since trigger is meaningful for them too), narrowed to one
    bundle when `--bundle <name>` is also given. With `--bundle` plus
    positional args, bare names resolve in `<name>` and qualified names
    must match it.
    """
    config = crony.runtime.load_config()
    bundles = config.toml_config
    config.require_addressable(bundle)
    stamped = config.installed_full_names()
    normalized = _resolve_bulk_names(jobs, bundle, stamped)
    targets = _resolve_action_targets(config, normalized, runnable_only=True)

    if not wait:
        for full, ref, unit_name in targets:
            # Fire the unit under its installed (current) name; the
            # user-trigger flag lands in the uuid-keyed state dir.
            crony.runner.trigger_unit(
                unit_name,
                triggered_by_user=True,
                state_dir=crony.model.Job.state_dir_from_ref(ref),
            )
            logger.info("%s: triggered", full)
        return

    # Synchronous waiter mode. Resolve per-name timeouts via each
    # name's bundle; bundles can disagree about defaults so we look
    # up trigger_timeout_sec per-bundle too.
    worst_rc = 0
    for full, ref, unit_name in targets:
        bn, short = crony.config.parse_full_name(full)
        b = bundles.by_name(bn)
        if b is None:
            raise crony.errors.UsageError(
                f"unknown bundle for {full!r} (apply may be stale)"
            )
        if short not in b.config.jobs and short not in b.config.job_groups:
            # Installed on disk but no longer in the config (an
            # orphan). A plain `trigger` still fires it, but --wait
            # resolves per-name timeouts from the config, which no
            # longer describes it -- refuse with a clear message
            # rather than a raw KeyError.
            raise crony.errors.UsageError(
                f"{full!r} is installed but not in the current config "
                f"(apply may be stale -- re-apply or `crony destroy` "
                f"it); --wait cannot resolve its timeouts"
            )
        b_target = b.config.resolve_target()
        if short in b.config.jobs:
            timeout = crony.runner.timeout_to_wait(
                b.config.resolved_job_timeout_sec(b.config.jobs[short])
            )
        else:
            timeout = crony.runner.timeout_to_wait(
                b.config.resolved_group_timeout_sec(b_target, short)
            )
        tt = (
            float(trigger_timeout)
            if trigger_timeout is not None
            else float(b.config.defaults.trigger_timeout_sec)
        )
        rec = crony.runner.trigger_unit_sync(
            unit_name,
            state_dir=crony.model.Job.state_dir_from_ref(ref),
            job_timeout=timeout,
            trigger_timeout=tt,
            triggered_by_user=True,
        )
        cls = rec.get("exit_class", "ok")
        rc = crony.runner.trigger_exit_code(rec)
        logger.info("%s: %s (exit %s)", full, cls, rc)
        if rc and (not worst_rc or abs(rc) > abs(worst_rc)):
            worst_rc = rc
    if worst_rc:
        raise SystemExit(worst_rc)


def _build_status_tree(
    bundles: crony.config.TomlConfig, host: str, platform: str
) -> tuple[list[str], dict[str, int]]:
    """Return the DFS order and per-name depth of each active target tree.

    For each bundle, resolves the target that activates on
    (host, platform) and walks `target.jobs` -> group.jobs ->
    leaves in list order, preserving the order each parent
    declared its children. Both selected and masked entries are
    recorded so the renderer can place a masked-but-in-tree row
    at its config depth instead of below the tree. The per-walk
    visited-set bounds the walk if a malformed config somehow
    bypasses `crony.config._validate_config` (e.g. tests building
    TomlBundleConfig directly without going through
    `crony.config.TomlBundleConfig._from_raw`); under the single-parent
    invariant from validation it never deduplicates a real traversal.
    """
    order: list[str] = []
    depth: dict[str, int] = {}

    def _walk(
        bundle: crony.config.TomlBundle,
        short: str,
        d: int,
        seen: set[str],
    ) -> None:
        full = bundle.full_name(short)
        if full in seen:
            return
        seen.add(full)
        order.append(full)
        depth[full] = d
        g = bundle.config.job_groups.get(short)
        if g is None:
            return
        for child in g.jobs:
            _walk(bundle, child, d + 1, seen)

    for b in bundles.bundles:
        target = b.config.resolve_target(host, platform)
        if target is None:
            continue
        seen: set[str] = set()
        for ref in target.jobs:
            _walk(b, ref, 0, seen)
    return order, depth


class _StatusCols(StrEnum):
    """The selectable `crony status` column names, in `--cols all`
    display order. This is the authoritative column set: a column has to
    appear here to be documented (`_STATUS_COLUMNS`), rendered
    (`row_cells`), or named in an alias's expansion. The per-flag
    columns are not members -- they are a dynamic family keyed by each
    `JobFlags` token (the `<flag>` doc entry covers them)."""

    JOB = "job"
    JOB_OR_UUID = "job-or-uuid"
    KIND = "kind"
    CONFIG = "config"
    SCHEDULE = "schedule"
    GROUPS = "groups"
    STATUS = "status"
    LAST_RAN = "last-ran"
    MASKED_BY = "masked-by"
    UNIT_NAME = "unit-name"
    UUID = "uuid"
    UNIT_CONFIG_1 = "unit-config-1"
    UNIT_CONFIG_2 = "unit-config-2"
    LOG_FILE = "log-file"
    FLAGS = "flags"
    TIMEOUT = "timeout"
    PRIORITY = "priority"
    STALE = "stale"


# Display labels for the platform's ordered rendered-unit slots, assigned
# by position (the platform carries no labels): slot 0 is the config /
# service unit, slot 1 the schedule-arming companion. `crony status` maps
# a drifted slot index onto these for the STALE column and the per-unit
# path columns' divergence marker.
_UNIT_SLOT_LABELS: tuple[str, ...] = (
    _StatusCols.UNIT_CONFIG_1.value,
    _StatusCols.UNIT_CONFIG_2.value,
)


# Documentation entry for the per-flag column family (one opt-in
# true/false column per `JobFlags` token); not a selectable name.
_FLAG_COL_DOC = "<flag>"


class _ColVisibility(StrEnum):
    """When an alias expansion keeps a column. `ALWAYS` is unconditional;
    the others are dropped from an alias's columns in a context where the
    column would only ever be blank (an explicitly named column is always
    honored regardless). The condition is an intrinsic property of the
    column, so it lives on its `_StatusColumn` rather than being hand-coded
    per alias."""

    ALWAYS = "always"
    IF_MASKED_PRESENT = "if-masked-present"
    IF_SECOND_UNIT_PRESENT = "if-second-unit-present"


class _StatusColumn(NamedTuple):
    """One `crony status` column: its `--cols` name, its table HEADER,
    the prose the `--help` column reference renders, and when an alias
    keeps it (`visibility`). The single description home keeps the help
    text from drifting from the column set. The `_FLAG_COL_DOC`
    (`<flag>`) entry is a documentation entry for the per-flag column
    family, not a selectable name."""

    name: str
    header: str
    description: str
    visibility: _ColVisibility = _ColVisibility.ALWAYS


_STATUS_COLUMNS: tuple[_StatusColumn, ...] = (
    _StatusColumn(
        _StatusCols.JOB,
        "JOB",
        "Full job name: `<bundle>.<short>`. This name may not be usable "
        "with subcommands if a pending configuration update will assign "
        "this name to a new job, in which case you can use the "
        "`<bundle>:<UUID>` name to directly address this job. May be "
        "empty for a broken job with no recoverable name.",
    ),
    _StatusColumn(
        _StatusCols.JOB_OR_UUID,
        "JOB / UUID",
        "Normally the full job name `<bundle>.<short>`, but in the case "
        "of a job naming conflict or a broken job with no recoverable "
        "name this column may report `<bundle>:<UUID>`.",
    ),
    _StatusColumn(
        _StatusCols.KIND,
        "KIND",
        'Job type: "job" or "group".',
    ),
    _StatusColumn(
        _StatusCols.CONFIG,
        "CONFIG",
        'See "CONFIG values".',
    ),
    _StatusColumn(
        _StatusCols.SCHEDULE,
        "SCHEDULE",
        'See "SCHEDULE values".',
    ),
    _StatusColumn(
        _StatusCols.GROUPS,
        "GROUPS",
        "Comma-separated list of job groups containing this job. A job "
        "can only have one unmasked parent, but can have multiple masked "
        "parents. Empty when the job isn't part of any group.",
    ),
    _StatusColumn(
        _StatusCols.STATUS,
        "STATUS",
        'See "STATUS values".',
    ),
    _StatusColumn(
        _StatusCols.LAST_RAN,
        "LAST RAN",
        "Relative time of the last job start.",
    ),
    _StatusColumn(
        _StatusCols.MASKED_BY,
        "MASKED BY",
        "A comma-separated list of reasons why a job is masked (CONFIG = "
        'masked) on the current host. See "MASKED values".',
        _ColVisibility.IF_MASKED_PRESENT,
    ),
    _StatusColumn(
        _StatusCols.UNIT_NAME,
        "UNIT NAME",
        "Platform unit identifier.",
    ),
    _StatusColumn(
        _StatusCols.UUID,
        "UUID",
        "The job's `<bundle>:<UUID>` name.",
    ),
    _StatusColumn(
        _StatusCols.UNIT_CONFIG_1,
        "UNIT CONFIG 1",
        "Filesystem path of the platform config unit. Empty when no "
        "config unit exists on disk.",
    ),
    _StatusColumn(
        _StatusCols.UNIT_CONFIG_2,
        "UNIT CONFIG 2",
        "Filesystem path of the platform's second unit -- the systemd "
        "timer, or the launchd start-time-jitter companion for a jittered "
        "interval job. Empty for a job with no second unit (an unscheduled "
        "or grouped job, or a calendar / short-interval job on "
        "macOS/darwin).",
        _ColVisibility.IF_SECOND_UNIT_PRESENT,
    ),
    _StatusColumn(
        _StatusCols.LOG_FILE,
        "LOG FILE",
        "Filesystem path of the job's log file.",
    ),
    _StatusColumn(
        _StatusCols.FLAGS,
        "FLAGS",
        "Comma-separated list of capability flags enabled for the job. "
        'See "FLAG values".',
    ),
    _StatusColumn(
        _FLAG_COL_DOC,
        "",
        "One opt-in true/false column per capability flag (`--cols "
        "interactive`, etc.). Request by name; the `all` alias omits "
        'these in favor of the compact `flags` column. See "FLAG values".',
    ),
    _StatusColumn(
        _StatusCols.TIMEOUT,
        "TIMEOUT",
        "Job wallclock cap: `<n>s`. The job will be killed if its "
        "wallclock execution time exceeds this cap. May be `none` for "
        "uncapped jobs.",
    ),
    _StatusColumn(
        _StatusCols.PRIORITY,
        "PRIORITY",
        "Job scheduling priority: high | normal | low. Empty for groups.",
    ),
    _StatusColumn(
        _StatusCols.STALE,
        "STALE",
        "A comma-separated list of the snapshot fields that have "
        "diverged between the pending config and the applied unit "
        "(CONFIG = stale). Each is named the way that field is known -- "
        "the config-file knob, a capability flag, a status column, or "
        "its dash-spelled snapshot attribute.",
    ),
)
# Every `_StatusCols` member must carry exactly one registry entry, so
# the help reference and headers can't diverge from the column set.
_documented_cols = [
    col.name for col in _STATUS_COLUMNS if not col.name.startswith("<")
]
assert sorted(_documented_cols) == sorted(_StatusCols), (
    "`_STATUS_COLUMNS` must document every `_StatusCols` member exactly "
    f"once: {sorted(map(str, _documented_cols))} != "
    f"{sorted(map(str, _StatusCols))}"
)

# The per-flag column family: one opt-in column per capability flag,
# keyed by the flag's token, so the set tracks `JobFlags` as members are
# added. These are selectable but not `_StatusCols` members.
_FLAG_COL_TOKENS: frozenset[str] = frozenset(
    f.token for f in crony.config.JobFlags.members()
)

# Selectable columns map name -> HEADER (the `<...>` doc entries are not
# selectable). The per-flag columns are appended after the `_StatusCols`.
# Keys are the plain string column names (not the `_StatusCols` /
# `JobFlagNames` members they derive from) so the registry matches its
# `dict[str, str]` type and never leaks an enum repr when a name list is
# formatted into a `--cols` error.
_STATUS_COL_HEADERS: dict[str, str] = {
    str(col.name): col.header
    for col in _STATUS_COLUMNS
    if not col.name.startswith("<")
}
for _flag_member in crony.config.JobFlags.members():
    _STATUS_COL_HEADERS[str(_flag_member.token)] = _flag_member.token.upper()

# Each selectable column's alias visibility, so `_expand_status_alias`
# trims by column property rather than by hand-coded column name.
_COL_VISIBILITY: dict[str, _ColVisibility] = {
    col.name: col.visibility
    for col in _STATUS_COLUMNS
    if not col.name.startswith("<")
}


# Wrap width for the `crony status --help` epilog text. Each section's
# body is indented two spaces under its `Title:` header (matching the
# top-level `crony --help` epilog), so content wraps at 76 to stay within
# 78 once indented. The man page renders the same reference from
# structured data (it reflows at display width), so this only governs the
# terminal --help layout.
_STATUS_HELP_WIDTH = 76


def _column_items(*, default: bool) -> list[tuple[str, str]]:
    """The `_STATUS_COLUMNS` reference items, split into the default set
    (shown when `--cols` is omitted, in display order) and the opt-in
    remainder (sorted alphabetically; `<...>` documentation entries like
    `<flag>` sort by their bare name)."""
    defaults = set(_DEFAULT_STATUS_COLS)
    cols = [c for c in _STATUS_COLUMNS if (c.name in defaults) == default]
    if not default:
        cols.sort(key=lambda c: c.name.strip("<>"))
    return [(c.name, c.description) for c in cols]


# The Colors lead-in, stored raw (unwrapped) so each consumer wraps to
# its own width: the `--help` epilog fills it, the man-page renderer lets
# the formatter reflow.
_COLOR_LEAD = (
    "On a color-capable TTY (and NO_COLOR unset) some cells are colored; "
    "redirected or piped output is plain, where drift shows as a trailing "
    "`^` plus a footnote legend instead."
)


def _color_items() -> list[tuple[str, str]]:
    """The red / yellow palette as reference items, rendered from
    `_RED_CELLS` / `_YELLOW_CELLS` so the documented colors track what
    `_status_value_color` paints."""

    def palette(table: dict[str, frozenset[str]]) -> str:
        parts: list[str] = []
        for col, values in table.items():
            header = _STATUS_COL_HEADERS.get(col, col.upper())
            parts.append(f"{header} {' / '.join(sorted(values))}")
        return "; ".join(parts)

    return [
        ("red", f"{palette(_RED_CELLS)}."),
        (
            "yellow",
            f"{palette(_YELLOW_CELLS)}, plus any cell that diverged from "
            "the applied state (on a color stream its `^` marker is "
            "dropped in favor of the color).",
        ),
    ]


class _StatusAliases(StrEnum):
    """The `--cols` alias names. Each expands to a list of `_StatusCols`
    via its `_STATUS_ALIASES` entry."""

    DEFAULT = "default"
    ALL = "all"
    UNIT_FILES = "unit-files"


# A parsed `--cols` token: a concrete column, an alias (expanded later
# with runtime context), or a per-flag column (`JobFlagNames`). All
# three are StrEnums with disjoint string values, so the union
# round-trips to the on-disk / row-key spelling while staying closed
# and type-checked.
ColToken = _StatusCols | _StatusAliases | crony.config.JobFlagNames


class _StatusAlias(NamedTuple):
    """A `--cols` alias: its name, the `_StatusCols` it expands to (before
    the context trim `_expand_status_alias` applies), and the prose the
    `--help` Aliases section renders. The single `cols` home keeps the
    expansion and its documentation from drifting from each other."""

    name: _StatusAliases
    cols: tuple[_StatusCols, ...]
    description: str


_DEFAULT_STATUS_COLS: tuple[_StatusCols, ...] = (
    _StatusCols.JOB_OR_UUID,
    _StatusCols.CONFIG,
    _StatusCols.SCHEDULE,
    _StatusCols.STATUS,
    _StatusCols.LAST_RAN,
)
_STATUS_ALIASES: tuple[_StatusAlias, ...] = (
    _StatusAlias(
        _StatusAliases.DEFAULT,
        _DEFAULT_STATUS_COLS,
        "The columns shown when `--cols` is omitted: "
        + ", ".join(_DEFAULT_STATUS_COLS)
        + ".",
    ),
    _StatusAlias(
        _StatusAliases.ALL,
        tuple(_StatusCols),
        "Every column except the per-flag columns (use the compact "
        "`flags` instead), `masked-by` (kept only when a masked entry is "
        "present), and the optional `unit-config-2` (shown only where a "
        "second unit is present). Naming an excluded column explicitly "
        "still shows it.",
    ),
    _StatusAlias(
        _StatusAliases.UNIT_FILES,
        (_StatusCols.UNIT_CONFIG_1, _StatusCols.UNIT_CONFIG_2),
        "unit-config-1, plus the optional unit-config-2 where present.",
    ),
)
_STATUS_ALIAS_BY_NAME: dict[str, _StatusAlias] = {
    a.name: a for a in _STATUS_ALIASES
}
# Plain string alias names (not `_StatusAliases` members), so the
# registry matches its `tuple[str, ...]` type and formats as bare values
# in a `--cols` error rather than enum reprs.
_STATUS_COL_ALIAS_NAMES: tuple[str, ...] = tuple(str(a) for a in _StatusAliases)
_JOB_FLAG_COL_NAMES: frozenset[str] = frozenset(crony.config.JobFlagNames)

# Silent `--cols` spellings accepted but never advertised: `defaults` is
# easy to reach for in place of the canonical `default`. Kept out of the
# `--help` Aliases section and the parse-error listing so the documented
# surface stays a single spelling.
_SILENT_COL_ALIASES: dict[str, ColToken] = {
    "defaults": _StatusAliases.DEFAULT,
}


def _column_in_context(
    col: str, *, masked_present: bool, second_unit_present: bool
) -> bool:
    """Whether a column's `_ColVisibility` keeps it in this context. A
    column would only ever be blank here when its condition fails -- a
    masked-reason column with no masked row shown, or the second-unit
    column when no shown row carries a second unit. The condition is an
    intrinsic per-column property, so this same pass serves every
    backend."""
    visibility = _COL_VISIBILITY[col]
    if visibility is _ColVisibility.IF_MASKED_PRESENT:
        return masked_present
    if visibility is _ColVisibility.IF_SECOND_UNIT_PRESENT:
        return second_unit_present
    return True


def _expand_status_alias(
    name: str, *, masked_present: bool, second_unit_present: bool
) -> tuple[str, ...]:
    """Expand a `--cols` alias to its column list for this context.

    A column whose `_ColVisibility` condition fails here would only ever
    be blank, so the alias drops it to keep the wide views useful rather
    than padded with dead space. The rule is the column's own property,
    so the same pass serves every alias and a future conditional column
    is trimmed without touching this function. Trimming applies only to
    the alias; a column named explicitly is always honored (`--cols
    all,unit-config-2` still shows the second unit).
    """
    return tuple(
        col
        for col in _STATUS_ALIAS_BY_NAME[name].cols
        if _column_in_context(
            col,
            masked_present=masked_present,
            second_unit_present=second_unit_present,
        )
    )


# ANSI color for the status table, emitted only when stdout is a TTY
# and NO_COLOR is unset (https://no-color.org/). Red flags a broken or
# failed state -- including a `disabled` SCHEDULE cell, where the entry
# isn't firing; yellow flags drift the operator can reconcile with
# `crony apply` (a `stale` config verdict, or any divergence-flagged
# cell). The `^` marker itself is never colored.
_ANSI_RED: str = "\033[31m"
_ANSI_YELLOW: str = "\033[33m"
_ANSI_RESET: str = "\033[0m"

# Per-column cell values that take a color, keyed by `--cols` name.
# `_status_value_color` reads these to paint the table and the `--help`
# Color section renders its palette from them, so what the docs claim
# can't drift from what renders. Red flags a broken or failed state
# (`signal` never reaches the STATUS cell -- it renders as `fail`);
# yellow flags reconcilable `stale` drift. Each value is a `ConfigStatus`
# / `JobStatus` member (both StrEnums, so plain strings) or a literal
# cell string.
_RED_CELLS: dict[str, frozenset[str]] = {
    _StatusCols.CONFIG: frozenset(
        {
            crony.model.ConfigStatus.MISSING,
            crony.model.ConfigStatus.ERROR,
            crony.model.ConfigStatus.BROKEN,
            crony.model.ConfigStatus.ORPHAN,
        }
    ),
    _StatusCols.STATUS: frozenset(
        {
            crony.model.JobStatus.FAIL,
            crony.model.JobStatus.TIMEOUT,
            crony.model.JobStatus.CANCELED,
            crony.model.JobStatus.CRASHED,
        }
    ),
    _StatusCols.SCHEDULE: frozenset({crony.model.ScheduleValue.DISABLED.value}),
}
_YELLOW_CELLS: dict[str, frozenset[str]] = {
    _StatusCols.CONFIG: frozenset({crony.model.ConfigStatus.STALE}),
}


def status_reference_sections() -> list[ReferenceSection]:
    """The `crony status` column / value / alias / color reference as
    structured sections, sourced from the registries and enums. The
    single source for both the `--help` epilog text and the man page's
    STATUS COLUMNS section, so the two can't drift."""
    return [
        ReferenceSection("Default Columns", _column_items(default=True)),
        ReferenceSection("Optional Columns", _column_items(default=False)),
        ReferenceSection(
            "Column Aliases",
            [(a.name, a.description) for a in _STATUS_ALIASES],
        ),
        ReferenceSection(
            "CONFIG values",
            [(m.value, m.description) for m in crony.model.ConfigStatus],
        ),
        ReferenceSection(
            "SCHEDULE values",
            [(m.value, m.description) for m in crony.model.ScheduleValue],
        ),
        ReferenceSection(
            "STATUS values",
            [(m.value, m.description) for m in crony.model.JobStatus],
        ),
        ReferenceSection(
            "FLAG values",
            [(f.token, f.description) for f in crony.config.JobFlags.members()],
        ),
        ReferenceSection(
            "MASKED values",
            [(r.value, r.description) for r in crony.config.MaskReason],
        ),
        ReferenceSection("Colors", _color_items(), lead=_COLOR_LEAD),
    ]


STATUS_HELP_EPILOG: str = (
    "\n\n".join(
        reference_section_text(s, width=_STATUS_HELP_WIDTH)
        for s in status_reference_sections()
    )
    + "\n"
)


def _classify_col_token(name: str) -> ColToken:
    """Map a validated `--cols` name to its column / alias / flag enum."""
    if name in _SILENT_COL_ALIASES:
        return _SILENT_COL_ALIASES[name]
    if name in _STATUS_COL_ALIAS_NAMES:
        return _StatusAliases(name)
    if name in _JOB_FLAG_COL_NAMES:
        return crony.config.JobFlagNames(name)
    return _StatusCols(name)


def parse_cols_arg(value: str) -> list[ColToken]:
    """argparse `type=` for `status --cols`: parse into an ordered list.

    Splits the comma-separated spec (whitespace around names ignored)
    and rejects any name that is not a column, a per-flag column, or an
    alias (canonical or a silent `_SILENT_COL_ALIASES` spelling), so a
    typo is loud at parse time rather than a silent missing column.
    Returns each name as its `_StatusCols` / `_StatusAliases` /
    `JobFlagNames` enum member; `_parse_status_cols` expands the aliases
    among them once the displayed rows and platform are known.
    """
    raw = [c.strip() for c in value.split(",") if c.strip()]
    valid = (
        set(_STATUS_COL_HEADERS)
        | set(_STATUS_COL_ALIAS_NAMES)
        | set(_SILENT_COL_ALIASES)
    )
    unknown = [c for c in raw if c not in valid]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown status column(s): {sorted(unknown)} "
            f"(valid: {sorted(_STATUS_COL_HEADERS)}; "
            f"aliases: {sorted(_STATUS_COL_ALIAS_NAMES)})"
        )
    return [_classify_col_token(c) for c in raw]


def _parse_status_cols(
    raw: list[ColToken] | None,
    *,
    masked_present: bool,
    second_unit_present: bool,
) -> list[str]:
    """Expand the parsed `--cols` tokens into an ordered column list.

    `job-or-uuid` is always included (and forced to the first
    column) because everything else is meaningless without an
    entity identity, and it's the one column guaranteed to be
    pasteable back into `crony destroy` even for nameless or
    name-shadowed rows. `default`, `all`, and `unit-files` are
    aliases expanded by `_expand_status_alias` (which trims columns
    that would be blank in this context); mixing aliases with
    explicit names is allowed (`default,masked-by`), and an
    explicitly named column is never trimmed. Order is preserved
    across the resolved list with duplicates dropped.
    """
    if not raw:
        return list(_DEFAULT_STATUS_COLS)
    expanded: list[str] = []
    for token in raw:
        if isinstance(token, _StatusAliases):
            expanded.extend(
                _expand_status_alias(
                    token,
                    masked_present=masked_present,
                    second_unit_present=second_unit_present,
                )
            )
        else:
            expanded.append(token)
    seen: set[str] = set()
    cols: list[str] = []
    for col in expanded:
        if col in seen:
            continue
        seen.add(col)
        cols.append(col)
    if _StatusCols.JOB_OR_UUID in cols:
        cols.remove(_StatusCols.JOB_OR_UUID)
    return [_StatusCols.JOB_OR_UUID] + cols


def _resolve_states(
    config: crony.model.Config,
    full: str,
    remnants: set[str],
    *,
    mask_reason: str = "",
) -> tuple[
    crony.model.ConfigStatus,
    crony.model.JobStatus,
]:
    """Compute the (cfg_status, job_status) pair for one full name.

    `do_status` is the only consumer; the function is factored
    out so the derivation has a single home and the renderer-side
    filter (`--exclude-healthy`) reads the same pair as the default
    tree view. Returns the values straight from the underlying state
    readers -- no opinion about whether a given combination is "bad"
    -- so the caller applies its own filtering / display logic on top.

    CONFIG precedence:

        error / broken  >  orphan  >  masked  >  synced / stale / missing

    `error` and `broken` are top-of-chain. `error` reports an
    entry whose TOML failed to parse; `broken` reports an entry
    whose on-disk snapshot can't be loaded, or whose installed unit
    can't run -- its baked uv / crony binary is gone, its config unit
    file was deleted while the scheduler still has it loaded, the
    scheduler has no unit loaded for it at all, or a scheduled entry's
    timer file is gone. Either way the operator's next action is "fix
    this specific thing," and the mask-reason / synced-stale-missing
    states are uninteresting until then.

    The synced / stale / missing / orphan / broken base comes
    from `_cfg_status`, which scores the entity against the
    in-memory `Config` (no disk re-query). `mask_reason` is the
    entry's filter-exclusion reason on this host; when non-empty,
    the cfg value becomes `"orphan"` if on-disk remnants exist and
    `"masked"` otherwise. The orphan-on-mask rule encodes
    "cleanup needed wins over passive-not-for-this-host."
    """
    bn, short = crony.config.parse_full_name(full)
    bundle = config.toml_config.by_name(bn)
    entry: crony.config.TomlJob | crony.config.TomlJobGroup | None = None
    if bundle is not None and (
        short in bundle.config.errored_jobs
        or short in bundle.config.errored_job_groups
    ):
        # An errored entry never parsed into a uuid, so it has no
        # ref to score against the graphs; `error` is top-of-chain
        # and surfaces by name.
        cfg_status = crony.model.ConfigStatus.ERROR
    else:
        if bundle is not None:
            entry = bundle.config.jobs.get(
                short
            ) or bundle.config.job_groups.get(short)
        cfg_status = _cfg_status(config, full, entry, bn)
    if mask_reason and cfg_status not in (
        crony.model.ConfigStatus.ERROR,
        crony.model.ConfigStatus.BROKEN,
    ):
        cfg_status = (
            crony.model.ConfigStatus.ORPHAN
            if full in remnants
            else crony.model.ConfigStatus.MASKED
        )
    job_status = _job_status(config, full)
    return cfg_status, job_status


def _entry_is_scheduled(
    entry: crony.config.TomlJob | crony.config.TomlJobGroup | None,
) -> bool:
    # "Scheduled" selects the `.timer` unit name over the bare
    # `.service`. A dormant entry (transit group, trigger-only OnDemand,
    # or no timing) is not scheduled -- `is_scheduled` is the shared
    # "arms a real timer" test.
    if entry is None:
        return False
    return crony.unit.is_scheduled(entry.timing)


def _snapshot_says_scheduled(
    config: crony.model.Config, full: str
) -> bool | None:
    """`True` / `False` if the current-graph entry for `full`
    arms a real timer (a Schedule / Interval, so the scheduler names
    its `.timer`); `None` otherwise. A dormant entry -- a transit group
    or a trigger-only OnDemand job -- reads `False` (the bare
    `.service`). Used to guess UNIT NAME for entries whose live config no
    longer exists.

    The lookup goes through `resolve_runnable` (current only).
    A broken or unit-only or pending-only entry returns `None`
    here because it's not in `current` -- the caller
    (`_unit_name_for`) then renders an empty cell or falls
    through to the pending-side answer if a `TomlJob` /
    `TomlJobGroup` is in hand.
    """
    ref = config.resolve_runnable(full)
    if ref is None:
        return None
    snap = config.current.job_from_ref(ref)
    if snap is not None:
        return crony.unit.is_scheduled(snap.timing)
    return None


def _unit_name_for(
    full: str,
    entry: crony.config.TomlJob | crony.config.TomlJobGroup | None,
    config: crony.model.Config,
    platform: str | None = None,
) -> str:
    """Platform unit identifier for the UNIT NAME column.

    Resolves the entry's scheduled-ness -- from the live config entry,
    else the current-graph snapshot -- and hands it to the scheduler,
    which names the unit. `None` scheduled-ness (neither config nor
    snapshot can decide) lets a backend whose name is schedule-
    independent still answer, and one that needs it return "".
    """
    if entry is not None:
        scheduled: bool | None = _entry_is_scheduled(entry)
    else:
        scheduled = _snapshot_says_scheduled(config, full)
    return crony.runtime.scheduler(platform).unit_name(full, scheduled)


# Trailing flag on a status cell whose pending and current values
# diverge. `^` (not `*`) so it can't be mistaken for a schedule
# wildcard (`*-*-* 02:00`); appended with no separating space.
_DIVERGENCE_MARKER: str = "^"

_STALE_VALUE_FOOTER: str = (
    f"{_DIVERGENCE_MARKER} -- One or more flagged cells are stale; "
    "`crony apply` reconciles them. Either the pending config value "
    "(shown by default) differs from the applied one -- see it with "
    "--config-current -- or an installed unit file drifted from the "
    "snapshot."
)


def _color_supported() -> bool:
    """Return True if ANSI color escape sequences should be emitted.

    Color is suppressed when NO_COLOR is set in the environment or
    when stdout isn't a TTY -- redirecting to a file or piping into
    another process should produce plain text.
    """
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _status_value_color(col: str, value: str) -> str | None:
    """ANSI code for a status cell by (column, value), or None, from the
    `_RED_CELLS` / `_YELLOW_CELLS` palette. Only the verdict columns
    carry a value-based color; the divergence marker is handled
    separately by the renderer."""
    if value in _RED_CELLS.get(col, frozenset()):
        return _ANSI_RED
    if value in _YELLOW_CELLS.get(col, frozenset()):
        return _ANSI_YELLOW
    return None


def _render_status_cell(
    col: str, value: str, width: int, use_color: bool
) -> str:
    """Render one status cell padded to `width`.

    Staleness has two mutually exclusive presentations. On a plain
    stream the cell keeps its `^` marker(s) verbatim (the footer legend
    explains them). On a color stream the marker is dropped -- a stale
    cell is shown by coloring its value yellow instead -- so the `^`
    never clutters interactive output. Padding is computed from the
    visible text and kept outside the color codes so zero-width escapes
    never throw off column alignment.
    """
    if not use_color:
        return value + " " * max(0, width - len(value))
    stale = _DIVERGENCE_MARKER in value
    body = value.replace(_DIVERGENCE_MARKER, "")
    pad = " " * max(0, width - len(body))
    if stale:
        return f"{_ANSI_YELLOW}{body}{_ANSI_RESET}{pad}"
    code = _status_value_color(col, body)
    if code is None:
        return body + pad
    return f"{code}{body}{_ANSI_RESET}{pad}"


def _build_group_membership(
    config: crony.model.Config,
) -> tuple[
    dict[crony.unit.EntityRef, list[str]], dict[crony.unit.EntityRef, list[str]]
]:
    """Reverse-index group membership from the pending and current
    graphs.

    Returns `(pending, current)` where `<table>[<child_ref>]` lists
    the full names of every group whose `children` list contains
    the child in that graph. The `children` lists hold the child
    `EntityRef`s (the apply-time edge), so the table is keyed by that
    ref -- the uuid in it survives a rename, so the row still finds
    its membership regardless of which name it displays under. The
    parent names come from the same graph, so the two sides diverge
    when a group has been edited but not re-applied.
    Each value list is sorted for stable display order.
    """

    def _membership(
        graph: crony.model.Graph,
    ) -> dict[crony.unit.EntityRef, list[str]]:
        table: dict[crony.unit.EntityRef, list[str]] = {}
        for parent in graph.groups.values():
            for child_ref in parent.children:
                if (
                    child_ref not in graph.jobs
                    and child_ref not in graph.groups
                ):
                    continue
                table.setdefault(child_ref, []).append(str(parent.entity_name))
        for v in table.values():
            v.sort()
        return table

    return _membership(config.pending), _membership(config.current)


def _build_config_group_membership(
    toml_config: crony.config.TomlConfig,
) -> dict[str, list[str]]:
    """Reverse-index group membership straight from the parsed TOML,
    spanning every defined group whether or not it is selected here.

    `_build_group_membership` only sees entries that survive host /
    platform selection into the pending / current graphs, so a
    masked entry's GROUPS cell comes up empty even though the status
    tree still nests it under its parent. This walks the same source
    `_build_status_tree` does -- each group's `jobs` list -- so a
    masked entry can fall back to its config-declared membership.
    Keyed `child_full -> sorted [parent_full]`.
    """
    membership: dict[str, list[str]] = {}
    for bundle in toml_config.bundles:
        for parent_short, group in bundle.config.job_groups.items():
            parent_full = bundle.full_name(parent_short)
            for child_short in group.jobs:
                child_full = bundle.full_name(child_short)
                membership.setdefault(child_full, []).append(parent_full)
    for parents in membership.values():
        parents.sort()
    return membership


def _node_flags(
    node: crony.model.Job | crony.model.JobGroup | None,
) -> crony.config.JobFlags | None:
    """The resolved per-flag view of a status row's graph node, or
    None for an absent node (so its flag cells render empty).

    Both jobs and groups carry a resolved flags bitmask. A group's is
    its cascade value -- shown so the inheritance it hands down to its
    children is visible, even though group flags have no runtime effect
    on the group itself.
    """
    return node.flags if node is not None else None


def _diverged(pending: object, current: object) -> bool:
    """`^`-annotation predicate: the entity exists in both graphs
    AND the values disagree on this field. The annotation never
    depends on the display mode -- it just signals "the other view
    of this cell says something different."
    """
    return pending is not None and current is not None and pending != current


def _select_sourced(
    pending_val: str | None,
    current_val: str | None,
    config_source: str,
) -> tuple[str, bool]:
    """Pick the displayed value for a dual-source field, plus whether
    the two sides diverge.

    Both values are read by uuid by the caller, so a rename (new name
    in pending, old name in current, same uuid) compares the right
    pair. `is_stale` fires only when both sides have a value and they
    disagree -- a `^` marker meaning "the other view differs."

    Source selection:
      default           pending value, else current (pending-first).
      --config-pending  pending value (else empty).
      --config-current  current value (else empty).
    """
    is_stale = _diverged(pending_val, current_val)
    if config_source == "current":
        return (current_val or "", is_stale)
    if config_source == "pending":
        return (pending_val or "", is_stale)
    return (
        (pending_val if pending_val is not None else current_val) or "",
        is_stale,
    )


def _select_name(
    pending_name: str | None,
    current_name: str | None,
    config_source: str,
) -> str:
    """Pick the displayed name for a row.

    Identity must never render blank, so this falls back to the other
    side under every mode (unlike the field selector, which leaves a
    cell empty when the chosen source has no value). `--config-current`
    prefers the applied name; default / `--config-pending` prefer the
    config name -- so a rename shows its new name by default and its
    old applied name under `--config-current`.
    """
    if config_source == "current":
        return current_name or pending_name or ""
    return pending_name or current_name or ""


def _keep_awake_warning(config: crony.model.Config) -> str | None:
    """A warning for `crony status` / `crony config validate` when a
    configured job requests keep-awake but this host cannot honor it,
    else None.

    keep-awake degrades to running the job unwrapped (see
    `HostPlatform.keep_awake_argv`), so this is advisory, not a job
    error. On Linux the sleep inhibitor is a privileged polkit action
    that a seatless job's user is commonly denied.
    """
    ka_flag = crony.config.JobFlags.KEEP_AWAKE
    wants_keep_awake = any(
        ka_flag in job.flags for job in config.pending.jobs.values()
    )
    if not wants_keep_awake or crony.runtime.host().keep_awake_available():
        return None
    return (
        "keep-awake is requested by a job but this host cannot acquire a "
        "sleep inhibitor, so those jobs run without it. Grant the job's "
        "user the org.freedesktop.login1.inhibit-block-sleep polkit "
        "action to enable it."
    )


def do_status(
    jobs: list[str],
    cols: list[ColToken] | None,
    show_masked: bool,
    bundle: str | None,
    config_current: bool,
    config_pending: bool,
    exclude_healthy: bool,
) -> None:
    """Print the resolved state per job in a tabular view.

    With no args, the report covers selected jobs/groups plus any
    orphans across all bundles. Names are full namespaced
    (`<bundle>.<short>`); bare CLI input is shorthand for
    `default.<short>`. Linger status on linux surfaces above the
    table.

    With `--bundle <name>`, restricts the table (and the masked-
    entries set surfaced by `-a`) to that bundle. Bare CLI input
    resolves in `<name>` and qualified names must match it.

    Column selection is via `--cols`; valid names, aliases, and
    descriptions live in `crony status --help`'s epilog. `-a` /
    `--all` lists every defined entry, including ones masked on
    the current host (target-unused or excluded by the entry's
    `platforms` / `hosts` filters). `--config-current` and
    `--config-pending` force every dual-source column (name, kind,
    schedule, groups, unit-name, log-file, flags and the per-flag
    columns) to a single source; the `^` divergence flag still fires
    whenever the two sources differ. The two flags are mutually
    exclusive.

    `--exclude-healthy` drops rows where CONFIG is `synced`, the
    entry is not disabled, and STATUS is `ok` / `never` / `gated`.
    A disabled entry is unhealthy (it isn't firing), so it survives
    the filter. Output is flat (no tree indent). Always exits 0 --
    this is a filter on the display, not a gate.

    On a color-capable TTY (NO_COLOR unset) broken / failed verdicts
    render red and reconcilable drift renders yellow; see the
    `--help` epilog's Color section. Redirected output is plain.
    """
    config_source = (
        "current"
        if config_current
        else "pending"
        if config_pending
        else "default"
    )
    config = crony.runtime.load_config()
    bundles = config.toml_config
    config.require_addressable(bundle)
    platform = config.platform
    _by_full, selected, masked_by_full = (
        _selected_and_masked_full_names_per_bundle(bundles)
    )
    remnants = config.installed_full_names()
    if bundle is not None:
        selected = crony.config.bundle_prefix_filter(selected, bundle)
        remnants = crony.config.bundle_prefix_filter(remnants, bundle)
        masked_by_full = {
            n: r
            for n, r in masked_by_full.items()
            if n.startswith(f"{bundle}.")
        }

    try:
        crony.runtime.scheduler().verify()
    except crony.platform.SchedulerWarning as warn:
        logger.warning("%s", warn)

    keep_awake_warn = _keep_awake_warning(config)
    if keep_awake_warn is not None:
        logger.warning("%s", keep_awake_warn)

    # Surface bundle parse failures at the top of the report so
    # the operator sees them before scanning the table. The table
    # itself shows whatever is interpretable on-disk; entries
    # whose bundle didn't load show up as orphans.
    if bundles.errored_bundles:
        print("bundle parse failures (config-side entries not loaded):")
        for src, msg in bundles.errored_bundles.items():
            print(f"  {src}: {msg}")
        print()

    full_names: list[str]
    if jobs:
        full_names = [crony.config.resolve_cli_name(n, bundle) for n in jobs]
    else:
        errored_full = _errored_full_names(bundles, bundle)
        # On-disk orphans with no recoverable name (a broken snapshot
        # or a bare state dir with no snapshot at all), and current
        # entries whose name is shadowed by a collision, are
        # addressable only through the synthetic `<bundle>:<UUID>`
        # form -- their row carries that form in the JOB / UUID
        # cell. (A shadowed entry's plain name still belongs to its
        # collision winner, so adding the name to `active` would
        # only re-render the winner.) A nameless orphan whose ref is
        # still a live pending entry is that entry's own wiped state,
        # rendered under the entry's named row -- not a separate
        # ref-form row.
        pending_refs = config.pending.refs()
        ref_form_only = {
            str(b.entity_ref)
            for b in config.orphans.values()
            if b.name is None
            and b.entity_ref not in pending_refs
            and (bundle is None or b.bundle == bundle)
        }
        ref_form_only |= {
            str(ref)
            for ref in config.shadowed
            if bundle is None or ref.bundle == bundle
        }
        # Errored entries always surface in the default view --
        # without this the user can fix a typo only by reading
        # the warning line; they wouldn't see the entry in the
        # table itself. Unit-only orphans (platform unit files
        # with no state dir) are already in `remnants` --
        # `Config.installed_full_names()` includes the unit-only
        # set -- so we don't add them separately.
        active = selected | remnants | errored_full | ref_form_only
        if show_masked:
            active = active | set(masked_by_full)
        full_names = sorted(active)

    tree_order, tree_depth = _build_status_tree(bundles, config.host, platform)
    if bundle is not None:
        tree_order = [n for n in tree_order if n.startswith(f"{bundle}.")]
        tree_depth = {
            n: d for n, d in tree_depth.items() if n.startswith(f"{bundle}.")
        }
    pending_groups, current_groups = _build_group_membership(config)
    config_groups = _build_config_group_membership(bundles)

    # Collapse the enumerated names to one row per uuid: a rename
    # surfaces one entity under its applied (current) name and its
    # config (pending) name, both resolving to the same uuid, and must
    # render as a single row shown under the name from the active
    # source. Errored entries never parsed to a uuid and keep their
    # own name-keyed row.
    names_by_ref: dict[crony.unit.EntityRef, list[str]] = {}
    refform_refs: set[crony.unit.EntityRef] = set()
    nameless_rows: list[str] = []
    for full in full_names:
        direct = crony.unit.EntityRef.from_str(full)
        if direct is not None:
            refform_refs.add(direct)
            names_by_ref.setdefault(direct, []).append(full)
            continue
        r = config.resolve_pending(full) or config.resolve_current(full)
        if r is None:
            bn0, short0 = crony.config.parse_full_name(full)
            bdl0 = bundles.by_name(bn0)
            e0 = (
                bdl0.config.jobs.get(short0)
                or bdl0.config.job_groups.get(short0)
                if bdl0 is not None
                else None
            )
            if e0 is not None:
                r = crony.unit.EntityRef(bn0, e0.uuid)
        if r is None:
            nameless_rows.append(full)
        else:
            names_by_ref.setdefault(r, []).append(full)

    built: list[tuple[str, dict[str, str]]] = []

    def _build_row(
        ref: crony.unit.EntityRef | None, candidates: list[str]
    ) -> None:
        def _mark(pending_val: str | None, current_val: str | None) -> str:
            # A dual-source cell: pick the active source's value and
            # append the divergence flag when the two sides differ.
            value, diverged = _select_sourced(
                pending_val, current_val, config_source
            )
            if diverged:
                value = f"{value}{_DIVERGENCE_MARKER}"
            return value

        pending_node = (
            config.pending.job_from_ref(ref) if ref is not None else None
        )
        current_node = (
            config.current.job_from_ref(ref) if ref is not None else None
        )
        pending_name = (
            str(pending_node.entity_name) if pending_node is not None else None
        )
        current_name = (
            str(current_node.entity_name) if current_node is not None else None
        )
        if current_name is None and ref is not None:
            oe = config.orphans.get(ref)
            current_name = oe.name if oe is not None else None
        fallback = sorted(candidates)[0] if candidates else ""
        # The config (pending) name drives tree placement, masking,
        # and the TOML-entry lookup; the displayed identity is chosen
        # by source. A masked entry is in neither graph -- fall back to
        # the enumerated name.
        config_name = pending_name or fallback
        mask_reason = masked_by_full.get(config_name, "")
        bn, short = crony.config.parse_full_name(config_name)
        bdl = bundles.by_name(bn)
        entry: crony.config.TomlJob | crony.config.TomlJobGroup | None = (
            bdl.config.jobs.get(short) or bdl.config.job_groups.get(short)
            if bdl is not None
            else None
        )
        # A ref-form row (shadowed collision loser, or a broken
        # snapshot with no recoverable name) renders by uuid in the
        # identity column; its plain name belongs to another entity.
        is_refform = ref is not None and ref in refform_refs

        pkind = pending_node.kind if pending_node is not None else None
        ckind = current_node.kind if current_node is not None else None
        if pkind is not None or ckind is not None:
            kind = _mark(pkind, ckind)
        elif isinstance(entry, crony.config.TomlJobGroup):
            kind = crony.unit.EntityKind.GROUP
        elif entry is not None:
            kind = crony.unit.EntityKind.JOB
        else:
            # No node and no live config entry: an off-graph ref whose
            # kind nothing on this side records, so the cell is blank.
            kind = ""

        # CONFIG / STATUS are single-source verdicts (not flag-selected);
        # resolve them against the config name so the errored detection
        # lands on the right entry.
        cfg_status, job_status = _resolve_states(
            config, config_name, remnants, mask_reason=mask_reason
        )
        last_ran = _last_ran_at(config, config_name)
        # The operator-disable rides on the applied snapshot, so it reads
        # off the current node (a disable always follows an apply). The
        # override below keys off this in every --config-* view -- the
        # pending side, which load mirrors to the same value, shows
        # `disabled` too rather than the schedule it would compare to.
        disabled = current_node is not None and current_node.unit_disabled

        # SCHEDULE / GROUPS / name are dual-source: read each side by
        # uuid and let the active source pick (default pending-first).
        masked_in_tree = bool(mask_reason) and config_name in tree_depth
        pending_sched = (
            _schedule_display(pending_node.timing)
            if pending_node is not None
            else None
        )
        if pending_sched is None and masked_in_tree and entry is not None:
            pending_sched = _schedule_display(entry.timing)
        current_sched = (
            _schedule_display(current_node.timing)
            if current_node is not None
            else None
        )
        if disabled:
            # A disabled entry won't fire on its schedule (and a disabled
            # group child is skipped by its parent), so the cron
            # expression is misleading -- show the disable instead, in
            # every view: the pending config carries the same disable, so
            # the schedule it would compare against is "disabled" too.
            sched_cell = crony.model.ScheduleValue.DISABLED.value
        else:
            sched_cell = _mark(pending_sched, current_sched)

        # A node present in a graph has a well-defined membership even
        # when it belongs to no group -- that empty value ("") still
        # diverges from a non-empty one, so the flag fires. Only an
        # entity absent from the graph entirely reads as None (no view).
        pending_members = pending_groups.get(ref, []) if ref is not None else []
        current_members = current_groups.get(ref, []) if ref is not None else []
        pending_groups_str: str | None = (
            ",".join(pending_members) if pending_node is not None else None
        )
        current_groups_str: str | None = (
            ",".join(current_members) if current_node is not None else None
        )
        if masked_in_tree and not pending_members and not current_members:
            # Surface the config-declared parent for a masked entry
            # absent from both graphs; the current side stays None so
            # the config-only value doesn't read as drift.
            cfg_members = config_groups.get(config_name, [])
            pending_groups_str = ",".join(cfg_members) if cfg_members else None
            current_groups_str = None
        groups_cell = _mark(pending_groups_str, current_groups_str)

        # The displayed name always falls back to the other source so
        # identity never renders blank; the flag fires when the config
        # and applied names diverge (a not-yet-applied rename).
        display_name = (
            _select_name(pending_name, current_name, config_source) or fallback
        )
        if _diverged(pending_name, current_name):
            display_name = f"{display_name}{_DIVERGENCE_MARKER}"
        # UNIT NAME is the platform label of the source-selected name,
        # flagged when the two names give different labels.
        pending_unit = (
            _unit_name_for(pending_name, entry, config)
            if pending_name is not None
            else None
        )
        current_unit = (
            _unit_name_for(current_name, None, config)
            if current_name is not None
            else None
        )
        if config_source == "current":
            unit_name = current_unit or pending_unit or ""
        else:
            unit_name = pending_unit or current_unit or ""
        if _diverged(pending_unit, current_unit):
            unit_name = f"{unit_name}{_DIVERGENCE_MARKER}"
        if is_refform:
            # A ref-form row is addressable only by uuid -- a shadowed
            # loser's plain name belongs to its live collision winner,
            # a nameless orphan has none. Show the ref in both the JOB
            # and JOB / UUID columns so no column prints a name the
            # operator can't act on.
            row_ref: crony.unit.EntityRef | None = ref
            job_cell = str(ref) if ref is not None else ""
            job_or_uuid_cell = str(ref)
        else:
            row_ref = ref
            job_cell = display_name
            job_or_uuid_cell = display_name
        uuid_cell = str(row_ref) if row_ref is not None else ""
        # `unit-config-1` / `unit-config-2`: the platform unit paths captured
        # at load time (uuid-keyed RuntimeState), read positionally from
        # the ordered per-unit view. Empty for entries with no runtime, and
        # unit-config-2 is empty for a job with no second unit -- an
        # unscheduled or grouped entry, or a calendar / short-interval job
        # on launchd (only a jittered interval job there gets a companion).
        rt = config.runtime.get(row_ref) if row_ref is not None else None
        views = rt.unit_paths if rt is not None else []
        unit_1_cell = str(views[0]) if len(views) > 0 and views[0] else ""
        unit_2_cell = str(views[1]) if len(views) > 1 and views[1] else ""
        # Flag the specific unit file whose install drifted from the
        # snapshot (re-apply re-renders it), mirroring the `stale`
        # column's `unit-config-1` / `unit-config-2` tokens. Drift is the
        # normalized unit differing between the pending and current
        # nodes; an orphan / broken / pending-only row lacks one side and
        # goes unflagged.
        if pending_node is not None and current_node is not None:
            drifted = set(
                crony.model.rendered_drifted_indices(
                    pending_node.rendered_units,
                    current_node.rendered_units,
                )
            )
            if unit_1_cell and 0 in drifted:
                unit_1_cell = f"{unit_1_cell}{_DIVERGENCE_MARKER}"
            if unit_2_cell and 1 in drifted:
                unit_2_cell = f"{unit_2_cell}{_DIVERGENCE_MARKER}"
        # `log-file`: the reported log path, read off each side's node
        # via the same `log_path` accessor `crony logs` uses. Dual-
        # source, so a not-yet-applied rename flags `^`. An orphan row
        # (no graph node) reports its remnant's path on the current
        # side; a broken row with no recoverable name reports nothing.
        pending_log = (
            str(pending_node.log_path) if pending_node is not None else None
        )
        current_log: str | None = None
        if current_node is not None:
            current_log = str(current_node.log_path)
        elif row_ref is not None and row_ref in config.orphans:
            current_log = str(config.orphans[row_ref].log_path)
        log_file_cell = _mark(pending_log, current_log)
        # FLAGS: per-flag resolved state read off each side's job node.
        # Each per-flag column is a dual-source true / false cell; the
        # `flags` summary lists the active source's enabled flags,
        # tagging each with `^` where the two sides disagree on it.
        pending_flags = _node_flags(pending_node)
        current_flags = _node_flags(current_node)
        if config_source == "current":
            active_flags = current_flags
        elif config_source == "pending":
            active_flags = pending_flags
        else:
            active_flags = (
                pending_flags if pending_flags is not None else current_flags
            )
        # `timeout` / `priority` are config fields read from the
        # snapshot; source-selected and `^`-flagged like the other
        # dual-source cells.
        timeout_cell = _mark(
            _timeout_display(pending_node), _timeout_display(current_node)
        )
        priority_cell = _mark(
            _priority_display(pending_node), _priority_display(current_node)
        )
        # The `stale` column summarizes why an entry reads stale -- the
        # snapshot fields that differ, including `unit-config-1` /
        # `unit-config-2` for an installed-unit drift.
        stale_cell = _stale_fields(pending_node, current_node)
        flag_cells: dict[str, str] = {}
        flags_summary_parts: list[str] = []
        for member in crony.config.JobFlags.members():
            pending_flag = (
                ("true" if member in pending_flags else "false")
                if pending_flags is not None
                else None
            )
            current_flag = (
                ("true" if member in current_flags else "false")
                if current_flags is not None
                else None
            )
            flag_cells[member.token] = _mark(pending_flag, current_flag)
            member_diverged = (
                pending_flags is not None
                and current_flags is not None
                and (member in pending_flags) != (member in current_flags)
            )
            if active_flags is not None and member in active_flags:
                token: str = member.token
                if member_diverged:
                    token = f"{token}{_DIVERGENCE_MARKER}"
                flags_summary_parts.append(token)
        row_cells: dict[str, str] = {
            _StatusCols.JOB: job_cell,
            _StatusCols.JOB_OR_UUID: job_or_uuid_cell,
            _StatusCols.KIND: kind,
            _StatusCols.CONFIG: cfg_status,
            _StatusCols.SCHEDULE: sched_cell,
            _StatusCols.GROUPS: groups_cell,
            _StatusCols.STATUS: job_status,
            _StatusCols.LAST_RAN: last_ran,
            _StatusCols.MASKED_BY: mask_reason,
            _StatusCols.UNIT_NAME: unit_name,
            _StatusCols.UUID: uuid_cell,
            _StatusCols.UNIT_CONFIG_1: unit_1_cell,
            _StatusCols.UNIT_CONFIG_2: unit_2_cell,
            _StatusCols.LOG_FILE: log_file_cell,
            _StatusCols.FLAGS: ",".join(flags_summary_parts),
            _StatusCols.TIMEOUT: timeout_cell,
            _StatusCols.PRIORITY: priority_cell,
            _StatusCols.STALE: stale_cell,
        }
        row_cells.update(flag_cells)
        # Every selectable column must produce a cell, and no row may
        # carry a cell for an undocumented column -- the registry and
        # the renderer stay in lockstep so `--cols` can never select a
        # column the row lacks (a KeyError at print time) or render one
        # the help reference omits.
        assert set(row_cells) == set(_StatusCols) | _FLAG_COL_TOKENS, (
            "`row_cells` keys must equal the selectable column set: "
            f"{sorted(map(str, row_cells))} != "
            f"{sorted(map(str, set(_StatusCols) | _FLAG_COL_TOKENS))}"
        )
        built.append((config_name, row_cells))

    for ref_key, candidate_names in names_by_ref.items():
        _build_row(ref_key, candidate_names)
    for full in nameless_rows:
        _build_row(None, [full])

    rows = [row for _, row in built]

    if exclude_healthy:
        # Filter to unhealthy rows and render flat -- no tree
        # indent, since the surviving rows won't reconstruct the
        # tree anyway and a half-indented subtree is more
        # confusing than a flat list. Healthy: `config == synced`
        # AND not disabled AND `status` in {ok, never, gated}. A
        # disabled entry is unhealthy because it isn't firing; its
        # SCHEDULE cell reads `disabled`. (A not-loaded unit is
        # already excluded by `config != synced` -- it reads broken.)
        healthy_status = {
            crony.model.JobStatus.OK,
            crony.model.JobStatus.NEVER,
            crony.model.JobStatus.GATED,
        }
        rows = [
            r
            for r in rows
            if not (
                r[_StatusCols.CONFIG] == crony.model.ConfigStatus.SYNCED
                and r[_StatusCols.SCHEDULE]
                != crony.model.ScheduleValue.DISABLED.value
                and r[_StatusCols.STATUS] in healthy_status
            )
        ]
        rows = sorted(rows, key=lambda r: r[_StatusCols.JOB_OR_UUID])
    else:
        # Order rows by tree DFS to surface execution order rather
        # than alphabetical: roots from each active target first, then
        # group children indented by depth, then off-tree rows
        # (orphans, CLI args outside any tree, masked entries not in
        # any active target) below in stable sorted order. The
        # identity cells carry two spaces of indent per depth level so
        # the visual nesting matches the dispatch graph. Tree placement
        # keys on each row's config (pending) name -- the structure
        # `_build_status_tree` walks -- even when the row displays its
        # applied name under --config-current.
        by_tree_key: dict[str, dict[str, str]] = {}
        for key, row in built:
            by_tree_key.setdefault(key, row)
        ordered: list[dict[str, str]] = []
        consumed: set[int] = set()
        for full in tree_order:
            tree_row = by_tree_key.get(full)
            if tree_row is None or id(tree_row) in consumed:
                continue
            consumed.add(id(tree_row))
            tree_row = dict(tree_row)
            indent = "  " * tree_depth[full]
            tree_row[_StatusCols.JOB_OR_UUID] = (
                indent + tree_row[_StatusCols.JOB_OR_UUID]
            )
            if tree_row[_StatusCols.JOB]:
                tree_row[_StatusCols.JOB] = indent + tree_row[_StatusCols.JOB]
            ordered.append(tree_row)
        off_tree = [row for _, row in built if id(row) not in consumed]
        for row in sorted(off_tree, key=lambda r: r[_StatusCols.JOB_OR_UUID]):
            ordered.append(row)
        rows = ordered

    # Deferred until the displayed rows exist: the `all` alias's
    # masked-by and unit-config-2 trims key on whether any shown row
    # carries a masked reason / a second unit, which only the built rows
    # can answer.
    masked_present = any(row[_StatusCols.MASKED_BY] for row in rows)
    second_unit_present = any(row[_StatusCols.UNIT_CONFIG_2] for row in rows)
    selected_cols = _parse_status_cols(
        cols,
        masked_present=masked_present,
        second_unit_present=second_unit_present,
    )

    use_color = _color_supported()

    # Per-column width: max of the header label and the longest
    # displayed cell, so a long "last-ran" value (`12d ago`) doesn't get
    # squeezed by the 8-char header. A color stream drops the `^` marker
    # (color carries the staleness), so its width is measured without it.
    def _displayed(value: str) -> str:
        return value.replace(_DIVERGENCE_MARKER, "") if use_color else value

    widths: dict[str, int] = {}
    for col in selected_cols:
        header = _STATUS_COL_HEADERS[col]
        cell_max = max((len(_displayed(r[col])) for r in rows), default=0)
        widths[col] = max(len(header), cell_max)

    # Two-space separator: a single space ran headers and cells
    # together at narrow column widths (e.g. CONFIG=6 vs STATUS=6
    # produced columns visually flush against each other).
    sep = "  "
    header_line = sep.join(
        f"{_STATUS_COL_HEADERS[c]:<{widths[c]}}" for c in selected_cols
    )
    print(header_line.rstrip())
    for row in rows:
        line = sep.join(
            _render_status_cell(c, row[c], widths[c], use_color)
            for c in selected_cols
        )
        print(line.rstrip())
    # The `^` marker and this legend are the plain-text staleness signal;
    # a color stream shows staleness by color instead, so the footer
    # prints only when not coloring and a displayed cell carries `^`.
    # The marker can sit mid-cell -- the `flags` summary tags individual
    # flags (e.g. `interactive^,keep-awake`) -- so test for it anywhere.
    if not use_color and any(
        _DIVERGENCE_MARKER in row[c] for row in rows for c in selected_cols
    ):
        print()
        print(_STALE_VALUE_FOOTER)


def do_logs(
    job: str,
    n: int | None,
    since: datetime.datetime | None,
    tail: bool,
    path: bool,
    latest: bool,
) -> None:
    """Print a job's recent log output.

    `job` is the full namespaced name (`<bundle>.<short>`); bare
    input is shorthand for `default.<short>`. Also accepts the
    `<bundle>:<UUID>` ref form for entities with no recoverable
    name (corrupt snapshot, broken entry) so the operator can
    paste a JOB cell from a status row directly.

    -p/--path   print the log file's path and exit (no content read).
                Path resolution is purely structural -- prints even
                when the file doesn't exist yet (e.g. an entry that
                hasn't been applied), so it composes with other
                tools: `$EDITOR "$(crony logs j -p)"`.
    -l/--latest print only the most recent run's entry (from the
                last `=== ... ===` header to EOF). Overrides -n /
                --since; mutually exclusive with --tail.
    -n N        print the last N lines (default 200, or 10 with
                `--tail`). With `--tail`, controls how many lines
                of history are shown before live-tailing begins;
                `-n 0` skips the history.
    -s/--since X
                parse X as a duration ('1h', '2d') or ISO timestamp
                and skip log content older than that. Run-header
                timestamps anchor the cut.
    -t/--tail   show the last `-n` lines (default 10) and then
                follow appended output (-f semantics) until
                interrupted.
    """
    full = crony.config.normalize_full_name(job)
    try:
        config = crony.runtime.load_config()
    except crony.errors.ConfigError:
        config = None
    sd: Path | None = None
    current_node: crony.model.Job | crony.model.JobGroup | None = None
    if config is not None:
        # Logs: prefer the current ref (where the run.log actually
        # lives), fall back to pending so `--path` mode prints the
        # canonical location for a pre-apply entry, and finally
        # fall back to a ref-form input's parsed ref so an
        # operator can read a state dir that isn't in any Config
        # source (e.g. a snapshot-less remnant the user wants to
        # inspect before destroying).
        ref = (
            config.resolve_current(full)
            or config.resolve_pending(full)
            or crony.unit.EntityRef.from_str(full)
        )
        if ref is not None:
            # Derive the state dir directly from the ref. This
            # path is what `RuntimeState.state_dir` would
            # contain when an entry is in `config.runtime`, and
            # it's also defined for refs that aren't
            # (`<bundle>:<UUID>` input whose snapshot is
            # unparseable, pre-apply entries, broken / future
            # orphans). The `log_path.exists()` check
            # below handles "path is well-defined but no log
            # there yet".
            sd = crony.model.Job.state_dir_from_ref(ref)
            current_node = config.current.job_from_ref(ref)
    if sd is None:
        raise crony.errors.UsageError(
            f"no log for {full!r} (no applied state on this host)"
        )
    log_path = sd / crony.model.RUN_LOG_NAME
    if path:
        # The reported path comes from the applied (current) node, so
        # it shows the short-name alias when the on-disk link resolves
        # and the uuid path otherwise -- always a valid on-disk
        # location. A pre-apply / ref-form / broken entry has no
        # current node, so it reports the uuid path directly.
        print(current_node.log_path if current_node is not None else log_path)
        return
    if not log_path.exists():
        raise crony.errors.UsageError(f"no log for {full!r} (state dir: {sd})")
    if n is None:
        n = 10 if tail else 200
    if tail:
        _follow_log(log_path, n=n)
        return
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if latest:
        # `--latest` is a single-run readout; it overrides -n /
        # --since since the entry is bounded by run-header
        # markers, not line count or wallclock.
        text = crony.notify.extract_latest_log_entry(text)
    else:
        if since is not None:
            text = _filter_since(text, since)
        if n > 0:
            lines = text.splitlines()
            if len(lines) > n:
                text = "\n".join(lines[-n:]) + "\n"
    sys.stdout.write(text)
    sys.stdout.flush()


def _follow_log(log_path: Path, *, n: int = 0) -> None:
    """tail -f equivalent on the log file.

    Prints the last `n` lines of the file before entering the
    follow loop (so `crony logs -t` mirrors `tail -f`'s "show
    history then live-tail" behavior); pass `n=0` to skip the
    history and start with new content only.

    Returns cleanly on Ctrl-C and on a closed downstream pipe so
    `crony logs -t | head` and an interactive interrupt both
    terminate without a stack trace.
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            if n > 0:
                # Print the last `n` lines, then continue from
                # wherever those landed in the file. Using
                # readlines() over f then slicing is fine at the
                # log sizes crony produces; a streaming
                # reverse-read would only matter for very large
                # files.
                lines = f.readlines()
                tail = lines[-n:] if len(lines) > n else lines
                try:
                    sys.stdout.writelines(tail)
                    sys.stdout.flush()
                except BrokenPipeError:
                    return
            else:
                f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                try:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                except BrokenPipeError:
                    return
    except KeyboardInterrupt:
        return


_DURATION_RE = re.compile(r"^(\d+)([smhd])$")


def parse_since_arg(value: str) -> datetime.datetime:
    """argparse `type=` for `logs --since`: duration shorthand or ISO.

    Returns a tz-aware datetime. ISO inputs without an offset are
    rejected -- comparing them against the runner's tz-aware run-header
    timestamps would raise TypeError mid-filter, so the offset is
    required at parse time.
    """
    text = value.strip()
    m = _DURATION_RE.fullmatch(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = datetime.timedelta(
            seconds=n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        )
        return datetime.datetime.now(datetime.UTC).astimezone() - delta
    try:
        ts = datetime.datetime.fromisoformat(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"unparseable value {value!r} (use NUMs/m/h/d or ISO timestamp)"
        ) from e
    if ts.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"{value!r} is missing a timezone offset; "
            f"use a form like 2026-04-01T12:00:00-07:00"
        )
    return ts


def _filter_since(text: str, cutoff: datetime.datetime) -> str:
    """Drop log content older than --since by run-header timestamp."""
    header_re = re.compile(r"^=== (\S+) ")
    keep_from: int | None = None
    lines = text.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        m = header_re.match(line)
        if not m:
            continue
        try:
            ts = datetime.datetime.fromisoformat(m.group(1))
        except ValueError:
            continue
        if ts >= cutoff:
            keep_from = idx
            break
    if keep_from is None:
        return ""
    return "".join(lines[keep_from:])


def _secret_warning(
    label: str,
    *,
    keychain_service: str | None,
    keychain_account: str | None,
    file_path: str | None,
) -> str:
    """Build a precise validate warning for an unresolved secret.

    Distinguishes "no source configured" from "configured source(s)
    resolved empty" so a misconfigured keychain item or missing file
    isn't misreported as "you forgot to set anything".
    """
    if keychain_service is None and file_path is None:
        return (
            f"{label} unresolved: no source configured (set keychain or file)"
        )
    parts: list[str] = []
    if keychain_service is not None:
        ident = (
            f"{keychain_service!r}/{keychain_account!r}"
            if keychain_account is not None
            else f"{keychain_service!r}"
        )
        parts.append(f"keychain item {ident}")
    if file_path is not None:
        parts.append(f"file {file_path!r}")
    return f"{label} unresolved: tried {' and '.join(parts)}"


def _validate_bundle_warnings(
    bundle: crony.config.TomlBundle,
) -> list[str]:
    """Return per-bundle warnings: defined channels whose secrets
    don't resolve, channels defined but never referenced, etc."""
    warnings: list[str] = []
    config = bundle.config

    # Walk every layer that might select channels and confirm any
    # we'll actually try to send through has its secrets resolvable.
    listed: set[str] = set(config.defaults.notify_channels)
    for j in config.jobs.values():
        if j.notify_channels is not None:
            listed.update(j.notify_channels)
    all_targets = list(config.platform_targets.values()) + list(
        config.host_targets.values()
    )
    if config.all_target is not None:
        all_targets.append(config.all_target)
    for t in all_targets:
        if t.notify_channels is not None:
            listed.update(t.notify_channels)

    for ch_name in sorted(listed):
        if ch_name == crony.config.NOTIFY_INHERIT_TOKEN:
            # The inherit sentinel resolves to the default bundle's
            # channels; their secrets are checked when that bundle is
            # validated, not here.
            continue
        if ch_name in crony.config.BUILTIN_NOTIFY_CHANNELS:
            # Zero-config built-in (e.g. dialog-popup): no block and no
            # secrets to resolve, so there is nothing to warn about.
            continue
        channel = config.defaults.notify_channel_defs.get(ch_name)
        if channel is None:
            # Cross-cutting validation already raises on this; be
            # defensive in case a future caller bypasses it.
            warnings.append(
                f"channel {ch_name!r} listed but not defined in "
                f"[defaults.notify.{ch_name}]"
            )
            continue
        if channel.transport == "email":
            assert channel.email is not None
            label = f"channel {ch_name!r}: SMTP password"
            try:
                secret = crony.notify.retrieve_secret(
                    keychain_service=channel.email.smtp_pass_keychain_service,
                    keychain_account=channel.email.smtp_pass_keychain_account,
                    file_path=channel.email.smtp_pass_file,
                )
                if secret is None:
                    warnings.append(
                        _secret_warning(
                            label,
                            keychain_service=(
                                channel.email.smtp_pass_keychain_service
                            ),
                            keychain_account=(
                                channel.email.smtp_pass_keychain_account
                            ),
                            file_path=channel.email.smtp_pass_file,
                        )
                    )
            except crony.errors.PreconditionError as e:
                warnings.append(str(e))
        elif channel.transport == "ntfy":
            assert channel.ntfy is not None
            label = f"channel {ch_name!r}: ntfy token"
            try:
                token = crony.notify.retrieve_secret(
                    keychain_service=channel.ntfy.token_keychain_service,
                    keychain_account=channel.ntfy.token_keychain_account,
                    file_path=channel.ntfy.token_file,
                )
                if token is None:
                    warnings.append(
                        _secret_warning(
                            label,
                            keychain_service=(
                                channel.ntfy.token_keychain_service
                            ),
                            keychain_account=(
                                channel.ntfy.token_keychain_account
                            ),
                            file_path=channel.ntfy.token_file,
                        )
                    )
            except crony.errors.PreconditionError as e:
                warnings.append(str(e))

    # Defined-but-unreferenced channels: not an error (may be staged
    # for future use), but worth flagging.
    defined = set(config.defaults.notify_channel_defs.keys())
    unused = defined - listed
    for ch_name in sorted(unused):
        warnings.append(
            f"channel {ch_name!r} is defined in "
            f"[defaults.notify.{ch_name}] but never referenced "
            f"by any notify_channels list"
        )

    if not config.jobs and not config.job_groups:
        warnings.append(
            "bundle defines no jobs or groups (nothing it contains can fire)"
        )
    return warnings


def _validate_file(path: Path) -> None:
    """Structurally validate a single TOML file as a bundle.

    The bundle name is derived exactly as the loader would for this
    path: the installed default config file (`crony.paths.CONFIG_FILE`)
    is the `default` bundle, any other file is named after its stem (so
    `borgadm.toml` validates as bundle `borgadm`). That keeps the
    default-bundle-only rules (e.g. no notify-inherit sentinel) and
    the non-default inherit semantics aligned with what `apply` will
    later enforce. Parses the file and runs the per-entity /
    cross-cutting schema checks, but does NOT touch the installed
    config dir or run the secret-resolution / linger checks: a
    not-yet-installed file has no host context, and an inheriting
    bundle's channels live in the default bundle, which a single file
    can't see. Lets a tool (or user) pre-flight a generated bundle
    before installing it. Raises ConfigError (exit CONFIG) on any
    error; prints `ok` and returns when clean. A clean file that still
    uses a deprecated-but-supported spelling (legacy underscore keys, or
    the flat `[target.<platform>]` target section) additionally prints a
    deprecation warning per affected spelling and exits WARNING.
    """
    if not path.exists():
        raise crony.errors.ConfigError(f"config not found: {path}")
    if path.resolve() == crony.paths.CONFIG_FILE.resolve():
        name = crony.config.DEFAULT_BUNDLE_NAME
    else:
        name = path.stem
    crony.config.validate_bundle_name(name, str(path))
    # TomlBundle.load logs each per-entity error and raises on a
    # bundle-level structural failure; the errored_* counts catch the
    # per-entity cases so this exits non-zero on either.
    bundle = crony.config.TomlBundle.load(name, path)
    cfg = bundle.config
    errored = (
        len(cfg.errored_jobs)
        + len(cfg.errored_job_groups)
        + len(cfg.errored_platform_targets)
        + len(cfg.errored_host_targets)
        + (1 if cfg.errored_all_target else 0)
    )
    if errored:
        raise crony.errors.ConfigError(
            f"{path}: {errored} invalid config "
            f"{'entry' if errored == 1 else 'entries'}"
        )
    print(f"ok: {path} validates as bundle {name!r}")
    deprecations: list[str] = []
    if cfg.legacy_underscore_keys:
        deprecations.append(_legacy_keys_warning(cfg.legacy_underscore_keys))
    if cfg.legacy_platform_targets:
        deprecations.append(
            _legacy_platform_target_warning(cfg.legacy_platform_targets)
        )
    if deprecations:
        print("warnings:")
        for msg in deprecations:
            print(f"  - {path}: {msg}")
        # WARNING is not raisable as a CronyError; SystemExit forwards
        # the code through cli() unchanged.
        raise SystemExit(int(crony.errors.ExitCode.WARNING))


def _legacy_keys_warning(keys: list[str]) -> str:
    """One-line deprecation notice for a config file still using
    underscore-spelled keys. Dashes are the canonical spelling; the
    underscore form still parses but is being phased out."""
    return (
        f"legacy underscore-spelled config key(s) {', '.join(keys)}; dashes "
        f"are the canonical spelling (e.g. 'keep-awake') -- update to silence"
    )


def _legacy_platform_target_warning(names: set[str]) -> str:
    """One-line deprecation notice for a config file still using the
    legacy flat `[target.<platform>]` spelling. The nested
    `[target.platform.<platform>]` form is canonical; the flat form
    still parses but is being phased out."""
    joined = ", ".join(sorted(names))
    return (
        f"legacy flat platform target section(s) [target.{joined}]; "
        f"[target.platform.<platform>] is the canonical spelling "
        f"(e.g. [target.platform.{sorted(names)[0]}]) -- update to silence"
    )


def do_validate(bundle: str | None, file: str | None) -> None:
    """Lint configs; report linger status and broken secret files.

    TomlConfig.load_all already enforces per-bundle structural rules
    and isolates failed bundles. This subcommand surfaces linger /
    per-bundle warnings as informational output and exits WARNING
    (1) when any are present, CONFIG (3) when no bundles load. A bundle
    still using a deprecated-but-supported spelling draws a deprecation
    warning: one naming its legacy underscore keys (the dash spelling is
    canonical), and one for the flat `[target.<platform>]` target
    section (the nested `[target.platform.<platform>]` form is
    canonical).
    `validate` looks only at the parsed config -- it never inspects
    crony's applied / on-disk state. (Use `crony status` or
    `crony destroy --orphans` to find and clean installed remnants
    no config selects.)

    With --bundle <name>, restricts the per-bundle warnings to just
    that bundle. The host-wide linger-status check only runs in the
    unfiltered case -- it's about the whole-host picture, not any
    one bundle.

    With --file <path> (mutually exclusive with --bundle), validates
    that single file in isolation rather than the installed config
    dir -- see `_validate_file`.
    """
    if file is not None:
        _validate_file(Path(file))
        return
    bundles = crony.config.TomlConfig.load_all()
    bundles.require_known(bundle)

    warnings: list[str] = []
    if bundle is None:
        try:
            crony.runtime.scheduler().verify()
        except crony.platform.SchedulerWarning as warn:
            warnings.append(str(warn))
        keep_awake_warn = _keep_awake_warning(crony.runtime.load_config())
        if keep_awake_warn is not None:
            warnings.append(keep_awake_warn)

    target_bundles = (
        [b for b in bundles.bundles if b.name == bundle]
        if bundle is not None
        else list(bundles.bundles)
    )
    for b in target_bundles:
        # Per-entity validation failures (errored jobs / groups /
        # targets) have to flip the validate exit code -- a CI
        # gate that runs `crony config validate` won't notice a broken
        # bundle entry otherwise. The per-entity error message is
        # already prefixed with its section header, so emit it
        # verbatim alongside `_validate_bundle_warnings` output.
        config = b.config
        for msg in sorted(config.errored_jobs.values()):
            warnings.append(f"{b.source}: {msg}")
        for msg in sorted(config.errored_job_groups.values()):
            warnings.append(f"{b.source}: {msg}")
        for msg in sorted(config.errored_platform_targets.values()):
            warnings.append(f"{b.source}: {msg}")
        for msg in sorted(config.errored_host_targets.values()):
            warnings.append(f"{b.source}: {msg}")
        if config.errored_all_target:
            warnings.append(f"{b.source}: {config.errored_all_target}")
        for w in _validate_bundle_warnings(b):
            warnings.append(f"{b.source}: {w}")
        if config.legacy_underscore_keys:
            warnings.append(
                f"{b.source}: "
                f"{_legacy_keys_warning(config.legacy_underscore_keys)}"
            )
        if config.legacy_platform_targets:
            plat_warn = _legacy_platform_target_warning(
                config.legacy_platform_targets
            )
            warnings.append(f"{b.source}: {plat_warn}")

    print(f"bundles loaded: {len(target_bundles)}")
    for b in target_bundles:
        config = b.config
        errored = (
            len(config.errored_jobs)
            + len(config.errored_job_groups)
            + len(config.errored_platform_targets)
            + len(config.errored_host_targets)
            + (1 if config.errored_all_target else 0)
        )
        errored_suffix = f", errored={errored}" if errored else ""
        print(
            f"  {b.name} ({b.source}): "
            f"jobs={len(config.jobs)}, groups={len(config.job_groups)}, "
            f"targets: platform={len(config.platform_targets)} "
            f"host={len(config.host_targets)} "
            f"all={1 if config.all_target else 0}{errored_suffix}"
        )
    if warnings:
        print("warnings:")
        for w in warnings:
            print(f"  - {w}")
        # WARNING is not raisable as a CronyError; SystemExit forwards
        # the code through cli() unchanged.
        raise SystemExit(int(crony.errors.ExitCode.WARNING))
    print("ok")


def _notify_test_one_bundle(
    bundle: crony.config.TomlBundle,
    channel: str | None,
    bundles: crony.config.TomlConfig,
) -> tuple[
    list[str], list[tuple[str, crony.model.NotificationResult]], str | None
]:
    """Run notify-test against one bundle.

    Returns (successes, failures, inherited_from). `inherited_from` is
    the bundle whose notify config was actually used -- the default
    bundle -- when this bundle inherits it, or None when the bundle
    sends through its own channels. The caller uses it to attribute an
    inherited channel to where it is defined rather than to the bundle
    under test (which has no definition of its own).
    """
    config = bundle.config
    # Synthetic transient TomlJob built only to feed
    # `resolved_notify_channels`. The uuid is never persisted and
    # never compared against any real entry, so the all-zeros
    # placeholder is fine.
    synthetic_job = crony.config.TomlJob(
        name="notify-test",
        uuid="00000000-0000-0000-0000-000000000000",
        command="true",
    )
    target = config.resolve_target()
    resolved = config.resolved_notify_channels(target, synthetic_job)
    # `eff_defaults` carries the channel defs + attach settings used
    # for dispatch; a bundle that inherits sends through the default
    # bundle's definitions, so an explicit --channel resolves there
    # too.
    resolved, eff_defaults = crony.notify.expand_notify_inherit(
        resolved, bundle.name, bundles, config.defaults
    )
    # `expand_notify_inherit` swaps in the default bundle's Defaults
    # object only when it actually expands the inherit sentinel, so an
    # identity change means the channels (and their definitions) came
    # from the default bundle.
    inherited_from = (
        crony.config.DEFAULT_BUNDLE_NAME
        if eff_defaults is not config.defaults
        else None
    )
    use_channels = [channel] if channel is not None else resolved
    if not use_channels:
        return ([], [], inherited_from)
    result = crony.model.JobRunResult(
        host=crony.platform.current_host(),
        platform=crony.platform.current_platform(),
        started_at=crony.runtime.now_iso(),
        ended_at=crony.runtime.now_iso(),
        duration_sec=0.0,
        exit_class=crony.model.ExitClass.FAIL,
        exit_code=1,
        signal=None,
        process_exit=1,
        gate=crony.model.GateResult.NONE,
        log_path="(synthetic)",
        notifications={
            ch: crony.model.NotificationResult(sent=False)
            for ch in use_channels
        },
    )
    log_text = (
        f"synthetic test message from `crony notify-test` "
        f"(bundle: {bundle.name}).\n"
        "if you can read this, the channel is working end-to-end.\n"
    )
    crony.notify.dispatch_notify(
        result, f"{bundle.name}.notify-test", log_text, eff_defaults
    )
    successes = [ch for ch, nr in result.notifications.items() if nr.sent]
    failures = [
        (ch, nr) for ch, nr in result.notifications.items() if not nr.sent
    ]
    return (successes, failures, inherited_from)


def do_notify_test(channel: str | None, bundle: str | None) -> None:
    """Send a synthetic failure notification.

    TomlBundle / channel resolution follows crony's bare-input rule: when
    nothing is specified, only the default bundle is exercised (never
    a fan-out across every bundle).

    --bundle <name>      send only through that bundle's configured
                         channels.
    --channel <ch>       a bare channel name resolves against the
                         selected bundle (default if --bundle is
                         absent), or against the default bundle's
                         channels when the selected bundle inherits.
    --channel <bn>.<ch>  fully qualified; the bundle prefix wins,
                         and is required to match --bundle when both
                         are given.

    Verifies plumbing end-to-end without scheduling a real job. The
    exit code reflects whether every per-channel send succeeded: any
    per-channel failure surfaces as CONFIG (config-shaped) or ERROR
    (transport-shaped), preserving the original failure category.
    """
    bundles = crony.config.TomlConfig.load_all()

    # Resolve --channel into (bundle_from_channel, channel_or_None).
    channel_bundle: str | None = None
    channel_short: str | None = None
    if channel is not None:
        if "." in channel:
            channel_bundle, channel_short = crony.config.parse_full_name(
                channel
            )
        else:
            channel_short = channel

    # Compose --bundle and --channel's bundle prefix. The parser
    # rejects a fully-qualified --channel whose bundle contradicts an
    # explicit --bundle, so at most one of the two is set here.
    bundle_name = channel_bundle or bundle or crony.config.DEFAULT_BUNDLE_NAME
    bundles.require_known(bundle_name)
    target_bundle = bundles.by_name(bundle_name)
    assert target_bundle is not None  # require_known guarantees

    successes, failures, inherited_from = _notify_test_one_bundle(
        target_bundle, channel_short, bundles
    )
    # An inherited channel is defined in (and dispatched through) the
    # default bundle, not the bundle under test; surface that so the
    # reader knows where the channel actually lives.
    origin = f" (inherited from {inherited_from})" if inherited_from else ""
    for ch in successes:
        logger.info(
            "notification sent via %s.%s%s", target_bundle.name, ch, origin
        )
    if not successes and not failures:
        logger.info("no notify channels configured; nothing to send")
        return
    if not failures:
        return
    all_failures: list[tuple[str, str, crony.model.NotificationResult]] = [
        (target_bundle.name, ch, nr) for ch, nr in failures
    ]
    # Preserve the failure category: if every failure is config-shaped
    # surface CONFIG (3); otherwise ERROR (4). A mix of config and
    # transport failures gets ERROR -- the configurational issues
    # would block transport anyway.
    all_config = all(
        nr.error_class == "ConfigError" for _, _, nr in all_failures
    )
    detail = "; ".join(
        f"{bn}.{ch}{origin}: {nr.error}" for bn, ch, nr in all_failures
    )
    if all_config:
        raise crony.errors.ConfigError(f"notify-test failed: {detail}")
    raise crony.errors.CronyError(f"notify-test failed: {detail}")
