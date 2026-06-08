# This is AI generated code

"""crony's runtime layer above the pure domain model.

Builds the whole-process Config from disk (parse every bundle, scan the
state-dir tree once, assemble the pending + current graphs and their
RuntimeState), loads and schema-checks applied snapshots, writes
last-run records, holds the run-lock, and answers scheduler queries
(install paths, drift, enable state) through the per-host platform
backend. All disk reads, locks, and scheduler queries the pure
crony.model deliberately omits live here.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import crony.config
import crony.errors
import crony.model
import crony.paths
import crony.platform
import crony.unit


def load_snapshot(
    ref: crony.unit.EntityRef,
) -> crony.model.Job | crony.model.JobGroup:
    """Load and validate a snapshot file by ref. Raises
    PreconditionError if missing (entry not applied) or schema
    mismatch (re-apply required).

    The platform unit's argv carries `bundle:uuid` so the runner
    addresses the snapshot directly via `entity_state_dir`
    without scanning the bundle's state dirs. The error messages
    use the recovered `name` field when available so the operator
    sees the human-readable identity.
    """
    state_dir = crony.model.entity_state_dir(ref)
    ref_str = str(ref)
    p = state_dir / "snapshot.json"
    if not p.is_file():
        raise crony.errors.PreconditionError(
            f"no snapshot for {ref_str} (run `crony apply` first; "
            f"if state exists on disk, ensure {p} is parseable)"
        )
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise crony.errors.PreconditionError(
            f"snapshot at {p} is unreadable: {e}"
        ) from e
    name_hint = raw.get("name") if isinstance(raw, dict) else None
    display = name_hint if isinstance(name_hint, str) else ref_str
    schema = raw.get("schema") if isinstance(raw, dict) else None
    if schema != crony.model.SNAPSHOT_SCHEMA:
        raise crony.errors.PreconditionError(
            f"snapshot for {display!r} has schema {schema!r}, "
            f"expected {crony.model.SNAPSHOT_SCHEMA} (re-apply required)"
        )
    # A schema-matched but otherwise malformed snapshot (extra /
    # missing fields, a bad schedule / interval string, or an unknown
    # kind) raises TypeError / ValueError from snapshot_from_dict.
    # `_build_current_graph` already treats that as a broken entity;
    # mirror it here so the runner records a `canceled` last-run via
    # its PreconditionError handler instead of crashing with a
    # traceback the scheduler never sees.
    try:
        return crony.model.snapshot_from_dict(raw)
    except (TypeError, ValueError) as e:
        raise crony.errors.PreconditionError(
            f"snapshot for {display!r} has malformed fields: {e} "
            f"(re-apply required)"
        ) from e


def _read_runtime_state(
    state_dir: Path,
    *,
    full_name: str | None,
    snapshot: crony.model.Job | crony.model.JobGroup | None = None,
) -> crony.model.RuntimeState:
    """Snapshot the runtime-only state inside one state dir: the
    parsed last-run record and presence of the lock / pending /
    user-trigger flag files. When `full_name` is known, also probe
    the platform unit file path so subcommands can read it from
    RuntimeState instead of walking the unit dirs ad-hoc.
    `full_name` is None only for a broken entry whose snapshot
    didn't yield a recoverable name.

    `snapshot` is the parsed `Job` / `JobGroup` for entries in
    `Config.current`; when supplied (with a known `full_name`),
    the unit-install integrity check runs and `unit_is_stale`
    reflects the result. Left None for broken refs (no snapshot)
    and unit-only refs (no state dir to read from).
    """
    last_run: crony.model.LastRun | None = None
    last_run_path = state_dir / "last-run.json"
    if last_run_path.is_file():
        try:
            raw = json.loads(last_run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, dict):
            last_run = crony.model.LastRun.from_raw(raw)

    is_running = False
    lock_path = state_dir / "run.lock"
    if lock_path.is_file():
        try:
            with acquire_lock(lock_path):
                pass
        except crony.errors.LockBusyError:
            is_running = True

    unit_config: Path | None = None
    unit_timer: Path | None = None
    unit_is_stale = False
    if full_name is not None:
        unit_config = _platform_unit_config_path(full_name)
        unit_timer = _platform_unit_timer_path(full_name)
        if snapshot is not None:
            unit_is_stale = _unit_is_stale(snapshot)

    return crony.model.RuntimeState(
        state_dir=state_dir,
        last_run=last_run,
        is_running=is_running,
        is_pending=(state_dir / "pending.flag").is_file(),
        has_user_trigger_flag=(state_dir / "user-trigger.flag").is_file(),
        unit_config=unit_config,
        unit_timer=unit_timer,
        unit_is_stale=unit_is_stale,
    )


def _build_current_graph(
    state_root: Path,
) -> tuple[
    crony.model.Graph,
    dict[crony.unit.EntityRef, crony.model.JobOrphan],
    dict[crony.unit.EntityRef, crony.model.RuntimeState],
]:
    """Scan `STATE_DIR/<bundle>/<uuid>/` once. Every uuid-keyed dir
    with a parseable schema-matched `snapshot.json` becomes a
    current-graph node; the rest of the dir contents become its
    `RuntimeState`. Snapshots that exist but can't be turned into
    a `Job` / `JobGroup` (corrupt JSON, wrong schema, unrecognized
    kind, dataclass `TypeError`) become broken `JobOrphan` records
    (`reason` set) so status / destroy can surface them with a clear
    "re-apply required" signal instead of treating the entry as
    silently absent.
    """
    current = crony.model.Graph()
    broken: dict[crony.unit.EntityRef, crony.model.JobOrphan] = {}
    runtime: dict[crony.unit.EntityRef, crony.model.RuntimeState] = {}
    if not state_root.exists():
        return current, broken, runtime
    for bundle_dir in state_root.iterdir():
        if not bundle_dir.is_dir():
            continue
        for uuid_dir in bundle_dir.iterdir():
            # Skip the short-name alias symlinks that live alongside the
            # uuid dirs: `is_dir()` follows a symlink to its target, so
            # an alias would otherwise be scanned as a phantom uuid dir
            # keyed by the short name and double-load the same snapshot.
            if uuid_dir.is_symlink() or not uuid_dir.is_dir():
                continue
            snap_path = uuid_dir / "snapshot.json"
            if not snap_path.is_file():
                continue
            ref = crony.unit.EntityRef(bundle_dir.name, uuid_dir.name)
            try:
                raw = json.loads(snap_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                broken[ref] = crony.model.JobOrphan(
                    bundle=bundle_dir.name,
                    uuid=uuid_dir.name,
                    name=None,
                    reason=f"snapshot.json unreadable: {exc}",
                    source_path=snap_path,
                )
                continue
            raw_name = raw.get("name") if isinstance(raw, dict) else None
            name_hint = raw_name if isinstance(raw_name, str) else None
            if not isinstance(raw, dict):
                broken[ref] = crony.model.JobOrphan(
                    bundle=bundle_dir.name,
                    uuid=uuid_dir.name,
                    name=name_hint,
                    reason="snapshot.json is not a JSON object",
                    source_path=snap_path,
                )
                continue
            if raw.get("schema") != crony.model.SNAPSHOT_SCHEMA:
                broken[ref] = crony.model.JobOrphan(
                    bundle=bundle_dir.name,
                    uuid=uuid_dir.name,
                    name=name_hint,
                    reason=(
                        f"snapshot schema {raw.get('schema')!r} "
                        f"(expected {crony.model.SNAPSHOT_SCHEMA})"
                    ),
                    source_path=snap_path,
                )
                continue
            try:
                snap = crony.model.snapshot_from_dict(raw)
            except (TypeError, ValueError) as exc:
                broken[ref] = crony.model.JobOrphan(
                    bundle=bundle_dir.name,
                    uuid=uuid_dir.name,
                    name=name_hint,
                    reason=f"snapshot conversion failed: {exc}",
                    source_path=snap_path,
                )
                continue
            # Pin the alias as it actually is on disk so a missing /
            # mis-pointed link diverges from the config-built node's
            # expected pair, surfacing as drift through the snapshot
            # comparison (and steering `log_path` to the uuid path).
            snap.symlink = _read_symlink_pair(snap)
            full = str(snap.entity_name)
            if isinstance(snap, crony.model.Job):
                current.jobs[snap.entity_ref] = snap
                current.by_full_name[full] = snap.entity_ref
                runtime[snap.entity_ref] = _read_runtime_state(
                    uuid_dir,
                    full_name=full,
                    snapshot=snap,
                )
            else:
                current.groups[snap.entity_ref] = snap
                current.by_full_name[full] = snap.entity_ref
                runtime[snap.entity_ref] = _read_runtime_state(
                    uuid_dir,
                    full_name=full,
                    snapshot=snap,
                )
    # Broken entries get a runtime entry too -- the state dir
    # exists and carries the same per-run files (last-run.json /
    # run.lock / pending.flag / user-trigger.flag) as a normal
    # current entry, plus the platform unit path. Without this
    # the unit-config / last / last-ran columns render empty
    # for broken rows even when those files are on disk.
    for ref, broken_entry in broken.items():
        state_dir = crony.model.entity_state_dir(ref)
        runtime[ref] = _read_runtime_state(
            state_dir,
            full_name=broken_entry.name,
        )
    return current, broken, runtime


def load_config() -> crony.model.Config:
    """Build the whole-process Config: parse every bundle's TOML,
    walk the state-dir tree once, build pending + current graphs +
    runtime state in memory, and detect platform-unit-only orphans.
    """
    host = crony.platform.current_host()
    platform = crony.platform.current_platform()

    toml_config = crony.config.TomlConfig.load_all()
    pending = crony.model.Graph.build_pending(toml_config)
    current, broken, runtime = _build_current_graph(crony.paths.STATE_DIR)

    # Resolve same-name collisions among current entries. Two state
    # dirs can recover the same full name (uuid-edit residue that
    # escaped apply's cleanup, or hand-mucked state). The ref that
    # matches the live config keeps the plain name; the rest become
    # `shadowed` and surface by `<bundle>:<UUID>` in status. Without
    # this, `current.by_full_name` would silently drop all but the
    # last-scanned ref, hiding the residue.
    shadowed: set[crony.unit.EntityRef] = set()
    name_to_refs: dict[str, list[crony.unit.EntityRef]] = {}
    for ref in current.refs():
        snap = current.jobs.get(ref) or current.groups.get(ref)
        assert snap is not None  # ref came from current.refs()
        name_to_refs.setdefault(str(snap.entity_name), []).append(ref)
    pending_refs = pending.refs()
    for name, refs in name_to_refs.items():
        if len(refs) < 2:
            continue
        winner = next((r for r in refs if r in pending_refs), None)
        if winner is None:
            winner = min(refs, key=lambda r: r.uuid)
        current.by_full_name[name] = winner
        shadowed.update(r for r in refs if r != winner)

    # All on-disk junk lands in one `orphans` map keyed by ref: the
    # broken snapshots discovered above (real uuid, `reason` set), plus
    # the pure leftovers found next. `JobOrphan.is_broken` tells them
    # apart for status (`broken` vs `orphan`).
    orphans: dict[crony.unit.EntityRef, crony.model.JobOrphan] = dict(broken)
    orphans_by_full_name: dict[str, crony.unit.EntityRef] = {
        o.name: ref for ref, o in broken.items() if o.name is not None
    }

    # Names with a leftover platform unit file and / or short-name
    # alias symlink that no current or broken snapshot accounts for. A
    # rename leaves a stray alias with no unit; a state wipe leaves a
    # unit with no alias; some carry both. Synthesize a deterministic
    # `uuid5` keyed on the name so a name with both remnants resolves
    # to one stable EntityRef that `destroy` addresses through the same
    # machinery. The hash seed string is fixed for that stability and
    # is not a description.
    accounted_labels: set[str] = set(current.by_full_name) | set(
        orphans_by_full_name
    )
    unit_names = _platform_unit_names()
    alias_names = _alias_symlink_names()
    for full_name in (unit_names | alias_names) - accounted_labels:
        bundle_name, _, _ = full_name.partition(".")
        if not bundle_name:
            continue
        synthetic = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, f"crony.unit-only/{full_name}")
        )
        ref = crony.unit.EntityRef(bundle_name, synthetic)
        has_unit = full_name in unit_names
        orphans[ref] = crony.model.JobOrphan(
            bundle=bundle_name,
            uuid=synthetic,
            name=full_name,
            has_unit_file=has_unit,
            has_symlink=full_name in alias_names,
        )
        orphans_by_full_name[full_name] = ref
        # Surface the platform unit paths through RuntimeState (so
        # status's unit-config / unit-timer columns don't re-walk the
        # unit dirs); a symlink-only orphan has no unit file, so leave
        # them None.
        runtime[ref] = crony.model.RuntimeState(
            state_dir=crony.model.entity_state_dir(ref),
            last_run=None,
            is_running=False,
            is_pending=False,
            has_user_trigger_flag=False,
            unit_config=(
                _platform_unit_config_path(full_name) if has_unit else None
            ),
            unit_timer=(
                _platform_unit_timer_path(full_name) if has_unit else None
            ),
        )

    return crony.model.Config(
        toml_config=toml_config,
        pending=pending,
        current=current,
        orphans=orphans,
        orphans_by_full_name=orphans_by_full_name,
        runtime=runtime,
        host=host,
        platform=platform,
        shadowed=shadowed,
    )


def state_dir_for(node: crony.model.Job | crony.model.JobGroup) -> Path:
    """State directory for an entity, materialized on disk.

    Returns `node`'s uuid-keyed state dir, creating it (and the bundle
    subdir) when missing. Used by apply and the runner -- both write
    files under the returned path on every call, so a pre-existing
    empty dir is the right starting state.

    Also materializes an empty `run.log` when absent, so it exists
    from apply time onward -- an operator can `tail -f` it before
    the entity's first run instead of racing the runner to create
    it. Only created when missing, never truncated, so an existing
    log survives a re-apply untouched.
    """
    d = node.state_dir
    d.mkdir(parents=True, exist_ok=True)
    if not node.log_path_resolved.exists():
        node.log_path_resolved.touch()
    return d


def now_iso() -> str:
    """Current local time in ISO 8601 format with timezone offset."""
    return (
        datetime.datetime.now(datetime.UTC)
        .astimezone()
        .isoformat(timespec="seconds")
    )


@contextlib.contextmanager
def acquire_lock(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive non-blocking flock on lock_path for a run.

    The lock is a run-liveness signal: a held lock means a run is in
    flight, which `run_in_progress` (and status / apply / destroy
    through it) reads by probing whether the lock is acquirable. flock
    is used rather than the sibling run.pid because the kernel releases
    it when the runner dies, so a crashed run never reads as still
    running.

    Acquisition is non-blocking and raises LockBusyError when the lock
    is already held. In normal operation that does not happen: jobs run
    only via their platform unit, and a second fire of an
    already-running unit is coalesced into a no-op, so a given run.lock
    has a single contender. The raise is therefore incidental
    single-flight defense against a stray direct `crony run`, not the
    mechanism that prevents concurrent scheduled fires. The lock file is
    left in place across runs.
    """
    fd = open(lock_path, "w")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise crony.errors.LockBusyError(
                f"another instance of {lock_path.parent.name} is running"
            ) from e
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        fd.close()


def write_last_run(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write last-run.json so partial writes can't corrupt it.

    Insertion order is preserved (no `sort_keys=True`): top-level keys
    follow dataclass field order, and `notifications` keeps the
    configured channel order so the on-disk record matches what the
    user wrote in the toml.
    """
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def _platform_unit_config_path(
    name: str, platform: str | None = None
) -> Path | None:
    """The on-disk platform config unit file backing `name`, or None.
    The status `unit-config` column reads this from `RuntimeState`."""
    return scheduler(platform).unit_config_path(name)


def _platform_unit_timer_path(
    name: str, platform: str | None = None
) -> Path | None:
    """The on-disk timer unit arming `name`'s schedule, or None when the
    platform has no separate timer unit or the entry is unscheduled. The
    status `unit-timer` column reads this from `RuntimeState`."""
    return scheduler(platform).unit_timer_path(name)


def dispatch_unit_path(name: str, platform: str | None = None) -> Path:
    """File `trigger_unit` fires for `name` (may not exist). Used to
    refuse early when the unit was never installed."""
    return scheduler(platform).dispatch_unit_path(name)


def scheduler(platform: str | None = None) -> crony.platform.Scheduler:
    """The platform Scheduler for the host. `platform` defaults to the
    running host's `crony.platform.current_platform()`; tests pass it
    explicitly to exercise a specific backend. Built per call; the
    backend resolves its own unit directory (honoring its CRONY_*_DIR
    env override), so tests that redirect that env are picked up."""
    if platform is None:
        platform = crony.platform.current_platform()
    return crony.platform.get_scheduler(platform)


def host() -> crony.platform.HostPlatform:
    """The HostPlatform backend for the running host. Built per call via
    crony.platform.current_platform() so tests can redirect the platform."""
    return crony.platform.get_host(crony.platform.current_platform())


def _unit_is_stale(
    snap: crony.model.Job | crony.model.JobGroup, platform: str | None = None
) -> bool:
    """True when the platform install diverges from the snapshot --
    delegates to the scheduler's drift check."""
    return scheduler(platform).is_stale(snap.unit_spec())


def recover_full_name(state_dir: Path) -> str | None:
    """Return the full namespaced name a state dir was last
    applied under, or None when the snapshot is missing or
    unreadable.
    """
    snap_p = state_dir / "snapshot.json"
    if not snap_p.is_file():
        return None
    try:
        raw = json.loads(snap_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    name = raw.get("name")
    return name if isinstance(name, str) else None


def _read_symlink_pair(
    node: crony.model.Job | crony.model.JobGroup,
) -> tuple[Path, str] | None:
    """The on-disk alias pair for a node: (alias_path, target) when
    the alias is a symlink (a dangling one included), else None.
    `target` is the link's literal contents -- the bare uuid for an
    apply-created alias -- compared against the entry's uuid to tell a
    correct alias from a mis-pointed one."""
    link = node.symlink_state_dir
    if link.is_symlink():
        return (link, str(link.readlink()))
    return None


def run_in_progress(state_dir: Path) -> bool:
    """True when `state_dir`'s `run.lock` is held by another
    process -- a fire is in flight. False when the lock file is
    absent or freely acquirable.
    """
    lock_path = state_dir / "run.lock"
    if not lock_path.is_file():
        return False
    try:
        with acquire_lock(lock_path):
            return False
    except crony.errors.LockBusyError:
        return True


def _platform_unit_names() -> set[str]:
    """Names with a crony-managed platform unit file on this host.

    Walks the platform's user-unit directory and returns the name
    encoded in every crony-shaped filename -- including a stray whose
    name isn't a valid `<bundle>.<short>` (a hand-created or legacy
    unit), so `crony destroy` can still reach it. Used to discover
    entries whose unit landed on disk but whose state dir is missing
    -- e.g. a state wipe (rm -rf ~/.local/state/crony/) without
    `crony destroy` first. Without this, those units would be
    invisible to status / destroy and the user would have to track
    them down manually.
    """
    return scheduler().installed_names()


def _alias_symlink_names() -> set[str]:
    """The `<bundle>.<short>` of every short-name alias symlink under
    the state tree.

    The alias is the only symlink crony plants beside the uuid dirs,
    so any symlink child of a bundle dir is one. A dangling alias (its
    uuid dir gone) is still reported -- orphan cleanup reclaims the
    link by name. Used to discover aliases whose name no current /
    broken snapshot accounts for (a rename that left the old alias
    behind, or a hand-created one).
    """
    names: set[str] = set()
    root = crony.paths.STATE_DIR
    if not root.exists():
        return names
    for bundle_dir in root.iterdir():
        if not bundle_dir.is_dir():
            continue
        for child in bundle_dir.iterdir():
            if child.is_symlink():
                names.add(f"{bundle_dir.name}.{child.name}")
    return names


def unit_state(name: str, platform: str | None = None) -> str:
    """The platform scheduler's enable state for a stamped entity:
    `enabled`, `disabled`, or `none`.

    Distinct from CONFIG: a unit can be configured-and-stamped while
    being disabled at the platform scheduler. `none` means the
    scheduler doesn't know a unit by this name -- nothing instantiated
    to flip on or off. (The `grouped` UNIT-axis value, for an entry
    with no own unit to enable, is set by the status caller, not here.)
    """
    return scheduler(platform).state(name).value


def user_trigger_flag_path(state_dir: Path) -> Path:
    """Filesystem path of the one-shot user-trigger sentinel."""
    return state_dir / "user-trigger.flag"


def write_user_trigger_flag(state_dir: Path) -> None:
    """Write the user-trigger sentinel just before kicking the
    platform scheduler. Empty file -- presence is the signal. The
    caller passes the entry's uuid-keyed state dir so the sentinel
    lives alongside the other per-run files (run.lock, pending.flag,
    last-run.json).
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    user_trigger_flag_path(state_dir).write_bytes(b"")


def consume_user_trigger_flag(state_dir: Path) -> bool:
    """Read-and-delete the user-trigger sentinel. Returns True if
    the flag was present (the runner should bypass the interactive
    wait), False otherwise.
    """
    try:
        user_trigger_flag_path(state_dir).unlink()
    except FileNotFoundError:
        return False
    return True


def read_pid_file(pid_path: Path) -> int | None:
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    try:
        return int(text)
    except ValueError:
        return None
