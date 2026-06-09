# This is AI generated code

"""crony's runtime layer above the pure domain model.

Builds the whole-process Config from disk (parse every bundle, scan the
state-dir tree once, assemble the pending + current graphs and their
RuntimeState), loads and schema-checks applied snapshots, applies and
destroys individual entries (apply_one / destroy_one -- rendering,
installing, and removing platform units, alias symlinks, and state
dirs), writes last-run records, holds the run-lock, and answers
scheduler queries (install paths, drift, enable state) through the
per-host platform backend. All disk reads, mutations, locks, and
scheduler queries the pure crony.model deliberately omits live here.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import logging
import os
import shutil as shutil  # noqa: PLC0414  re-exported for tests
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

logger = logging.getLogger(__name__)


def load_snapshot(
    ref: crony.unit.EntityRef,
) -> crony.model.Job | crony.model.JobGroup:
    """Load and validate a snapshot file by ref. Raises
    PreconditionError if missing (entry not applied) or schema
    mismatch (re-apply required).

    The platform unit's argv carries `bundle:uuid` so the runner
    addresses the snapshot directly via `Job.state_dir_from_ref(ref)`
    without scanning the bundle's state dirs. The error messages
    use the recovered `name` field when available so the operator
    sees the human-readable identity.
    """
    state_dir = crony.model.Job.state_dir_from_ref(ref)
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
    last_exits: dict[str, crony.platform.UnitLastExit] | None = None,
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

    `last_exits` is the scheduler's bulk last-launch map (keyed by
    full name); the entry for `full_name`, if any, is stored so
    status can reconcile a killed-but-unrecorded launch against the
    parsed `last_run`.
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

    unit_last_exit = (
        last_exits.get(full_name)
        if last_exits is not None and full_name is not None
        else None
    )
    return crony.model.RuntimeState(
        state_dir=state_dir,
        last_run=last_run,
        is_running=is_running,
        is_pending=(state_dir / "pending.flag").is_file(),
        has_user_trigger_flag=(state_dir / "user-trigger.flag").is_file(),
        unit_config=unit_config,
        unit_timer=unit_timer,
        unit_is_stale=unit_is_stale,
        unit_last_exit=unit_last_exit,
    )


def _build_current_graph(
    state_root: Path,
    last_exits: dict[str, crony.platform.UnitLastExit],
) -> tuple[
    crony.model.Graph,
    dict[crony.unit.EntityRef, crony.model.JobOrphan],
    dict[crony.unit.EntityRef, crony.model.RuntimeState],
]:
    """Scan `STATE_DIR/<bundle>/<uuid>/` once. Every uuid-keyed dir
    with a parseable schema-matched `snapshot.json` becomes a
    current-graph node; the rest of the dir contents become its
    `RuntimeState`. Dirs that don't yield a node become `JobOrphan`
    records so status / destroy can surface them instead of treating
    the entry as silently absent: a snapshot that exists but can't be
    turned into a `Job` / `JobGroup` (corrupt JSON, wrong schema,
    unrecognized kind, dataclass `TypeError`) is recorded broken
    (`reason` set, "re-apply required"); a dir with no snapshot at all
    is recorded as a nameless, non-broken orphan (leftover junk).
    """
    current = crony.model.Graph()
    orphans: dict[crony.unit.EntityRef, crony.model.JobOrphan] = {}
    runtime: dict[crony.unit.EntityRef, crony.model.RuntimeState] = {}
    if not state_root.exists():
        return current, orphans, runtime
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
            ref = crony.unit.EntityRef(bundle_dir.name, uuid_dir.name)
            snap_path = uuid_dir / "snapshot.json"
            if not snap_path.is_file():
                # A uuid dir with no snapshot at all is leftover state
                # (an interrupted apply, a hand-wiped snapshot) that no
                # config models. Record it as a nameless, non-broken
                # orphan -- not "re-apply" territory (there is nothing
                # to re-render), just junk a sweep or a ref-form destroy
                # reclaims.
                orphans[ref] = crony.model.JobOrphan(
                    bundle=bundle_dir.name,
                    uuid=uuid_dir.name,
                    name=None,
                )
                continue
            try:
                raw = json.loads(snap_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                orphans[ref] = crony.model.JobOrphan(
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
                orphans[ref] = crony.model.JobOrphan(
                    bundle=bundle_dir.name,
                    uuid=uuid_dir.name,
                    name=name_hint,
                    reason="snapshot.json is not a JSON object",
                    source_path=snap_path,
                )
                continue
            if raw.get("schema") != crony.model.SNAPSHOT_SCHEMA:
                orphans[ref] = crony.model.JobOrphan(
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
            # Read the alias as it is on disk and pin it at construction
            # so the frozen node carries the actual pair -- a missing /
            # mis-pointed link then diverges from the config-built
            # node's expected pair, surfacing as drift through the
            # snapshot comparison (and steering `log_path` to the uuid
            # path). A malformed name raises here and is treated as a
            # broken snapshot, same as a malformed body.
            try:
                en = (
                    crony.unit.EntityName.from_str(name_hint)
                    if isinstance(name_hint, str)
                    else None
                )
                alias = (
                    crony.model.Job.symlink_state_dir_from_name(en)
                    if en is not None
                    else None
                )
                snap = crony.model.snapshot_from_dict(
                    raw,
                    symlink=_read_symlink_pair(alias)
                    if alias is not None
                    else None,
                )
            except (TypeError, ValueError) as exc:
                orphans[ref] = crony.model.JobOrphan(
                    bundle=bundle_dir.name,
                    uuid=uuid_dir.name,
                    name=name_hint,
                    reason=f"snapshot conversion failed: {exc}",
                    source_path=snap_path,
                )
                continue
            full = str(snap.entity_name)
            if isinstance(snap, crony.model.Job):
                current.jobs[snap.entity_ref] = snap
                current.by_full_name[full] = snap.entity_ref
                runtime[snap.entity_ref] = _read_runtime_state(
                    uuid_dir,
                    full_name=full,
                    snapshot=snap,
                    last_exits=last_exits,
                )
            else:
                current.groups[snap.entity_ref] = snap
                current.by_full_name[full] = snap.entity_ref
                runtime[snap.entity_ref] = _read_runtime_state(
                    uuid_dir,
                    full_name=full,
                    snapshot=snap,
                    last_exits=last_exits,
                )
    # Broken entries get a runtime entry too -- the state dir
    # exists and carries the same per-run files (last-run.json /
    # run.lock / pending.flag / user-trigger.flag) as a normal
    # current entry, plus the platform unit path. Without this
    # the unit-config / last / last-ran columns render empty
    # for broken rows even when those files are on disk.
    for ref, orphan in orphans.items():
        state_dir = orphan.state_dir
        runtime[ref] = _read_runtime_state(
            state_dir,
            full_name=orphan.name,
            last_exits=last_exits,
        )
    return current, orphans, runtime


def load_config() -> crony.model.Config:
    """Build the whole-process Config: parse every bundle's TOML,
    walk the state-dir tree once, build pending + current graphs +
    runtime state in memory, and detect platform-unit-only orphans.
    """
    host = crony.platform.current_host()
    platform = crony.platform.current_platform()

    toml_config = crony.config.TomlConfig.load_all()
    pending = crony.model.Graph.build_pending(toml_config)
    # One bulk scheduler query feeds every entry's `unit_last_exit`, so
    # status can tell a launch killed before it recorded anything from
    # the stale `last-run.json` that survives such a kill.
    last_exits = scheduler(platform).unit_last_exits()
    current, orphans, runtime = _build_current_graph(
        crony.paths.STATE_DIR, last_exits
    )

    # Resolve same-name collisions among current entries. Two state
    # dirs can recover the same full name (uuid-edit residue that
    # escaped apply's cleanup, or hand-mucked state). The ref that
    # matches the live config keeps the plain name; the rest become
    # `shadowed` and surface by `<bundle>:<UUID>` in status. Without
    # this, `current.by_full_name` would silently drop all but the
    # last-scanned ref, hiding the residue.
    shadowed: set[crony.unit.EntityRef] = set()
    name_to_refs: dict[str, list[crony.unit.EntityRef]] = {}
    for node in current.nodes():
        name_to_refs.setdefault(str(node.entity_name), []).append(
            node.entity_ref
        )
    pending_refs = pending.refs()
    for name, refs in name_to_refs.items():
        if len(refs) < 2:
            continue
        winner = next((r for r in refs if r in pending_refs), None)
        if winner is None:
            winner = min(refs, key=lambda r: r.uuid)
        current.by_full_name[name] = winner
        shadowed.update(r for r in refs if r != winner)

    # `orphans` already holds the state-dir orphans from the scan
    # (broken snapshots with `reason` set, and snapshot-less leftover
    # dirs); the pure unit / alias leftovers found next join it.
    # `JobOrphan.is_broken` tells broken from orphan for status. A
    # snapshot-less dir whose uuid is still a live pending entry stays
    # in the map -- `config_state` reads it `stale` (re-apply), and
    # destroy / apply reclaim it through the entity.
    orphans_by_full_name: dict[str, crony.unit.EntityRef] = {
        o.name: ref for ref, o in orphans.items() if o.name is not None
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
        orphan = crony.model.JobOrphan(
            bundle=bundle_name,
            uuid=synthetic,
            name=full_name,
            has_unit_file=has_unit,
            has_symlink=full_name in alias_names,
        )
        orphans[ref] = orphan
        orphans_by_full_name[full_name] = ref
        # Surface the platform unit paths through RuntimeState (so
        # status's unit-config / unit-timer columns don't re-walk the
        # unit dirs); a symlink-only orphan has no unit file, so leave
        # them None.
        runtime[ref] = crony.model.RuntimeState(
            state_dir=orphan.state_dir,
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
            unit_last_exit=last_exits.get(full_name),
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


def _read_symlink_pair(link: Path) -> tuple[Path, str] | None:
    """The on-disk alias pair at `link`: (link, target) when it is a
    symlink (a dangling one included), else None. `target` is the
    link's literal contents -- the bare uuid for an apply-created
    alias -- compared against the entry's uuid to tell a correct alias
    from a mis-pointed one."""
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


# =============================================================================
# RECONCILIATION (apply / destroy one entry)
# =============================================================================
# Per-entry disk + scheduler reconciliation, driven by `do_apply` /
# `do_destroy` against the loaded Config. Commands orchestrate (select,
# order, log); these primitives own the on-disk artifacts.


def _repo_root() -> Path:
    """Repo root, derived from this module's location at
    <repo>/src/crony/runtime.py."""
    return Path(__file__).resolve().parent.parent.parent


def _crony_executable() -> Path:
    """Absolute path to bin/crony for re-invocation by groups.

    Derives bin/crony from this package's location
    (<repo>/src/crony/runtime.py -> <repo>/bin/crony) rather than
    from `sys.argv[0]` so the subprocess re-invocation reaches the
    right binary even when crony has been imported as a module (e.g.
    by the test suite, where sys.argv[0] is pytest, not crony).
    """
    return _repo_root() / "bin" / "crony"


def _uv_executable() -> Path:
    """Absolute path to `uv`, baked into platform unit files.

    The platform scheduler starts a unit's program
    with a minimal PATH that does not include $HOME/.local/bin or
    /opt/homebrew/bin. Crony's PEP 723 shebang `env -S uv run
    --script` therefore can't find uv and exits 127 before the
    script even starts. Resolving uv to an absolute path at apply
    time and writing it directly into the unit's argv sidesteps
    PATH entirely.

    Errors clearly if uv isn't on the agent's PATH at apply time;
    a misconfigured environment shouldn't silently render a unit
    that will fail at run time.
    """
    path = shutil.which("uv")
    if path is None:
        raise crony.errors.PreconditionError(
            "uv not found on PATH; install it (https://docs.astral.sh/uv/) "
            "before running `crony apply`. Platform units bake uv's "
            "absolute path so the scheduler doesn't have to find it "
            "on its minimal PATH."
        )
    return Path(path).resolve()


def _render_units(
    snap: crony.model.Job | crony.model.JobGroup, platform: str | None = None
) -> dict[str, str]:
    """Return {filename: content} for `snap`'s platform units.

    Delegates to the platform Scheduler with the live `_uv_executable()`
    / `_crony_executable()` paths baked into the unit argv. (The drift
    check re-renders inside the scheduler using the paths it recovers
    from the on-disk unit, so it does not go through here.)
    """
    return scheduler(platform).render(
        snap.unit_spec(),
        uv_path=_uv_executable(),
        crony_path=_crony_executable(),
    )


def apply_one(config: crony.model.Config, ref: crony.unit.EntityRef) -> str:
    """Apply one selected entry from the loaded model; return
    "added", "updated", or "unchanged".

    `ref` must be a selected pending entry of `config` -- `do_apply`
    only applies what `config.pending` selected on this host. The
    full namespaced name `<bundle>.<short>` is what lands on disk
    (the platform unit identifier).

    Every entry installs a platform unit, even schedule-less ones:
    a transit group's plist sits dormant until a parent dispatches
    it (or `crony trigger` fires it explicitly). On linux, only the
    .service file is installed for schedule-less entries; the
    .timer (which would have nothing to trigger on) is omitted, and
    apply cleans up a stale .timer if an entry transitions
    scheduled -> unscheduled.

    The entity's prior snapshot and unit-drift verdict come from
    the loaded model (`current` / `runtime`): the entry's own state
    is untouched by the apply loop until this call, so the loaded
    view still matches disk.
    """
    snapshot = config.pending.job_from_ref(ref)
    if snapshot is None:
        raise crony.errors.PreconditionError(
            f"{ref} is not a selected entry to apply"
        )
    full_name = str(snapshot.entity_name)
    bundle_name = ref.bundle
    timing = snapshot.timing
    snapshot_path = snapshot.snapshot_path

    # The full names the bundle's config currently defines, used to
    # keep the rename cleanup below from unlinking a *different* live
    # entry's unit when a name moves between entries in one edit (a
    # rename that frees a name a sibling then claims). A uuid change
    # is not handled here -- `do_apply` reclaims the old uuid before
    # this install runs.
    bundle = config.toml_config.by_name(bundle_name)
    bcfg = bundle.config if bundle is not None else None
    live_full_names: set[str] = set()
    if bcfg is not None:
        live_full_names = {f"{bundle_name}.{s}" for s in bcfg.jobs} | {
            f"{bundle_name}.{s}" for s in bcfg.job_groups
        }

    # Drift detection via direct snapshot comparison: take the
    # entity's prior snapshot (if any) and compare it to the
    # pending one. Both sides are the same dataclass shape, so
    # equality is a single Python `==`. A missing / unparseable /
    # wrong-schema prior snapshot is absent from `current` and so
    # "not equal", triggering a fresh write. Equality alone isn't
    # enough though: the unit-install integrity check (pinned on
    # `runtime` at load) catches a hand-edited / missing unit file
    # (or a scheduled unit the scheduler unloaded) whose snapshot
    # still matches, so an otherwise-clean apply still re-renders
    # and re-bootstraps the platform side.
    current_snapshot = config.current.job_from_ref(ref)
    rt = config.runtime.get(ref)
    unit_stale = rt.unit_is_stale if rt is not None else False
    if current_snapshot == snapshot and not unit_stale:
        return "unchanged"
    is_update = current_snapshot is not None

    # Same uuid, new name: the entry was renamed in config. The
    # state dir (uuid-keyed) is reused under the new name, but the
    # old name's platform unit is now stale -- remove it so only
    # the new name's unit remains. The shared state dir is left
    # alone (passing no state_dir to destroy_one). Skip when the
    # old name is itself a live entry (a name-swap edit handed it
    # to a sibling): that sibling owns the unit now, so removing it
    # would unlink a unit a live entry is firing from.
    if (
        current_snapshot is not None
        and str(current_snapshot.entity_name) != full_name
        and str(current_snapshot.entity_name) not in live_full_names
    ):
        destroy_one(
            str(current_snapshot.entity_name),
            None,
            current_snapshot.symlink_state_dir,
        )

    # Capture runtime state BEFORE we re-render so a hand-disabled
    # unit stays disabled across the re-load. The scheduler view
    # is the source of truth here (the platform unit can outlive
    # the state-dir snapshot); unit_state returns "none" for a
    # not-yet-installed unit, so only an explicit "disabled"
    # answer counts and a fresh install still lands enabled. A uuid
    # change clears it: `do_apply` reclaimed the old unit first, so
    # the new job installs fresh -- a uuid change is a new job.
    prior_disabled = unit_state(full_name) == "disabled"
    units = _render_units(snapshot)
    sched = scheduler()
    target_dir = sched.unit_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    # Clean up any previously-installed unit files for this name that
    # aren't in the current render set (handles a scheduled ->
    # unscheduled transition where the .timer should be removed).
    sched.prune_units(full_name, set(units))
    for fname, content in units.items():
        (target_dir / fname).write_text(content, encoding="utf-8")
    # Load the unit into the scheduler. `prior_disabled` re-applies a
    # hand-disable after the reload so a `crony disable` survives a
    # same-uuid re-render; a uuid change reclaimed the old unit first,
    # so the new job loads enabled (it is a new job). `scheduled=False`
    # installs a dormant unit (no .timer on linux) that only fires when
    # something triggers it.
    sched.activate(
        full_name,
        prior_disabled=prior_disabled,
        scheduled=timing is not None,
    )

    # Materialize the uuid-keyed state dir and seed an empty run.log
    # so an operator can `tail -f` it from apply time, before the first
    # run; never truncated, so an existing log survives a re-apply.
    snapshot.state_dir.mkdir(parents=True, exist_ok=True)
    if not snapshot.log_path_resolved.exists():
        snapshot.log_path_resolved.touch()
    snapshot_path.write_text(
        json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _link_alias(snapshot)
    return "updated" if is_update else "added"


def _link_alias(node: crony.model.Job | crony.model.JobGroup) -> None:
    """Point the entry's short-name alias at its uuid dir.

    The alias is `node.symlink_state_dir` and its (relative) target is
    the bare uuid, so the state tree stays relocatable. A correctly-
    pointed alias is left untouched; one pointing elsewhere is
    repointed. A non-symlink already sitting at the alias path is left
    alone -- apply never clobbers real state.
    """
    link = node.symlink_state_dir
    if link.is_symlink():
        if os.readlink(link) == node.uuid:
            return
        link.unlink()
    elif link.exists():
        return
    link.symlink_to(node.uuid)


def destroy_one(
    name: str | None,
    state_dir: Path | None,
    alias_dir: Path | None = None,
) -> None:
    """Remove a single entity's platform unit and apply-time state.

    Always a full wipe: the platform unit files, the short-name alias
    symlink, and the entire uuid-keyed state dir go away in one shot.
    Tolerant of partial state throughout so a partially-installed
    entity can still be cleaned up.

    `name` is the full namespaced name used for platform unit paths
    (`org.crony.<name>.plist`, `crony-<name>.{service,timer}`); pass
    `None` to skip platform unit cleanup (e.g. a ref-form destroy whose
    snapshot is unparseable). `alias_dir` is the entity's
    `symlink_state_dir` (resolved by the caller from the entity it
    holds); when it is a symlink it is unlinked, never a real dir. None
    means no alias to clean. `state_dir` is the uuid-keyed dir; None
    means there's no state dir to clean up.
    """
    if name is not None:
        scheduler().remove_files(name)
    if alias_dir is not None and alias_dir.is_symlink():
        alias_dir.unlink()
    if state_dir is None or not state_dir.is_dir():
        return
    shutil.rmtree(state_dir)
