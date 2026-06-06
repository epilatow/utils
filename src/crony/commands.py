# This is AI generated code

"""crony's command handlers.

The do_* verbs behind the CLI -- apply / destroy / enable / disable /
trigger / status / logs / config / validate / notify-test / self-test --
plus the apply and destroy primitives, the name-resolution and
apply-ordering helpers, and the status renderer (its column model,
divergence and color handling, and per-axis state derivation). This is
the in-process API a caller drives instead of shelling out to the crony
CLI; it composes the lower layers (config, model, runtime, notify,
runner) and performs the on-disk unit lifecycle.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil as shutil  # noqa: PLC0414  re-exported for tests
import subprocess as subprocess  # noqa: PLC0414  re-exported for tests
import sys as sys  # noqa: PLC0414  re-exported for tests
import time as time  # noqa: PLC0414  re-exported for tests
import uuid
from pathlib import Path

import tomlkit
import tomlkit.exceptions

import crony.notify
import crony.paths
import crony.platform
import crony.runner
import crony.runtime
from crony.config import (
    BUILTIN_NOTIFY_CHANNELS,
    DEFAULT_BUNDLE_NAME,
    NOTIFY_INHERIT_TOKEN,
    TomlBundle,
    TomlConfig,
    TomlJob,
    TomlJobGroup,
    bundle_prefix_filter,
    normalize_full_name,
    parse_full_name,
    resolve_cli_name,
    validate_bundle_name,
)
from crony.errors import (
    ConfigError,
    CronyError,
    ExitCode,
    LockBusyError,
    PreconditionError,
    UsageError,
)
from crony.model import (
    Config,
    Graph,
    Job,
    JobGroup,
    JobRunResult,
    NotificationResult,
    entity_state_dir,
    snapshot_path_for,
)
from crony.notify import (
    expand_notify_inherit,
    extract_latest_log_entry,
    retrieve_secret,
)
from crony.platform import (
    SchedulerWarning,
)
from crony.runner import (
    timeout_to_wait,
    trigger_exit_code,
)
from crony.runtime import (
    acquire_lock,
    load_config,
    now_iso,
    recover_full_name,
    run_in_progress,
    scheduler,
    state_dir_for,
)
from crony.unit import (
    EntityRef,
    Interval,
    Schedule,
    Timing,
)

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    """Repo root, derived from this module's location at
    <repo>/src/crony/commands.py."""
    return Path(__file__).resolve().parent.parent.parent


# =============================================================================
# DEFAULT CONFIG TEMPLATE
# =============================================================================
# Emitted by `crony config init`. Every section is commented out so a user can
# uncomment the bits they want without touching the explanatory prose.

DEFAULT_CONFIG_TEMPLATE: str = """\
# ============================================================================
# crony config bundle
# ============================================================================
# crony reads bundles from:
#   ~/.config/crony/config.toml      -> bundle name "default"
#   ~/.config/crony/config/<x>.toml  -> bundle name "<x>"
#                                       (filename stem matches
#                                       [A-Za-z0-9][A-Za-z0-9_-]*)
# Each bundle is independent: its [defaults], targets, groups, and
# notify config apply only to the jobs defined in the same file. The
# one cross-bundle tie is notification inheritance: a non-default
# bundle can borrow the default bundle's notify config (see the
# defaults section below).
# Job and group names are namespaced as <bundle>.<short>; on the
# CLI, a bare name (`crony trigger foo`) is shorthand for
# `default.foo` and never falls through to other bundles. The
# `-b/--bundle <name>` flag on multi-job subcommands (apply,
# destroy, status, enable, disable, trigger) scopes the operation
# to <name>, so a bare arg resolves there instead of `default`.
#
# Schedules use systemd OnCalendar syntax; intervals use systemd
# time-span syntax. See:
#   https://www.freedesktop.org/software/systemd/man/systemd.time.html
# ============================================================================


# ----------------------------------------------------------------------------
# defaults section
# ----------------------------------------------------------------------------
# Settings cascaded to every job in THIS bundle. Per-job and
# per-target settings can override anything here. Defaults from
# other bundles never apply -- each bundle has its own.

# [defaults]
# notify_channels      = []           # names defined below, or "dialog-popup"
# notify_attach_log    = true         # include log content in notifications
# notify_attach_max_kb = 256          # email cap (ntfy uses a 3 KB cap)
# job_timeout_sec      = 1800         # per-job wallclock cap; 0 = no cap
# trigger_timeout_sec  = 15           # `crony trigger --wait` deadline
# log_keep_runs        = 30
# priority             = "high"       # priority class; jobs may override
# keep_awake           = true         # default; jobs may override
#
# [defaults.env]
# PATH = "$HOME/.local/bin:$PATH"      # merged under every job; a job key wins

# Notification inheritance. The reserved channel name `default` in a
# NON-default bundle's `notify_channels` pulls in the default bundle's
# channel list, definitions, and attach settings. Alone
# (`notify_channels = ["default"]`) the bundle notifies exactly as the
# default bundle would, so it can omit every [defaults.notify.*] block
# of its own. Combined with explicit siblings
# (`notify_channels = ["default", "dialog-popup"]`) it notifies as the
# default bundle would PLUS those channels -- de-duped, so a channel in
# both fires once. Those siblings must be channels defined in THIS
# bundle or zero-config built-ins (e.g. dialog-popup); the sentinel
# already pulls in the whole default set, so there is no need to
# re-list the default bundle's channels by name. `default` is also the
# implicit default: a
# non-default bundle that says nothing about notify config inherits the
# default bundle's. Opt back out with an explicit `notify_channels = []`
# (silence). The default bundle cannot inherit itself, and `default` is
# a reserved channel name (no [defaults.notify.default] block).

# Each notify channel is a [defaults.notify.<name>] block. The name
# is whatever the user lists in `notify_channels`. The `transport`
# field selects the underlying sender ("email", "ntfy", or
# "dialog-popup"); when omitted, transport defaults to the channel
# name (so a block named `email`, `ntfy`, or `dialog-popup` picks up
# its like-named transport automatically). Optional `headers` is a
# table of arbitrary message headers crony attaches to email / ntfy
# -- crony-controlled headers (To/From/Subject for email;
# Authorization/Tags/Title for ntfy) are reserved and rejected.
#
# `dialog-popup` is a zero-config built-in -- it needs no block at
# all. Just list "dialog-popup" in `notify_channels` and a failing
# job pops a native desktop dialog (macOS) showing the failure
# summary and latest log. An explicit block is allowed but only ever
# sets `transport`.

# Embedded SMTP channel.
# [defaults.notify.email]
# # transport defaults to "email" since the channel is named "email"
# to                         = "you@example.com"
# from                       = "crony@example.com"
# smtp_host                  = "smtp.gmail.com"
# smtp_port                  = 587
# smtp_user                  = "you@gmail.com"
# smtp_starttls              = true
# # Password retrieval -- first match wins. On macOS prefer the
# # keychain item (the optional _account narrows the lookup when
# # multiple items share a service name); on Linux fall back to a
# # 0600 secrets file.
# smtp_pass_keychain_service = "crony-smtp"
# smtp_pass_keychain_account = "you@gmail.com"   # optional
# smtp_pass_file             = "~/.config/crony/secrets/smtp-password"
# headers                    = { "Reply-To" = "you@example.com" }

# ntfy channel.
# [defaults.notify.ntfy]
# url                    = "https://ntfy.example.com/automation"
# token_keychain_service = "ntfy-automation"
# token_keychain_account = "edp"                 # optional
# token_file             = "~/.config/crony/secrets/ntfy-token"

# Custom-named ntfy channel that also relays through ntfy's email
# integration. The transport field is required because the channel
# name `ntfy-email` doesn't match a built-in transport.
# [defaults.notify.ntfy-email]
# transport              = "ntfy"
# url                    = "https://ntfy.example.com/automation"
# token_keychain_service = "ntfy-automation"
# headers                = { "Email" = "you@example.com", "Priority" = "high" }

# Native desktop dialog (macOS). Zero-config -- normally you just put
# "dialog-popup" in notify_channels with no block at all; this
# explicit form is equivalent and only names the transport.
# [defaults.notify.dialog-popup]
# transport = "dialog-popup"


# ----------------------------------------------------------------------------
# job sections
# ----------------------------------------------------------------------------
# A schedulable unit of work. Either `command` (one-line shell) or
# `script` (path under the dotfiles repo) is required. Schedule and
# interval are optional; a job with neither fires only when a
# scheduled group dispatches it. Validation rejects a target chain
# that reaches a job through a path with no schedule anywhere.
#
# `uuid` is the stable per-bundle identity of the entry, decoupled
# from `name` so that renaming a job in this file is recognized as
# a rename rather than a delete plus an unrelated add. Hand-edit a
# new entry without it and run `crony config update` to fill the
# field in place; the same `update` action works on `[job-group.*]`
# blocks below. `crony config generate-uuid` prints one fresh UUID
# for editor seeding when `update` can't yet parse the file.

# [job.brew-update]
# uuid      = "aabbccdd-1234-5678-9abc-aabbccddeeff"   # `crony config update`
# command   = "brew update && brew upgrade && brew cleanup"
# platforms = ["darwin"]               # filter: skip on non-darwin hosts
# # hosts   = ["mymac"]                # filter: only on listed hostnames
# # hosts   = ["!noisyhost"]           # or: every host except listed
# #                                    #   (all entries must use `!`, or none)
# schedule  = "*-*-* 03:15"            # OnCalendar; daily at 03:15
# gate      = "command -v brew"        # skip benignly if absent
# # Optional per-job overrides of [defaults]:
# # notify_channels = ["email"]   # or [] to silence just this job
# # job_timeout_sec = 7200        # 0 = no cap (job manages its own timeout)
# # priority = "high"             # process-priority class for the unit:
# #   "high"   un-throttle: macOS ProcessType=Interactive + normal CPU/IO
# #            (avoids the QoS throttling that slows IO-bound work); on
# #            Linux there is no such throttle to undo, so it has no
# #            runtime effect (recorded as a unit comment).
# #   "low"    throttle: macOS Background + low-priority IO + Nice 10;
# #            Linux Nice 10 + idle IO scheduling.
# #   "normal" (default) emit nothing. Applies on every fire path
# #            (scheduled, `crony trigger`, parent-group dispatch).
# # keep_awake = true            # hold a power assertion for the run so
# #            an idle / on-AC machine doesn't sleep mid-job. NOTE:
# #            closing the lid on battery still sleeps the machine --
# #            nothing in userspace prevents that.
# # success_exit_codes = [1]     # non-zero exit codes to classify as
# #            success (exit 0 is always success). A run whose code is
# #            listed is "ok" -- not failed, no notification -- and the
# #            unit sees a 0 exit. For commands that exit non-zero on
# #            transient / non-fatal conditions (e.g. borg's exit 1 on
# #            backup warnings).
# # env supports $VAR / ${VAR} expansion against the running env, so
# # `PATH = "$HOME/.local/bin:$PATH"` extends the inherited PATH.
# # Unresolved vars stay literal; `$$` is a literal `$`. Merges over
# # [defaults.env] -- a key set here wins for this job.
# # env = { "PATH" = "$HOME/.local/bin:$PATH", "RUST_BACKTRACE" = "1" }

# [job.git-pull-utils]
# script   = "scripts/git-pull.sh"     # path under the dotfiles repo
# args     = ["~/utils"]               # `~` and `$VAR` expanded
# interval = "30min"                   # 30 min after each completion

# Group-only job: no schedule, only fires via [job-group.*].
# [job.uv-update]
# command = "uv self update && uv tool upgrade --all"
# gate    = "command -v uv"

# Interactive job: sits pending after its fire and pops a
# desktop dialog (Run / Delay / Cancel) once the user has been
# active continuously for `interactive_active` (default 10min).
# Auto-tags `platforms = ["darwin"]`, so a non-darwin host
# silently skips it. May live in a [job-group.*]; the group
# dispatches it async (no wait) and the child's interactive wait
# runs independently of the group's deadline.
# [job.weekly-prompt]
# command            = "/usr/local/bin/some-interactive-task"
# schedule           = "Sun *-*-* 12:00"  # weekly at noon
# interactive        = true
# # Optional knobs (defaults shown):
# # interactive_active = "10min"          # required user-active window
# # interactive_delay  = "1h"             # sleep after "Delay Job"


# ----------------------------------------------------------------------------
# job-group sections
# ----------------------------------------------------------------------------
# A sequencer that fires named jobs (or other groups) in order.
# Schedule / interval are optional: a group with no schedule is a
# "transit" group that fires only when a parent group dispatches it.
# Every chain from a target down through groups to a leaf job must
# contain a schedule somewhere, or validation rejects it.

# [job-group.daily-updates]
# uuid      = "11223344-5566-7788-99aa-bbccddeeff00"
# schedule  = "*-*-* 03:00"
# jobs      = ["brew-update", "uv-update", "git-pull-utils"]
# # platforms = ["darwin"]             # filter: as on jobs
# # hosts     = ["mymac"]              # filter: as on jobs
# # hosts     = ["!noisyhost"]         # `!` negation also supported here

# Nested group: this one has no schedule of its own; it fires only
# when the daily-updates group above (or some other scheduled
# parent) dispatches it.
# [job-group.uv-updates]
# jobs = ["uv-update"]


# ----------------------------------------------------------------------------
# target sections
# ----------------------------------------------------------------------------
# Selects which jobs and groups install on which hosts. A host
# target replaces the platform target when both apply; the explicit
# `jobs` list IS the selection (no inherits / exclude / add).
#
# Single-parent rule: within one target's subtree, each name has
# at most one parent. Listing both a group and one of its children
# from the same target -- or two groups that share a child -- is
# rejected at parse time.

# [target.darwin]
# jobs            = ["daily-updates"]
# notify_channels = ["email"]

# [target.linux]
# jobs            = ["git-pull-utils"]
# notify_channels = ["ntfy"]

# [target.host.work-laptop]
# jobs            = ["brew-update", "git-pull-utils"]
# notify_channels = []   # no external dispatch (run.log + last-run.json only)
"""


def _crony_executable() -> Path:
    """Absolute path to bin/crony for re-invocation by groups.

    Derives bin/crony from this package's location
    (<repo>/src/crony/commands.py -> <repo>/bin/crony) rather than
    from `sys.argv[0]` so the subprocess re-invocation reaches the
    right binary even when crony has been imported as a module (e.g.
    by the test suite, where sys.argv[0] is pytest, not crony).
    """
    return _repo_root() / "bin" / "crony"


def uv_executable() -> Path:
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
        raise PreconditionError(
            "uv not found on PATH; install it (https://docs.astral.sh/uv/) "
            "before running `crony apply`. Platform units bake uv's "
            "absolute path so the scheduler doesn't have to find it "
            "on its minimal PATH."
        )
    return Path(path).resolve()


# =============================================================================
# PLATFORM UNITS
# =============================================================================
# Render and (un)load platform-native scheduler units. The per-platform
# divergence lives entirely in the crony.platform backends; the
# functions here are thin delegates via scheduler(), which carries no
# platform branch -- the backend owns its own unit directory.


def _render_units(
    snap: Job | JobGroup, platform: str | None = None
) -> dict[str, str]:
    """Return {filename: content} for `snap`'s platform units.

    Delegates to the platform Scheduler with the live `uv_executable()`
    / `_crony_executable()` paths baked into the unit argv. (The drift
    check re-renders inside the scheduler using the paths it recovers
    from the on-disk unit, so it does not go through here.)
    """
    return scheduler(platform).render(
        snap.unit_spec(),
        uv_path=uv_executable(),
        crony_path=_crony_executable(),
    )


def _schedule_display(timing: Timing | None) -> str:
    """Render a unit's timing into one status cell value.

    A Schedule shows its OnCalendar source; an Interval shows
    `interval=<spec>`. An entry with no timing -- a transit group or a
    group-only job -- displays as `grouped` to mirror the UNIT axis
    value for the same condition.
    """
    if isinstance(timing, Schedule):
        return str(timing)
    if isinstance(timing, Interval):
        return f"interval={timing}"
    return "grouped"


def _activate_unit(
    name: str,
    platform: str | None = None,
    *,
    prior_disabled: bool,
    scheduled: bool,
) -> None:
    """Load the unit into the platform scheduler.

    `prior_disabled=True` preserves a hand-disabled state across an
    apply re-render: the unit is reloaded so the new content takes
    effect, but the persistent disable record is restored afterward
    so the scheduler doesn't fire it. Without this, a `crony disable
    foo` followed by an unrelated config edit would silently re-arm
    foo.

    `scheduled=False` means the entry has no schedule / interval.
    On linux that means there's no .timer to enable; only the
    .service is registered (oneshot, sits dormant until something
    starts it). The plist analog is the same -- a plist with no
    Start* keys loads fine and just doesn't auto-fire.
    """
    scheduler(platform).activate(
        name,
        prior_disabled=prior_disabled,
        scheduled=scheduled,
    )


def apply_one(config: Config, ref: EntityRef) -> str:
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
    snapshot: Job | JobGroup | None = config.pending.jobs.get(
        ref
    ) or config.pending.groups.get(ref)
    if snapshot is None:
        raise PreconditionError(f"{ref} is not a selected entry to apply")
    full_name = str(snapshot.name)
    bundle_name = ref.bundle
    timing = snapshot.timing
    snapshot_path = snapshot_path_for(ref)

    # Every uuid / full name the bundle's config currently defines
    # (plus `ref` itself, defensively). Used to keep per-entry
    # cleanup from clobbering a *different* live entry's state when
    # names move between entries in one edit (a rename that frees a
    # name another entry then claims).
    bundle = config.toml_config.by_name(bundle_name)
    bcfg = bundle.config if bundle is not None else None
    live_uuids = {ref.uuid}
    live_full_names: set[str] = set()
    if bcfg is not None:
        live_uuids |= {e.uuid for e in bcfg.jobs.values()} | {
            e.uuid for e in bcfg.job_groups.values()
        }
        live_full_names = {f"{bundle_name}.{s}" for s in bcfg.jobs} | {
            f"{bundle_name}.{s}" for s in bcfg.job_groups
        }

    # A uuid edit leaves the prior uuid's state dir behind under
    # the same name (the name-keyed unit re-points at this uuid).
    # Reclaim it before deciding "unchanged" so the residue is
    # cleaned even when the new uuid's snapshot already matches --
    # and so the full-sync orphan sweep can exclude still-selected
    # names (it trusts apply_one to have handled their residue).
    _sweep_superseded_state_dirs(ref, full_name, live_uuids)
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
    current_snapshot: Job | JobGroup | None = config.current.jobs.get(
        ref
    ) or config.current.groups.get(ref)
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
        and str(current_snapshot.name) != full_name
        and str(current_snapshot.name) not in live_full_names
    ):
        destroy_one(str(current_snapshot.name), None)

    # Capture runtime state BEFORE we re-render so a hand-disabled
    # unit stays disabled across the re-load. The scheduler view
    # is the source of truth here (the platform unit can outlive
    # the state-dir snapshot); crony.runtime.unit_state returns "none" for a
    # not-yet-installed unit, so only an explicit "disabled"
    # answer counts and a fresh install still lands enabled.
    prior_disabled = crony.runtime.unit_state(full_name) == "disabled"
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
    _activate_unit(
        full_name,
        prior_disabled=prior_disabled,
        scheduled=timing is not None,
    )

    state_dir_for(ref)
    snapshot_path.write_text(
        json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return "updated" if is_update else "added"


def destroy_one(name: str | None, state_dir: Path | None) -> None:
    """Remove a single entity's platform unit and apply-time state.

    Always a full wipe: the platform unit files and the entire
    uuid-keyed state dir go away in one shot. Tolerant of partial
    state throughout so a partially-installed entity can still be
    cleaned up.

    `name` is the full namespaced name used for platform unit
    paths (`org.crony.<name>.plist`, `crony-<name>.{service,timer}`).
    Pass `None` when no name is recoverable -- e.g. a ref-form
    destroy whose state dir snapshot is unparseable -- to skip
    platform unit cleanup. `state_dir` is the uuid-keyed dir
    (resolved by the caller via Config); None means there's no
    state dir to clean up.
    """
    if name is not None:
        scheduler().remove_files(name)
    if state_dir is None or not state_dir.is_dir():
        return
    shutil.rmtree(state_dir)


def _sweep_superseded_state_dirs(
    keep: EntityRef, full_name: str, live_uuids: set[str]
) -> None:
    """Remove state dirs in `keep`'s bundle whose snapshot recovers
    `full_name` under a uuid no live config entry claims.

    These are residue from a uuid edit: the name-keyed platform
    unit now points at `keep`, so the old uuid dirs are unreachable
    history. Only the state dir is wiped -- the unit is shared by
    name and already re-rendered for `keep`.

    `live_uuids` is every uuid the bundle's config currently
    defines. A dir keyed by one of those belongs to a live entry,
    not residue -- even when its on-disk name still matches
    `full_name` because that entry was renamed and hasn't been
    re-applied yet in this pass (a name-swap edit). Skipping them
    keeps a live entry's run history from being wiped by a sibling
    entry that grabbed its old name. A dir with a run in progress
    is left in place (logged); the next apply or an explicit
    `crony destroy <bundle>:<uuid>` reclaims it.
    """
    bundle_root = crony.paths.STATE_DIR / keep.bundle
    if not bundle_root.is_dir():
        return
    for uuid_dir in sorted(bundle_root.iterdir()):
        if not uuid_dir.is_dir() or uuid_dir.name in live_uuids:
            continue
        if recover_full_name(uuid_dir) != full_name:
            continue
        stray = EntityRef(keep.bundle, uuid_dir.name)
        if run_in_progress(uuid_dir):
            logger.warning(
                "%s: superseded state dir %s left in place (run in progress)",
                full_name,
                stray,
            )
            continue
        destroy_one(None, uuid_dir)
        logger.info(
            "%s: removed superseded state dir %s",
            full_name,
            stray,
        )


# =============================================================================
# RUNTIME STATE (enable / disable / status / linger)
# =============================================================================
# Runtime state is the platform scheduler's "will I fire this unit?"
# answer. It's orthogonal to CONFIG state: a unit can be `synced` with
# config but `disabled` at runtime because the user paused it. apply
# preserves this state across re-renders so a hand-disabled job stays
# off when the user runs `crony apply` to push other changes.


def last_run_state(config: Config, full_name: str) -> str:
    """Return LAST axis value for a stamped entity.

    Lock-held implies a run is currently in flight: "pending" when
    the in-flight run is an interactive job sitting in its wait
    loop (signaled by a `pending.flag` file in the state dir),
    "running" otherwise. Without the lock, read last-run.json's
    recorded exit_class. Absence of either means the entity has
    never run -- new install, group-only job that's never been
    triggered, etc.

    Resolution is current-first, pending-fallback. Runtime is
    uuid-keyed, and a rename keeps the uuid, so the run history
    lives at the same state dir under the new name. The current
    graph is keyed by the applied (old) name, so a not-yet-applied
    rename misses there; the pending graph carries the new name at
    the unchanged uuid and recovers the record. A genuinely new
    pending-only entry resolves to a uuid with no state dir, so
    `runtime.get` is None and it still reports "never".
    """
    ref = config.resolve_current(full_name) or config.resolve_pending(full_name)
    if ref is None:
        return "never"
    rt = config.runtime.get(ref)
    if rt is None:
        return "never"
    if rt.is_running:
        return "pending" if rt.is_pending else "running"
    if rt.last_run is None:
        return "never"
    cls = rt.last_run.exit_class
    if cls in (
        "ok",
        "fail",
        "timeout",
        "gated",
        "signal",
        "canceled",
    ):
        return cls if cls != "signal" else "fail"
    return "unknown"


def _last_ran_at(config: Config, full_name: str) -> str:
    """Return a compact "when did this last run" string for status.

    Returns "never" when there's no last-run record and "unknown"
    when the recorded timestamp is unreadable. Resolution is
    current-first, pending-fallback: runtime is uuid-keyed, so a
    not-yet-applied rename (new name, unchanged uuid) is recovered
    via the pending graph when the applied-name lookup misses.
    """
    ref = config.resolve_current(full_name) or config.resolve_pending(full_name)
    if ref is None:
        return "never"
    rt = config.runtime.get(ref)
    if rt is None or rt.last_run is None:
        return "never"
    started = rt.last_run.started_at
    if not started:
        return "unknown"
    try:
        dt = datetime.datetime.fromisoformat(started)
    except ValueError:
        return "unknown"
    now = datetime.datetime.now(datetime.UTC).astimezone()
    secs = int((now - dt).total_seconds())
    if secs < 0:
        # Clock skew or a future-dated record; surface explicitly
        # rather than rendering "0s ago" which would mislead.
        return "future"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _config_axis(
    config: Config,
    full: str,
    entry: TomlJob | TomlJobGroup | None,
    bn: str,
) -> str:
    """CONFIG axis (synced / stale / broken / missing / orphan)
    for `full`, derived entirely from the in-memory `Config` --
    no filesystem or scheduler re-query. `error` is handled by
    the caller (it's name-based; an errored entry has no ref).

    The ref to score is the entry's own `<bundle>:<uuid>` when it
    is still in config, otherwise whatever the on-disk side
    recovered (current snapshot, broken snapshot, or unit-only
    orphan). `Config.config_state` reduces the pending and current
    graphs -- both built once at load -- to a single verdict;
    `unit_is_stale` (the load-time unit-install integrity check)
    and a lingering-unit check layer drift signals on top of a
    bare snapshot comparison.
    """
    if entry is not None:
        ref: EntityRef | None = EntityRef(bn, entry.uuid)
    else:
        ref = config.resolve_current(full) or config.resolve_pending(full)
    if ref is None or ref not in config.all_refs():
        # Nothing the in-memory model knows by this ref: an
        # in-config-but-host-masked entry (absent from the pending
        # graph) or a bare name with no on-disk state. "missing"
        # is the pre-mask base the mask layer turns into "masked".
        return "missing"
    state = config.config_state(ref)
    if (
        state == "missing"
        and entry is not None
        and full in config.unit_only_by_full_name
    ):
        # In config and never cleanly applied (no current
        # snapshot) but a platform unit lingers from a prior apply
        # whose state dir was wiped -- re-apply territory,
        # surfaced as drift rather than a clean "not applied."
        return "stale"
    if state == "synced":
        # Snapshot equality alone isn't enough: a matching
        # snapshot whose unit file is missing / hand-edited /
        # unloaded is still stale and apply must re-render.
        rs = config.runtime.get(ref)
        if rs is not None and rs.unit_is_stale:
            return "stale"
    return state


def _enable_unit(name: str, platform: str | None = None) -> None:
    """Move the scheduler to the `enabled` state for `name` (delegates)."""
    scheduler(platform).enable(name)


def _disable_unit(name: str, platform: str | None = None) -> None:
    """Move the scheduler to the `disabled` state for `name` (delegates)."""
    scheduler(platform).disable(name)


# =============================================================================
# COMMAND HANDLERS
# =============================================================================
# Each handler's signature must match its argparse subparser's argument
# `dest` names exactly; the shared CmdCallbacksBase test enforces this.
# Handlers without behavior yet raise NotImplementedError so an
# unfinished feature surfaces immediately rather than silently no-oping.


def do_init(force: bool, bundle: str | None) -> None:
    """Generate a default config file.

    With `--bundle <name>`, writes `config/<name>.toml` (creating
    the dropin dir if missing). Otherwise writes `config.toml`.
    """
    if bundle is not None:
        if bundle == DEFAULT_BUNDLE_NAME:
            raise UsageError(
                f"--bundle {DEFAULT_BUNDLE_NAME!r} would shadow "
                f"config.toml; use plain `crony config init` (without "
                f"--bundle) for the default bundle"
            )
        try:
            validate_bundle_name(bundle, "--bundle")
        except ConfigError as e:
            raise UsageError(str(e)) from e
        target = crony.paths.CONFIG_DROPIN_DIR / f"{bundle}.toml"
        if target.exists() and not force:
            raise UsageError(
                f"{target} already exists; pass --force to overwrite"
            )
        crony.paths.CONFIG_DROPIN_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
        logger.info("wrote bundle config to %s", target)
        return
    if crony.paths.CONFIG_FILE.exists() and not force:
        raise UsageError(
            f"{crony.paths.CONFIG_FILE} already exists; "
            "pass --force to overwrite"
        )
    crony.paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    crony.paths.CONFIG_FILE.write_text(
        DEFAULT_CONFIG_TEMPLATE, encoding="utf-8"
    )
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
        candidates.append((DEFAULT_BUNDLE_NAME, crony.paths.CONFIG_FILE))
    if crony.paths.CONFIG_DROPIN_DIR.exists():
        for path in sorted(crony.paths.CONFIG_DROPIN_DIR.glob("*.toml")):
            candidates.append((path.stem, path))
    if bundle is not None:
        candidates = [
            (name, path) for (name, path) in candidates if name == bundle
        ]
    return candidates


def insert_missing_uuids_in_section(
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
            validate_bundle_name(bundle, "--bundle")
        except ConfigError as e:
            raise UsageError(str(e)) from e
    files = _bundle_files_for_update(bundle)
    if not files:
        if bundle is None:
            raise ConfigError(
                f"no config: expected {crony.paths.CONFIG_FILE} or "
                f"{crony.paths.CONFIG_DROPIN_DIR}/*.toml"
            )
        raise UsageError(
            f"no config file for bundle {bundle!r} "
            f"(expected {crony.paths.CONFIG_DROPIN_DIR}/{bundle}.toml)"
        )
    for bundle_name, path in files:
        try:
            doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        except tomlkit.exceptions.ParseError as e:
            logger.error("%s: TOML parse error: %s", path, e)
            continue
        added_jobs = insert_missing_uuids_in_section(doc, "job")
        added_groups = insert_missing_uuids_in_section(doc, "job-group")
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


def _errored_full_names(bundles: TomlConfig, bundle: str | None) -> set[str]:
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
    bundles: TomlConfig,
) -> tuple[dict[str, tuple[TomlBundle, str]], set[str]]:
    """Return (full_name -> (bundle, short)) and full_names set,
    spanning every bundle's selection on the current host.

    `by_full`'s key set is the same set as `selected`; this is the
    contract `do_apply` / `_expand_apply_subtree` rely on to
    distinguish "names known to this host's selection" from
    "everything else".  Callers that also need to inspect masked
    entries use `_selected_and_masked_full_names_per_bundle`.
    """
    by_full: dict[str, tuple[TomlBundle, str]] = {}
    for b in bundles.bundles:
        target = b.config.resolve_target()
        sel_jobs, sel_groups = b.config.selected_jobs_and_groups(target)
        for short in sel_jobs | sel_groups:
            by_full[b.full_name(short)] = (b, short)
    return by_full, set(by_full.keys())


def _selected_and_masked_full_names_per_bundle(
    bundles: TomlConfig,
) -> tuple[dict[str, tuple[TomlBundle, str]], set[str], dict[str, str]]:
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
    by_full: dict[str, tuple[TomlBundle, str]] = {}
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
            # genuinely-unused ones at this layer; `resolve_state_axes`
            # promotes them back to `error` so the user sees the
            # actual problem instead of a generic mask.
            masked_by_full[full] = "unused"
    return by_full, selected, masked_by_full


def _expand_apply_subtree(
    by_full: dict[str, tuple[TomlBundle, str]], full_names: list[str]
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
    by_full: dict[str, tuple[TomlBundle, str]], full_names: list[str]
) -> list[str]:
    """Order names so each group's children are applied first.

    Groups depend on their children (the group's snapshot pulls
    `group_budget_sec` from the children's resolved timeouts via
    the live config; ordering leaves-first keeps both pinned and
    in-progress state consistent within the same apply pass).

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
    config = load_config()
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
        raise UsageError(
            "refusing the full-sync apply: one or more config "
            f"files failed to parse ({affected}). Fix the config "
            "or pass explicit job names / `--bundle <name>` so "
            "the orphan-removal scope is intentional."
        )
    by_full, selected = _selected_full_names_per_bundle(bundles)
    if bundle is not None:
        selected = bundle_prefix_filter(selected, bundle)

    if jobs:
        normalized = [resolve_cli_name(arg, bundle) for arg in jobs]
        errored = _errored_full_names(bundles, bundle)
        errored_in_args = sorted(n for n in normalized if n in errored)
        if errored_in_args:
            # An errored entry has no parsed TomlBundleConfig fields, so we
            # can't render its plist / unit. Bail before any
            # partial apply happens.
            raise UsageError(
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
            raise UsageError(
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

    for full in full_names_to_apply:
        ref = config.pending.by_full_name[full]
        result = apply_one(config, ref)
        if verbose or result != "unchanged":
            logger.info("%s: %s", full, result)

    if remove_orphans:
        # Reconcile by identity, not by name: anything on disk
        # (current snapshot, broken snapshot, or unit-only orphan)
        # whose ref the live config no longer selects is an orphan,
        # regardless of whether its name is recoverable. A name-
        # based sweep missed broken snapshots (no recoverable
        # name) and unit-only orphans.
        #
        # Derived from the one up-front `config` (no reload): the
        # apply loop only installs selected entries and reclaims
        # their own superseded same-name residue (apply_one's
        # state-only sweep), so the set of orphans whose name is
        # NOT selected is unchanged by the loop. Excluding
        # still-selected names is also what keeps a shared name-
        # keyed unit safe -- the residue of a live entry is
        # apply_one's job, not the orphan sweep's, so the sweep
        # never unlinks a unit a selected entry is still firing.
        on_disk = (
            config.current.refs() | set(config.broken) | set(config.unit_only)
        )
        live = config.pending.refs()
        orphan_refs = sorted(
            (
                r
                for r in on_disk - live
                if (bundle is None or r.bundle == bundle)
                and config.name_for(r) not in selected
            ),
            key=lambda r: (r.bundle, r.uuid),
        )
        for ref in orphan_refs:
            name = config.name_for(ref)
            sd = entity_state_dir(ref)
            label = name if name is not None else str(ref)
            if sd.is_dir() and run_in_progress(sd):
                logger.warning(
                    "%s: orphan left in place (run in progress)", label
                )
                continue
            destroy_one(name, sd if sd.is_dir() else None)
            logger.info("%s: orphan removed", label)


def do_destroy(
    jobs: list[str],
    bundle: str | None,
    orphans: bool,
) -> None:
    """Remove platform units. Always a full wipe -- the platform
    unit files and the entry's state dir both go away.

    No args: factory reset -- every crony-managed remnant on this
    host. Discovery covers state dirs plus platform unit files.

    With `--bundle <name>` and no positional args: scope the reset
    to that bundle's discovered names. Other bundles' remnants
    stay intact.

    With `--orphans`: limit removal to entries with on-disk
    remnants that no bundle's live config selects to install on
    this host. Combinable with `--bundle` (orphans within that
    bundle namespace). Mutually exclusive with positional names.

    With names: surgical removal by full namespaced name. Refuses
    if a name is in neither any bundle's config nor the
    discovered set; refuses if a name's run.lock is currently
    held. Under `--bundle`, bare names resolve in that bundle and
    qualified names must match it. A rename (same uuid, new config
    name) is removable by either name -- resolution is by uuid, so
    the installed unit and the shared state dir are cleaned up; a
    name mapping to different uuids in config vs on disk is rejected
    until `crony apply`.

    Also accepts the `<bundle>:<UUID>` input form
    so an operator can copy the JOB cell from a status row for
    an entity with no recoverable name (corrupt snapshot, broken
    entry) and paste it here. The input validates iff the
    addressed state dir exists; the entity's actual name (used
    for platform unit cleanup) is recovered from the snapshot
    when readable, otherwise the platform unit cleanup is
    dropped and only the state dir is wiped.
    """
    if orphans and jobs:
        raise UsageError(
            "--orphans and positional names are mutually exclusive"
        )
    config = load_config()
    bundles = config.toml_config
    config.require_addressable(bundle)
    # Shadowed collision losers share a live name, so they never
    # surface in the name-keyed discovery set
    # (`Config.installed_full_names()`) -- collect them by ref.
    # They're always orphans (the winner holds the name), so both
    # factory-reset and `--orphans` reclaim them; surgical
    # `destroy <name>` doesn't (the operator can paste a
    # `<bundle>:<UUID>` from status to target one).
    shadowed_refs: list[EntityRef] = []
    if jobs:
        by_full, _ = _selected_full_names_per_bundle(bundles)
        # Defined names span every bundle's full names plus any
        # name with an on-disk remnant (so a leftover state dir or
        # unit can still be destroyed after the bundle is gone).
        defined = set(by_full.keys()) | bundles.all_full_names()
        known = defined | config.installed_full_names()
        if bundle is not None:
            known = bundle_prefix_filter(known, bundle)
        normalized = [resolve_cli_name(arg, bundle) for arg in jobs]
        # A `<bundle>:<UUID>` input is "known" iff
        # its state dir exists on disk; this lets the operator
        # destroy entries that have no recoverable name (corrupt
        # snapshots) by pasting the form from a status row.
        unknown = []
        for n in normalized:
            if n in known:
                continue
            syn = EntityRef.from_str(n)
            if syn is not None and entity_state_dir(syn).is_dir():
                continue
            unknown.append(n)
        if unknown:
            raise UsageError(f"unknown name(s): {sorted(unknown)}")
        full_names_to_destroy = normalized
    else:
        names = config.installed_full_names()
        if orphans:
            _by_full, selected = _selected_full_names_per_bundle(bundles)
            names = names - selected
        if bundle is not None:
            names = bundle_prefix_filter(names, bundle)
        full_names_to_destroy = sorted(names)
        shadowed_refs = sorted(
            (
                r
                for r in config.shadowed
                if bundle is None or r.bundle == bundle
            ),
            key=lambda r: (r.bundle, r.uuid),
        )

    explicit = bool(jobs)
    for full in full_names_to_destroy:
        # The state dir to wipe is the one the installed unit's argv
        # addresses: the *current* (most-recently-applied) uuid. For an
        # explicit `destroy <name>`, resolve by uuid so a rename
        # addressed by its new name still wipes the right entity (and a
        # name-swap raises rather than guessing). The `--orphans` /
        # factory-reset path iterates installed (current) names, so it
        # stays current-only -- a pending fallback there could resolve
        # an old-name remnant to a uuid still live under its new name
        # and wipe shared state. A `<bundle>:<UUID>` input lands as
        # `direct_ref` and is used directly.
        direct_ref = EntityRef.from_str(full)
        if explicit:
            ref = _resolve_addressable(config, full)
        else:
            ref = config.resolve_current(full) or direct_ref
        sd = entity_state_dir(ref) if ref is not None else None
        # Recover the entity's actual name for platform unit cleanup:
        # the unit file is keyed by the installed (current) name, which
        # a `<bundle>:<UUID>` input or a renamed entry's new name
        # doesn't carry. `name_for` recovers it (current-first); None
        # when the snapshot was unparseable or absent, in which case
        # the platform unit cleanup is dropped (no name -> no path).
        if direct_ref is not None:
            name_for_cleanup: str | None = config.name_for(direct_ref)
        elif explicit and ref is not None:
            name_for_cleanup = config.name_for(ref) or full
        else:
            name_for_cleanup = full
        # Refuse if a run is in progress; a partial destroy would
        # leave the running shim with deleted state under it.
        lock_path = sd / "run.lock" if sd is not None else None
        if lock_path is not None and lock_path.exists():
            try:
                with acquire_lock(lock_path):
                    pass
            except LockBusyError:
                raise LockBusyError(
                    f"{full}: run in progress; will not destroy"
                ) from None
        destroy_one(name_for_cleanup, sd)
        logger.info("%s: destroyed", full)

    # Reclaim shadowed collision residue (state dir only -- the
    # shared name-keyed unit belongs to the surviving winner and,
    # in a factory reset, is removed by the winner's own pass
    # above).
    for ref in shadowed_refs:
        sd = entity_state_dir(ref)
        if not sd.is_dir():
            continue
        if run_in_progress(sd):
            logger.warning(
                "%s: shadowed residue left in place (run in progress)", ref
            )
            continue
        destroy_one(None, sd)
        logger.info("%s: destroyed shadowed residue", ref)


def _applied_schedule_state(config: Config, full: str) -> str:
    """Return 'unresolved' / 'unscheduled' / 'scheduled' for `full`
    from the APPLIED snapshot.

    enable / disable arm or disarm the installed platform unit,
    which was rendered from the current (applied) snapshot -- so
    whether there's a timer to act on is the applied snapshot's
    schedule / interval, not the (possibly edited, not-yet-applied)
    pending config. `unresolved` covers a name with no current
    snapshot (a unit-only or broken remnant): nothing applied to
    read a schedule from. `unscheduled` is an applied entry with
    neither schedule nor interval (a grouped entry). `scheduled`
    has a schedule or interval.
    """
    # Resolve by uuid (current-first, pending-fallback) so a renamed-
    # but-not-applied entry addressed by its new name still reads its
    # applied snapshot at the unchanged uuid.
    ref = config.resolve_current(full) or config.resolve_pending(full)
    if ref is None:
        return "unresolved"
    snap: Job | JobGroup | None = config.current.jobs.get(
        ref
    ) or config.current.groups.get(ref)
    if snap is None:
        return "unresolved"
    if snap.timing is None:
        return "unscheduled"
    return "scheduled"


def _installed_refs(config: Config) -> set[EntityRef]:
    """The uuids with on-disk presence (a current snapshot, a broken
    snapshot, or a leftover platform unit) -- the set an action
    command can act on. Excludes pending-only entries (never applied).
    """
    return config.current.refs() | set(config.broken) | set(config.unit_only)


def _resolve_addressable(config: Config, full: str) -> EntityRef | None:
    """Resolve a user-supplied name to the single uuid it addresses
    for an action command (enable / disable / trigger / destroy).

    A rename keeps the uuid, so a not-yet-applied new name resolves to
    the same uuid as its installed old name; either is accepted.
    Raises `UsageError` when the name maps to different uuids in the
    pending and current graphs -- an unreconciled name swap that can't
    be disambiguated until `crony apply`. Returns None when neither
    graph knows the name (and it isn't a `<bundle>:<UUID>` address).
    """
    direct = EntityRef.from_str(full)
    if direct is not None:
        return direct
    pending_ref = config.resolve_pending(full)
    current_ref = config.resolve_current(full)
    if (
        pending_ref is not None
        and current_ref is not None
        and pending_ref != current_ref
    ):
        raise UsageError(
            f"{full!r} addresses {current_ref} on disk but {pending_ref} "
            f"in config; run `crony apply` to reconcile before addressing "
            f"it by name"
        )
    return current_ref or pending_ref


def _resolve_action_targets(
    config: Config, names: list[str], *, runnable_only: bool = False
) -> list[tuple[str, EntityRef, str]]:
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
    targets: list[tuple[str, EntityRef, str]] = []
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
        raise UsageError(f"{hint}: {sorted(unknown)} (run `crony apply` first)")
    return targets


def _resolve_bulk_names(
    jobs: list[str],
    bundle: str | None,
    stamped: set[str],
    config: Config,
    scheduled_only: bool,
) -> list[str]:
    """Derive the operate-on name set for enable / disable / trigger.

    With positional `jobs`, normalize each via `resolve_cli_name`
    (which honors `-b`'s bundle-scoping rules). With no positionals,
    `bundle` is required and the set expands to that bundle's
    stamped names; `scheduled_only` drops unscheduled entries (by
    their applied snapshot) so a bulk `enable -b foo` skips
    schedule-less group containers rather than rejecting the whole
    call.
    """
    if jobs:
        return [resolve_cli_name(arg, bundle) for arg in jobs]
    if bundle is None:
        raise UsageError("specify job names or --bundle")
    expanded = sorted(bundle_prefix_filter(stamped, bundle))
    if scheduled_only:
        expanded = [
            n
            for n in expanded
            if _applied_schedule_state(config, n) == "scheduled"
        ]
    return expanded


def _refuse_unscheduled_full(
    config: Config, full_names: list[str], verb: str
) -> None:
    """Reject entries with no schedule (nothing to enable/disable)."""
    unscheduled = [
        full
        for full in full_names
        if _applied_schedule_state(config, full) == "unscheduled"
    ]
    if unscheduled:
        raise UsageError(
            f"cannot {verb} grouped entries: {sorted(unscheduled)}. "
            f"Grouped entries are triggered by parent jobs with their "
            f"own schedule."
        )


def do_enable(jobs: list[str], bundle: str | None) -> None:
    """Move the platform scheduler into `enabled` for the named jobs.

    Names are full namespaced (`<bundle>.<short>`); bare input is
    shorthand for `default.<short>`. A rename (same uuid, new config
    name) is addressable by either name; a name mapping to different
    uuids in config vs on disk is rejected until `crony apply`.
    Refuses names not stamped on this host and ones with no
    schedule (no platform timer to enable / disable).

    With `--bundle <name>` and no positional args, enables every
    scheduled stamped entry in `<name>` (unscheduled entries in
    that bundle are skipped, not rejected). With `--bundle` plus
    positional args, bare names resolve in `<name>` and qualified
    names must match it.
    """
    config = load_config()
    config.require_addressable(bundle)
    installed = config.installed_full_names()
    normalized = _resolve_bulk_names(
        jobs, bundle, installed, config, scheduled_only=True
    )
    targets = _resolve_action_targets(config, normalized)
    _refuse_unscheduled_full(config, normalized, "enable")
    for full, _ref, unit_name in targets:
        _enable_unit(unit_name)
        logger.info("%s: enabled", full)


def do_disable(jobs: list[str], bundle: str | None) -> None:
    """Move the platform scheduler into `disabled` for the named jobs.

    Names are full namespaced (`<bundle>.<short>`); bare input is
    shorthand for `default.<short>`. A rename (same uuid, new config
    name) is addressable by either name; a name mapping to different
    uuids in config vs on disk is rejected until `crony apply`.
    Refuses names not stamped on this host and ones with no
    schedule (no platform timer to enable / disable).

    With `--bundle <name>` and no positional args, disables every
    scheduled stamped entry in `<name>` (unscheduled entries in
    that bundle are skipped, not rejected). With `--bundle` plus
    positional args, bare names resolve in `<name>` and qualified
    names must match it.
    """
    config = load_config()
    config.require_addressable(bundle)
    installed = config.installed_full_names()
    normalized = _resolve_bulk_names(
        jobs, bundle, installed, config, scheduled_only=True
    )
    targets = _resolve_action_targets(config, normalized)
    _refuse_unscheduled_full(config, normalized, "disable")
    for full, _ref, unit_name in targets:
        _disable_unit(unit_name)
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

    With `--bundle <name>` and no positional args, triggers every
    stamped entry in `<name>` (including schedule-less ones, since
    trigger is meaningful for them too). With `--bundle` plus
    positional args, bare names resolve in `<name>` and qualified
    names must match it.
    """
    if trigger_timeout is not None and not wait:
        raise UsageError(
            "--trigger-timeout requires --wait (only meaningful in "
            "synchronous mode)"
        )
    config = load_config()
    bundles = config.toml_config
    config.require_addressable(bundle)
    stamped = config.installed_full_names()
    normalized = _resolve_bulk_names(
        jobs, bundle, stamped, config, scheduled_only=False
    )
    targets = _resolve_action_targets(config, normalized, runnable_only=True)

    if not wait:
        for full, ref, unit_name in targets:
            # Fire the unit under its installed (current) name; the
            # user-trigger flag lands in the uuid-keyed state dir.
            crony.runner.trigger_unit(
                unit_name,
                triggered_by_user=True,
                state_dir=entity_state_dir(ref),
            )
            logger.info("%s: triggered", full)
        return

    # Synchronous waiter mode. Resolve per-name timeouts via each
    # name's bundle; bundles can disagree about defaults so we look
    # up trigger_timeout_sec per-bundle too.
    worst_rc = 0
    for full, ref, unit_name in targets:
        bn, short = parse_full_name(full)
        b = bundles.by_name(bn)
        if b is None:
            raise UsageError(
                f"unknown bundle for {full!r} (apply may be stale)"
            )
        if short not in b.config.jobs and short not in b.config.job_groups:
            # Installed on disk but no longer in the config (an
            # orphan). A plain `trigger` still fires it, but --wait
            # resolves per-name timeouts from the config, which no
            # longer describes it -- refuse with a clear message
            # rather than a raw KeyError.
            raise UsageError(
                f"{full!r} is installed but not in the current config "
                f"(apply may be stale -- re-apply or `crony destroy` "
                f"it); --wait cannot resolve its timeouts"
            )
        b_target = b.config.resolve_target()
        if short in b.config.jobs:
            timeout = timeout_to_wait(
                b.config.resolved_job_timeout_sec(b.config.jobs[short])
            )
        else:
            timeout = timeout_to_wait(
                b.config.resolved_group_timeout_sec(b_target, short)
            )
        tt = (
            float(trigger_timeout)
            if trigger_timeout is not None
            else float(b.config.defaults.trigger_timeout_sec)
        )
        rec = crony.runner.trigger_unit_sync(
            unit_name,
            state_dir=entity_state_dir(ref),
            job_timeout=timeout,
            trigger_timeout=tt,
            triggered_by_user=True,
        )
        cls = rec.get("exit_class", "ok")
        rc = trigger_exit_code(rec)
        logger.info("%s: %s (exit %s)", full, cls, rc)
        if rc and (not worst_rc or abs(rc) > abs(worst_rc)):
            worst_rc = rc
    if worst_rc:
        raise SystemExit(worst_rc)


def _build_status_tree(
    bundles: TomlConfig, host: str, platform: str
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
    `crony.config.TomlBundleConfig.from_raw`); under the single-parent
    invariant from validation it never deduplicates a real traversal.
    """
    order: list[str] = []
    depth: dict[str, int] = {}

    def _walk(
        bundle: TomlBundle,
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


_STATUS_COL_HEADERS: dict[str, str] = {
    "job": "JOB",
    "job-or-uuid": "JOB / UUID",
    "kind": "KIND",
    "config": "CONFIG",
    "schedule": "SCHEDULE",
    "groups": "GROUPS",
    "unit": "UNIT",
    "last": "LAST",
    "last-ran": "LAST RAN",
    "masked-by": "MASKED BY",
    "unit-name": "UNIT NAME",
    "uuid": "UUID",
    "unit-config": "UNIT CONFIG",
    "unit-timer": "UNIT TIMER",
}
DEFAULT_STATUS_COLS: tuple[str, ...] = (
    "job-or-uuid",
    "config",
    "schedule",
    "last",
    "last-ran",
)
_STATUS_COL_ALIASES: dict[str, tuple[str, ...]] = {
    "default": DEFAULT_STATUS_COLS,
    "all": tuple(_STATUS_COL_HEADERS.keys()),
    "unit-files": ("unit-config", "unit-timer"),
}


_STATUS_HELP_EPILOG_TEMPLATE: str = """\
Columns
-------
  config            synced | stale | missing | orphan | masked | error.
                    `error` flags an entry whose bundle config was
                    rejected (e.g. unknown key); the installed unit,
                    if any, is left untouched and `crony apply`
                    refuses the name until the config is fixed.
  groups            Comma-separated full names of groups containing this
                    entry. Same source rules as `schedule`: default and
                    --config-pending show the pending membership,
                    --config-current the applied one, flagged with `^`
                    when the two diverge. Empty when the entry isn't a
                    member of any group.
  job               Full namespaced name `<bundle>.<short>`. Opt-in. Always
                    the name, even for a row whose name collides with another
                    entry's (so you can see what the name was). Flagged with
                    `^` when the config and applied names differ (a not-yet-
                    applied rename). Empty for a broken entry with no
                    recoverable name.
  job-or-uuid       Identity column shown by default. The full namespaced
                    name when it unambiguously identifies the row; the
                    `<bundle>:<UUID>` form when the row has no recoverable
                    name (a corrupt snapshot) or its name is shadowed by a
                    collision -- so the cell is (modulo a trailing `^`)
                    pasteable into `crony destroy` / `crony logs`. A rename
                    (same uuid, new config name) is one row, shown under its
                    config name by default / --config-pending and its applied
                    name under --config-current, flagged with `^` since the
                    two names differ. Rows in an active target's
                    dispatch tree are indented two spaces per group-nesting
                    level and ordered by the target / group `jobs` lists
                    (execution order). Off-tree rows (orphans, names not in
                    any active target) follow below, unindented, sorted.
  kind              "job" or "group". Source-selected; flagged with `^` on
                    the rare divergence (a uuid redefined from one kind to
                    the other). Falls back to the snapshot's recorded kind
                    for rows whose live config no longer defines the entry.
  last              Last-run outcome: ok | fail | timeout | gated | canceled
                    | running | pending | never | unknown.
  last-ran          Compact relative time of the last run, e.g. "5m ago".
  masked-by         `host` and / or `platform` joined with `,`
                    (e.g. `host,platform` when both axes exclude
                    the entry), or one of `unused` / `empty`. Set
                    when the entry is filter-excluded on this host
                    -- which surfaces as `config=masked` for
                    entries with no on-disk state, and as
                    `config=orphan` for entries with leftover
                    units / state dirs from a prior apply
                    (the same `destroy --orphans` cleanup path
                    applies in both cases). `unused` means defined
                    in config but not listed in the host's
                    resolved target.jobs. `empty` means a group
                    whose every direct child is itself masked on
                    this host -- the reference is a no-op here,
                    so the group is masked too.
  schedule          Default mode shows the pending (config) schedule. When
                    the applied schedule differs, the cell carries a
                    trailing `^` (no space) and a footer prints below the
                    table pointing at `crony apply`. --config-current shows
                    the applied schedule, --config-pending the pending one;
                    the `^` fires on any divergence regardless of mode.
                    When the unit is disabled at the platform scheduler
                    the cell renders as `disabled` -- the cron expression
                    is misleading there since the unit won't fire. Add the
                    opt-in `unit` column to see the platform-scheduler
                    state for the rest of the rows. --config-pending
                    suppresses this override (the pending schedule is
                    a config fact, independent of runtime state).
  unit              Platform scheduler view: enabled | disabled | grouped
                    | none. `grouped` means the entry has no schedule
                    (parent group dispatches it); `none` means the
                    scheduler doesn't see a unit by this name.
  unit-name         Platform unit identifier: `org.crony.<name>` on macOS;
                    `crony-<name>.timer` (scheduled) or
                    `crony-<name>.service` (grouped) on Linux. Source-
                    selected like `schedule`; flagged with `^` when the
                    config and applied labels differ -- a not-yet-applied
                    rename, or (on Linux) a schedule gained/lost that
                    flips `.timer` <-> `.service`. Empty when neither
                    config nor snapshot describes the entry.
  uuid              The entry's stable identity in `<bundle>:<UUID>` form
                    (directly pasteable into `crony destroy` / `crony logs`).
                    Sourced from the live config when the entry is still
                    defined, else from the on-disk snapshot for orphan rows.
                    Empty when neither side has a uuid to report.
  unit-config       Filesystem path of the platform config unit -- the
                    unit that defines and runs the job
                    (`org.crony.<name>.plist` on macOS;
                    `crony-<name>.service` on Linux). Empty when no
                    config unit exists on disk.
  unit-timer        Filesystem path of the schedule-arming timer unit
                    (`crony-<name>.timer` on Linux). Always empty on
                    macOS (the plist carries its own schedule) and for
                    an unscheduled / grouped entry.

Aliases
-------
{aliases_block}

Color
-----
  When stdout is a TTY (and NO_COLOR is unset), broken / failed states
  are red -- CONFIG `missing` / `error` / `broken` / `orphan` and LAST
  `fail` / `timeout` / `canceled` -- and reconcilable drift is yellow --
  a `stale` CONFIG verdict and any cell carrying the `^` divergence
  marker (the `^` itself stays uncolored). Redirected or piped output
  is always plain.
"""


def _build_status_aliases_block() -> str:
    """Render the Aliases help block from `_STATUS_COL_ALIASES`.

    Each alias becomes one line: `  <name>  <expansion>`. `all`
    is rendered with a descriptive label instead of the full
    column list (which would just enumerate everything in the
    Columns section above) so the block stays scannable.
    """
    lines: list[str] = []
    for alias, expansion in _STATUS_COL_ALIASES.items():
        if alias == "all":
            label = "all"
        else:
            label = ", ".join(expansion)
        lines.append(f"  {alias:<8}  {label}")
    return "\n".join(lines)


STATUS_HELP_EPILOG: str = _STATUS_HELP_EPILOG_TEMPLATE.format(
    aliases_block=_build_status_aliases_block(),
)


def _parse_status_cols(spec: str | None) -> list[str]:
    """Parse the `--cols` argument into an ordered column list.

    Comma-separated; whitespace around names is ignored.
    `job-or-uuid` is always included (and forced to the first
    column) because everything else is meaningless without an
    entity identity, and it's the one column guaranteed to be
    pasteable back into `crony destroy` even for nameless or
    name-shadowed rows. `default` and `all` are aliases that
    expand to the default column set and every column
    respectively; mixing aliases with explicit names is allowed
    (`default,masked-by`). Order is preserved across the resolved
    list with duplicates dropped. Unknown names raise UsageError
    so a typo is loud, not a silent missing column.
    """
    if not spec:
        return list(DEFAULT_STATUS_COLS)
    raw = [c.strip() for c in spec.split(",") if c.strip()]
    valid = set(_STATUS_COL_HEADERS) | set(_STATUS_COL_ALIASES)
    unknown = [c for c in raw if c not in valid]
    if unknown:
        raise UsageError(
            f"unknown status column(s): {sorted(unknown)} "
            f"(valid: {sorted(_STATUS_COL_HEADERS)}; "
            f"aliases: {sorted(_STATUS_COL_ALIASES)})"
        )
    expanded: list[str] = []
    for c in raw:
        if c in _STATUS_COL_ALIASES:
            expanded.extend(_STATUS_COL_ALIASES[c])
        else:
            expanded.append(c)
    seen: set[str] = set()
    cols: list[str] = []
    for c in expanded:
        if c in seen:
            continue
        seen.add(c)
        cols.append(c)
    if "job-or-uuid" in cols:
        cols.remove("job-or-uuid")
    return ["job-or-uuid"] + cols


def resolve_state_axes(
    config: Config,
    full: str,
    remnants: set[str],
    *,
    mask_reason: str = "",
) -> tuple[str, str, str]:
    """Compute the (cfg, unit, last) status axes for one full name.

    `do_status` is the only consumer; the function is factored
    out so the axis derivation has a single home and the
    renderer-side filter (`--exclude-healthy`) reads the same
    triple as the default tree view. Returns the values straight
    from the underlying state readers -- no opinion about whether
    a given combination is "bad" -- so the caller applies its own
    filtering / display logic on top.

    CONFIG-axis precedence:

        error / broken  >  orphan  >  masked  >  synced / stale / missing

    `error` and `broken` are top-of-chain. `error` reports an
    entry whose TOML failed to parse; `broken` reports an entry
    whose on-disk snapshot can't be loaded. Either way the
    operator's next action is "fix this specific thing," and the
    mask-reason / synced-stale-missing axes are uninteresting
    until then.

    The synced / stale / missing / orphan / broken base comes
    from `_config_axis`, which scores the entity against the
    in-memory `Config` (no disk re-query). `mask_reason` is the
    entry's filter-exclusion reason on this host; when non-empty,
    the cfg axis becomes `"orphan"` if on-disk remnants exist and
    `"masked"` otherwise. The orphan-on-mask rule encodes
    "cleanup needed wins over passive-not-for-this-host."
    """
    bn, short = parse_full_name(full)
    bundle = config.toml_config.by_name(bn)
    entry: TomlJob | TomlJobGroup | None = None
    if bundle is not None and (
        short in bundle.config.errored_jobs
        or short in bundle.config.errored_job_groups
    ):
        # An errored entry never parsed into a uuid, so it has no
        # ref to score against the graphs; `error` is top-of-chain
        # and surfaces by name.
        cfg_state = "error"
    else:
        if bundle is not None:
            entry = bundle.config.jobs.get(
                short
            ) or bundle.config.job_groups.get(short)
        cfg_state = _config_axis(config, full, entry, bn)
    if mask_reason and cfg_state not in ("error", "broken"):
        cfg_state = "orphan" if full in remnants else "masked"
    # Schedule shape and the installed-unit name come from the entity
    # by uuid, not by `full`: a rename keeps the uuid, so the grouped
    # check reads the graph node and the platform query targets the
    # unit installed under the *current* (applied) name -- which may
    # differ from `full` for a not-yet-applied rename.
    ref = (
        EntityRef(bn, entry.uuid)
        if entry is not None
        else config.resolve_current(full) or config.resolve_pending(full)
    )
    pending_node = (
        config.pending.jobs.get(ref) or config.pending.groups.get(ref)
        if ref is not None
        else None
    )
    current_node = (
        config.current.jobs.get(ref) or config.current.groups.get(ref)
        if ref is not None
        else None
    )
    if cfg_state == "missing":
        unit_state = "none"
    else:
        sched_node = pending_node or current_node
        if sched_node is not None:
            grouped = sched_node.timing is None
        elif entry is not None:
            grouped = entry.timing is None
        else:
            grouped = False
        if grouped:
            unit_state = "grouped"
        else:
            installed_name = (
                str(current_node.name) if current_node is not None else full
            )
            unit_state = crony.runtime.unit_state(installed_name)
    last_state = last_run_state(config, full)
    return cfg_state, unit_state, last_state


def _entry_is_scheduled(entry: TomlJob | TomlJobGroup | None) -> bool:
    if entry is None:
        return False
    return entry.timing is not None


def _snapshot_says_scheduled(config: Config, full: str) -> bool | None:
    """`True` / `False` if the current-graph entry for `full`
    records schedule fields; `None` otherwise. Used to guess UNIT
    NAME for entries whose live config no longer exists.

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
    snap_j = config.current.jobs.get(ref)
    if snap_j is not None:
        return snap_j.timing is not None
    snap_g = config.current.groups.get(ref)
    if snap_g is not None:
        return snap_g.timing is not None
    return None


def _unit_name_for(
    full: str,
    entry: TomlJob | TomlJobGroup | None,
    config: Config,
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
    return scheduler(platform).unit_name(full, scheduled)


# Trailing flag on a status cell whose pending and current values
# diverge. `^` (not `*`) so it can't be mistaken for a schedule
# wildcard (`*-*-* 02:00`); appended with no separating space.
_DIVERGENCE_MARKER: str = "^"

_STALE_VALUE_FOOTER: str = (
    f"{_DIVERGENCE_MARKER} -- One or more flagged cells are stale: the "
    "pending config value (shown by default) differs from the applied "
    "one. Use --config-current to see the applied value, or run "
    "`crony apply` to bring the applied value in sync."
)

# ANSI color for the status table, emitted only when stdout is a TTY
# and NO_COLOR is unset (https://no-color.org/). Red flags a broken or
# failed state; yellow flags drift the operator can reconcile with
# `crony apply` (a `stale` config verdict, or any divergence-flagged
# cell). The `^` marker itself is never colored.
ANSI_RED: str = "\033[31m"
ANSI_YELLOW: str = "\033[33m"
ANSI_RESET: str = "\033[0m"

# CONFIG values worth a red flag. `last` is folded so `signal` never
# reaches the cell (it renders as `fail`); `timeout` / `canceled` are
# the other non-clean terminal outcomes.
_STATUS_RED_CONFIG: frozenset[str] = frozenset(
    {"missing", "error", "broken", "orphan"}
)
_STATUS_RED_LAST: frozenset[str] = frozenset({"fail", "timeout", "canceled"})


def color_supported() -> bool:
    """Return True if ANSI color escape sequences should be emitted.

    Color is suppressed when NO_COLOR is set in the environment or
    when stdout isn't a TTY -- redirecting to a file or piping into
    another process should produce plain text.
    """
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _status_value_color(col: str, value: str) -> str | None:
    """ANSI code for a status cell by (column, value), or None.

    Only the verdict columns carry a value-based color; the
    divergence marker is handled separately by the renderer.
    """
    if col == "config":
        if value in _STATUS_RED_CONFIG:
            return ANSI_RED
        if value == "stale":
            return ANSI_YELLOW
    elif col == "last" and value in _STATUS_RED_LAST:
        return ANSI_RED
    return None


def _render_status_cell(
    col: str, value: str, width: int, use_color: bool
) -> str:
    """Render one status cell padded to `width`, optionally colored.

    Width padding is computed from the visible text and kept outside
    the color codes so zero-width escapes never throw off column
    alignment. A divergence-flagged value (trailing `^`) is colored
    yellow on the value only -- the `^` and the padding stay plain.
    """
    pad = " " * max(0, width - len(value))
    if not use_color:
        return value + pad
    if value.endswith(_DIVERGENCE_MARKER):
        body = value[: -len(_DIVERGENCE_MARKER)]
        return f"{ANSI_YELLOW}{body}{ANSI_RESET}{_DIVERGENCE_MARKER}{pad}"
    code = _status_value_color(col, value)
    if code is None:
        return value + pad
    return f"{code}{value}{ANSI_RESET}{pad}"


def _build_group_membership(
    config: Config,
) -> tuple[dict[EntityRef, list[str]], dict[EntityRef, list[str]]]:
    """Reverse-index group membership from the pending and current
    graphs.

    Returns `(pending, current)` where `<table>[<child_ref>]` lists
    the full names of every group whose `children` list contains
    the child in that graph. The `children` lists hold uuids (the
    apply-time edge), so the table is keyed by the child's
    `EntityRef` -- a rename leaves the uuid unchanged, so the row
    still finds its membership regardless of which name it displays
    under. The parent names come from the same graph, so the two
    sides diverge when a group has been edited but not re-applied.
    Each value list is sorted for stable display order.
    """

    def _membership(graph: Graph) -> dict[EntityRef, list[str]]:
        table: dict[EntityRef, list[str]] = {}
        for parent in graph.groups.values():
            for child_uuid in parent.children:
                child_ref = EntityRef(parent.name.bundle, child_uuid)
                if (
                    child_ref not in graph.jobs
                    and child_ref not in graph.groups
                ):
                    continue
                table.setdefault(child_ref, []).append(str(parent.name))
        for v in table.values():
            v.sort()
        return table

    return _membership(config.pending), _membership(config.current)


def _build_config_group_membership(
    toml_config: TomlConfig,
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


def do_status(
    jobs: list[str],
    cols: str | None,
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
    schedule, groups, unit-name) to a single source; the `^`
    divergence flag still fires whenever the two sources differ. The
    two flags are mutually exclusive.

    `--exclude-healthy` drops rows where CONFIG is `synced`,
    UNIT is `enabled` (or `grouped` -- anything that fires), and
    LAST is `ok` / `never` / `gated`. A disabled unit is
    unhealthy (it isn't firing), so it survives the filter.
    Output is flat (no tree indent). Always exits 0 -- this is a
    filter on the display, not a gate.

    On a color-capable TTY (NO_COLOR unset) broken / failed verdicts
    render red and reconcilable drift renders yellow; see the
    `--help` epilog's Color section. Redirected output is plain.
    """
    if config_current and config_pending:
        raise UsageError(
            "--config-current and --config-pending are mutually exclusive"
        )
    config_source = (
        "current"
        if config_current
        else "pending"
        if config_pending
        else "default"
    )
    selected_cols = _parse_status_cols(cols)
    config = load_config()
    bundles = config.toml_config
    config.require_addressable(bundle)
    platform = config.platform
    _by_full, selected, masked_by_full = (
        _selected_and_masked_full_names_per_bundle(bundles)
    )
    remnants = config.installed_full_names()
    if bundle is not None:
        selected = bundle_prefix_filter(selected, bundle)
        remnants = bundle_prefix_filter(remnants, bundle)
        masked_by_full = {
            n: r
            for n, r in masked_by_full.items()
            if n.startswith(f"{bundle}.")
        }

    try:
        scheduler().verify()
    except SchedulerWarning as warn:
        logger.warning("%s", warn)

    # Surface bundle parse failures at the top of the report so
    # the operator sees them before scanning the table. The table
    # itself shows whatever is interpretable on-disk; entries
    # whose bundle didn't load show up as orphans or unit_only.
    if bundles.errored_bundles:
        print("bundle parse failures (config-side entries not loaded):")
        for src, msg in bundles.errored_bundles.items():
            print(f"  {src}: {msg}")
        print()

    full_names: list[str]
    if jobs:
        full_names = [resolve_cli_name(n, bundle) for n in jobs]
    else:
        errored_full = _errored_full_names(bundles, bundle)
        # Broken entries with no recoverable name, and current
        # entries whose name is shadowed by a collision, are
        # addressable only through the synthetic `<bundle>:<UUID>`
        # form -- their row carries that form in the JOB / UUID
        # cell. (A shadowed entry's plain name still belongs to its
        # collision winner, so adding the name to `active` would
        # only re-render the winner.)
        ref_form_only = {
            str(b.ref)
            for b in config.broken.values()
            if b.name is None and (bundle is None or b.bundle == bundle)
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
    names_by_ref: dict[EntityRef, list[str]] = {}
    refform_refs: set[EntityRef] = set()
    nameless_rows: list[str] = []
    for full in full_names:
        direct = EntityRef.from_str(full)
        if direct is not None:
            refform_refs.add(direct)
            names_by_ref.setdefault(direct, []).append(full)
            continue
        r = config.resolve_pending(full) or config.resolve_current(full)
        if r is None:
            bn0, short0 = parse_full_name(full)
            bdl0 = bundles.by_name(bn0)
            e0 = (
                bdl0.config.jobs.get(short0)
                or bdl0.config.job_groups.get(short0)
                if bdl0 is not None
                else None
            )
            if e0 is not None:
                r = EntityRef(bn0, e0.uuid)
        if r is None:
            nameless_rows.append(full)
        else:
            names_by_ref.setdefault(r, []).append(full)

    built: list[tuple[str, dict[str, str]]] = []
    any_stale = False

    def _build_row(ref: EntityRef | None, candidates: list[str]) -> None:
        nonlocal any_stale

        def _mark(pending_val: str | None, current_val: str | None) -> str:
            # A dual-source cell: pick the active source's value and
            # append the divergence flag when the two sides differ.
            value, diverged = _select_sourced(
                pending_val, current_val, config_source
            )
            if diverged:
                nonlocal any_stale
                any_stale = True
                value = f"{value}{_DIVERGENCE_MARKER}"
            return value

        pending_node: Job | JobGroup | None = (
            config.pending.jobs.get(ref) or config.pending.groups.get(ref)
            if ref is not None
            else None
        )
        current_node: Job | JobGroup | None = (
            config.current.jobs.get(ref) or config.current.groups.get(ref)
            if ref is not None
            else None
        )
        pending_name = (
            str(pending_node.name) if pending_node is not None else None
        )
        current_name = (
            str(current_node.name) if current_node is not None else None
        )
        if current_name is None and ref is not None:
            be = config.broken.get(ref)
            ue = config.unit_only.get(ref)
            current_name = (
                be.name
                if be is not None
                else ue.name
                if ue is not None
                else None
            )
        fallback = sorted(candidates)[0] if candidates else ""
        # The config (pending) name drives tree placement, masking,
        # and the TOML-entry lookup; the displayed identity is chosen
        # by source. A masked entry is in neither graph -- fall back to
        # the enumerated name.
        config_name = pending_name or fallback
        mask_reason = masked_by_full.get(config_name, "")
        bn, short = parse_full_name(config_name)
        bdl = bundles.by_name(bn)
        entry: TomlJob | TomlJobGroup | None = (
            bdl.config.jobs.get(short) or bdl.config.job_groups.get(short)
            if bdl is not None
            else None
        )
        # A ref-form row (shadowed collision loser, or a broken
        # snapshot with no recoverable name) renders by uuid in the
        # identity column; its plain name belongs to another entity.
        is_refform = ref is not None and ref in refform_refs

        pkind = (
            "group"
            if isinstance(pending_node, JobGroup)
            else "job"
            if pending_node is not None
            else None
        )
        ckind = (
            "group"
            if isinstance(current_node, JobGroup)
            else "job"
            if current_node is not None
            else None
        )
        if pkind is not None or ckind is not None:
            kind = _mark(pkind, ckind)
        elif isinstance(entry, TomlJobGroup):
            kind = "group"
        elif entry is not None:
            kind = "job"
        else:
            kind = config.current.kind_of(ref) or "" if ref is not None else ""

        # CONFIG / UNIT / LAST are single-source verdicts (not flag-
        # selected); resolve them against the config name so the
        # TOML-entry-based grouped check and errored detection land.
        cfg_state, unit_state, last = resolve_state_axes(
            config, config_name, remnants, mask_reason=mask_reason
        )
        last_ran = _last_ran_at(config, config_name)

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
        if config_source != "pending" and unit_state == "disabled":
            # A disabled timer won't fire on its schedule, so the cron
            # expression is misleading -- show the runtime fact, with no
            # divergence flag (the cell is no longer the schedule it
            # would compare against).
            sched_cell = "disabled"
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
            any_stale = True
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
            any_stale = True
            unit_name = f"{unit_name}{_DIVERGENCE_MARKER}"
        if is_refform:
            row_ref: EntityRef | None = ref
            job_cell = (config.name_for(ref) or "") if ref is not None else ""
            job_or_uuid_cell = str(ref)
        else:
            row_ref = ref
            job_cell = display_name
            job_or_uuid_cell = display_name
        uuid_cell = str(row_ref) if row_ref is not None else ""
        # `unit-config` / `unit-timer`: the platform unit paths captured
        # at load time (uuid-keyed RuntimeState). Empty for entries with
        # no runtime, and unit-timer is empty where the platform has no
        # separate timer unit / for an unscheduled entry.
        rt = config.runtime.get(row_ref) if row_ref is not None else None
        unit_config_cell = str(rt.unit_config) if rt and rt.unit_config else ""
        unit_timer_cell = str(rt.unit_timer) if rt and rt.unit_timer else ""
        built.append(
            (
                config_name,
                {
                    "job": job_cell,
                    "job-or-uuid": job_or_uuid_cell,
                    "kind": kind,
                    "config": cfg_state,
                    "schedule": sched_cell,
                    "groups": groups_cell,
                    "unit": unit_state,
                    "last": last,
                    "last-ran": last_ran,
                    "masked-by": mask_reason,
                    "unit-name": unit_name,
                    "uuid": uuid_cell,
                    "unit-config": unit_config_cell,
                    "unit-timer": unit_timer_cell,
                },
            )
        )

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
        # AND `unit` not in {none, disabled} AND `last` in
        # {ok, never, gated}. A disabled unit is unhealthy
        # because it isn't firing.
        healthy_last = {"ok", "never", "gated"}
        unhealthy_unit = {"none", "disabled"}
        rows = [
            r
            for r in rows
            if not (
                r["config"] == "synced"
                and r["unit"] not in unhealthy_unit
                and r["last"] in healthy_last
            )
        ]
        rows = sorted(rows, key=lambda r: r["job-or-uuid"])
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
            tree_row["job-or-uuid"] = indent + tree_row["job-or-uuid"]
            if tree_row["job"]:
                tree_row["job"] = indent + tree_row["job"]
            ordered.append(tree_row)
        off_tree = [row for _, row in built if id(row) not in consumed]
        for row in sorted(off_tree, key=lambda r: r["job-or-uuid"]):
            ordered.append(row)
        rows = ordered

    # Per-column width: max of the header label and the longest
    # cell, so a long "last-ran" value (`12d ago`) doesn't get
    # squeezed by the 8-char header.
    widths: dict[str, int] = {}
    for col in selected_cols:
        header = _STATUS_COL_HEADERS[col]
        cell_max = max((len(r[col]) for r in rows), default=0)
        widths[col] = max(len(header), cell_max)

    # Two-space separator: a single space ran headers and cells
    # together at narrow column widths (e.g. CONFIG=6 vs UNIT=8
    # produced columns visually flush against each other).
    sep = "  "
    header_line = sep.join(
        f"{_STATUS_COL_HEADERS[c]:<{widths[c]}}" for c in selected_cols
    )
    print(header_line.rstrip())
    use_color = color_supported()
    for row in rows:
        line = sep.join(
            _render_status_cell(c, row[c], widths[c], use_color)
            for c in selected_cols
        )
        print(line.rstrip())
    # The footer applies to any flagged cell. Suppress when the user
    # picked a column set that hides every column that can carry the
    # divergence marker.
    flaggable_cols = {
        "job",
        "job-or-uuid",
        "kind",
        "schedule",
        "groups",
        "unit-name",
    }
    if any_stale and any(c in selected_cols for c in flaggable_cols):
        print()
        print(_STALE_VALUE_FOOTER)


def do_logs(
    name: str,
    n: int | None,
    since: str | None,
    tail: bool,
    path: bool,
    latest: bool,
) -> None:
    """Print a job's recent log output.

    `name` is the full namespaced name (`<bundle>.<short>`); bare
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
    full = normalize_full_name(name)
    try:
        config = load_config()
    except ConfigError:
        config = None
    sd: Path | None = None
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
            or EntityRef.from_str(full)
        )
        if ref is not None:
            # Derive the state dir directly from the ref. This
            # path is what `RuntimeState.state_dir` would
            # contain when an entry is in `config.runtime`, and
            # it's also defined for refs that aren't
            # (`<bundle>:<UUID>` input whose snapshot is
            # unparseable, pre-apply entries, broken / future
            # unit-only orphans). The `log_path.exists()` check
            # below handles "path is well-defined but no log
            # there yet".
            sd = entity_state_dir(ref)
    if sd is None:
        raise UsageError(f"no log for {full!r} (no applied state on this host)")
    log_path = sd / "run.log"
    if path:
        print(log_path)
        return
    if not log_path.exists():
        raise UsageError(f"no log for {full!r} (state dir: {sd})")
    if tail and latest:
        raise UsageError("--tail and --latest are mutually exclusive")
    if n is None:
        n = 10 if tail else 200
    if tail:
        follow_log(log_path, n=n)
        return
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if latest:
        # `--latest` is a single-run readout; it overrides -n /
        # --since since the entry is bounded by run-header
        # markers, not line count or wallclock.
        text = extract_latest_log_entry(text)
    else:
        if since is not None:
            text = _filter_since(text, since)
        if n > 0:
            lines = text.splitlines()
            if len(lines) > n:
                text = "\n".join(lines[-n:]) + "\n"
    sys.stdout.write(text)
    sys.stdout.flush()


def follow_log(log_path: Path, *, n: int = 0) -> None:
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
                # readlines() over f then slicing is fine at log
                # sizes crony manages (run.log rotates per
                # `log_keep_runs`); a streaming reverse-read
                # would matter for multi-GB files we don't
                # produce.
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


def parse_since(spec: str) -> datetime.datetime:
    """Parse a --since argument as duration shorthand or ISO timestamp.

    Returns a tz-aware datetime. ISO inputs without an offset are
    rejected here -- comparing them against the runner's tz-aware
    run-header timestamps would raise TypeError mid-filter, so we
    surface the issue at parse time as a UsageError instead.
    """
    text = spec.strip()
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
        raise UsageError(
            f"unparseable --since value: {spec!r} "
            f"(use NUMs/m/h/d or ISO timestamp)"
        ) from e
    if ts.tzinfo is None:
        raise UsageError(
            f"--since {spec!r} is missing a timezone offset; "
            f"use a form like 2026-04-01T12:00:00-07:00"
        )
    return ts


def _filter_since(text: str, since: str) -> str:
    """Drop log content older than --since by run-header timestamp."""
    cutoff = parse_since(since)
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
    bundle: TomlBundle,
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
    for t in list(config.platform_targets.values()) + list(
        config.host_targets.values()
    ):
        if t.notify_channels is not None:
            listed.update(t.notify_channels)

    for ch_name in sorted(listed):
        if ch_name == NOTIFY_INHERIT_TOKEN:
            # The inherit sentinel resolves to the default bundle's
            # channels; their secrets are checked when that bundle is
            # validated, not here.
            continue
        if ch_name in BUILTIN_NOTIFY_CHANNELS:
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
                secret = retrieve_secret(
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
            except PreconditionError as e:
                warnings.append(str(e))
        elif channel.transport == "ntfy":
            assert channel.ntfy is not None
            label = f"channel {ch_name!r}: ntfy token"
            try:
                token = retrieve_secret(
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
            except PreconditionError as e:
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
    error; prints `ok` and returns when clean.
    """
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    if path.resolve() == crony.paths.CONFIG_FILE.resolve():
        name = DEFAULT_BUNDLE_NAME
    else:
        name = path.stem
    validate_bundle_name(name, str(path))
    # TomlBundle.load logs each per-entity error and raises on a
    # bundle-level structural failure; the errored_* counts catch the
    # per-entity cases so this exits non-zero on either.
    bundle = TomlBundle.load(name, path)
    cfg = bundle.config
    errored = (
        len(cfg.errored_jobs)
        + len(cfg.errored_job_groups)
        + len(cfg.errored_platform_targets)
        + len(cfg.errored_host_targets)
    )
    if errored:
        raise ConfigError(
            f"{path}: {errored} invalid config "
            f"{'entry' if errored == 1 else 'entries'}"
        )
    print(f"ok: {path} validates as bundle {name!r}")


def do_validate(bundle: str | None, file: str | None) -> None:
    """Lint configs; report linger status and broken secret files.

    TomlConfig.load_all already enforces per-bundle structural rules
    and isolates failed bundles. This subcommand surfaces linger /
    per-bundle warnings as informational output and exits WARNING
    (1) when any are present, CONFIG (3) when no bundles load.
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
    bundles = TomlConfig.load_all()
    bundles.require_known(bundle)

    warnings: list[str] = []
    if bundle is None:
        try:
            scheduler().verify()
        except SchedulerWarning as warn:
            warnings.append(str(warn))

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
        for w in _validate_bundle_warnings(b):
            warnings.append(f"{b.source}: {w}")

    print(f"bundles loaded: {len(target_bundles)}")
    for b in target_bundles:
        config = b.config
        errored = (
            len(config.errored_jobs)
            + len(config.errored_job_groups)
            + len(config.errored_platform_targets)
            + len(config.errored_host_targets)
        )
        errored_suffix = f", errored={errored}" if errored else ""
        print(
            f"  {b.name} ({b.source}): "
            f"jobs={len(config.jobs)}, groups={len(config.job_groups)}, "
            f"targets: platform={len(config.platform_targets)} "
            f"host={len(config.host_targets)}{errored_suffix}"
        )
    if warnings:
        print("warnings:")
        for w in warnings:
            print(f"  - {w}")
        # WARNING is not raisable as a CronyError; SystemExit forwards
        # the code through cli() unchanged.
        raise SystemExit(int(ExitCode.WARNING))
    print("ok")


def _notify_test_one_bundle(
    bundle: TomlBundle, channel: str | None, bundles: TomlConfig
) -> tuple[list[str], list[tuple[str, NotificationResult]], str | None]:
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
    synthetic_job = TomlJob(
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
    resolved, eff_defaults = expand_notify_inherit(
        resolved, bundle.name, bundles, config.defaults
    )
    # `expand_notify_inherit` swaps in the default bundle's Defaults
    # object only when it actually expands the inherit sentinel, so an
    # identity change means the channels (and their definitions) came
    # from the default bundle.
    inherited_from = (
        DEFAULT_BUNDLE_NAME if eff_defaults is not config.defaults else None
    )
    use_channels = [channel] if channel is not None else resolved
    if not use_channels:
        return ([], [], inherited_from)
    result = JobRunResult(
        host=crony.platform.current_host(),
        platform=crony.platform.current_platform(),
        started_at=now_iso(),
        ended_at=now_iso(),
        duration_sec=0.0,
        exit_class="fail",
        exit_code=1,
        signal=None,
        gate="none",
        log_path="(synthetic)",
        log_bytes_this_run=0,
        notifications={
            ch: NotificationResult(sent=False) for ch in use_channels
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
    bundles = TomlConfig.load_all()

    # Resolve --channel into (bundle_from_channel, channel_or_None).
    channel_bundle: str | None = None
    channel_short: str | None = None
    if channel is not None:
        if "." in channel:
            channel_bundle, channel_short = parse_full_name(channel)
        else:
            channel_short = channel

    # Compose --bundle and --channel's bundle prefix.
    if channel_bundle is not None and bundle is not None:
        if channel_bundle != bundle:
            raise UsageError(
                f"--bundle {bundle!r} contradicts --channel "
                f"{channel!r} (bundle {channel_bundle!r}); pick one"
            )
    bundle_name = channel_bundle or bundle or DEFAULT_BUNDLE_NAME
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
    all_failures: list[tuple[str, str, NotificationResult]] = [
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
        raise ConfigError(f"notify-test failed: {detail}")
    raise CronyError(f"notify-test failed: {detail}")


def do_self_test(*, verbose: bool, coverage: bool) -> int:
    """Run tests by invoking the test file directly."""
    repo_root = _repo_root()
    test_file = repo_root / "tests" / "test_crony.py"
    cmd = [str(test_file)]
    if verbose:
        cmd.append("--verbose")
    if coverage:
        cmd.append("--coverage")
    return subprocess.run(cmd, cwd=repo_root).returncode
