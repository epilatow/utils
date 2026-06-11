# This is AI generated code

"""crony's command handlers.

The do_* verbs behind the CLI -- apply / destroy / enable / disable /
trigger / status / logs / config / validate / notify-test -- plus the
name-resolution and apply-ordering helpers and the status renderer (its
column model, divergence and color handling, and per-axis state
derivation). This is the in-process API a caller drives instead of
shelling out to the crony CLI; it orchestrates the lower layers
(config, model, runtime, notify, runner). The per-entry on-disk unit
lifecycle itself (apply_one / destroy_one) lives in crony.runtime.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import os
import re
import subprocess as subprocess  # noqa: PLC0414  re-exported for tests
import sys as sys  # noqa: PLC0414  re-exported for tests
import time as time  # noqa: PLC0414  re-exported for tests
import uuid
from pathlib import Path

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

logger = logging.getLogger(__name__)


# =============================================================================
# DEFAULT CONFIG TEMPLATE
# =============================================================================
# Emitted by `crony config init`. Every section is commented out so a user can
# uncomment the bits they want without touching the explanatory prose.

_DEFAULT_CONFIG_TEMPLATE: str = """\
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
# notify-channels      = []           # names defined below, or "dialog-popup"
# notify-attach-log    = true         # include log content in notifications
# notify-attach-max-kb = 256          # email cap (ntfy uses a 3 KB cap)
# job-timeout-sec      = 1800         # per-job wallclock cap; 0 = no cap
# trigger-timeout-sec  = 15           # `crony trigger --wait` deadline
# log-keep-runs        = 30
# priority             = "high"       # priority class; jobs may override
# keep-awake           = true         # default; jobs may override
# # flags = ["keep-awake"]            # capability flags set here cascade
# #            to every job; a group or a job overrides with its own
# #            `flags` ("flag=false" turns one off). Same flags as the
# #            per-job example below. Settable at [defaults],
# #            [job-group.*], and [job.*].
#
# [defaults.env]
# PATH = "$HOME/.local/bin:$PATH"      # merged under every job; a job key wins

# Notification inheritance. The reserved channel name `default` in a
# NON-default bundle's `notify-channels` pulls in the default bundle's
# channel list, definitions, and attach settings. Alone
# (`notify-channels = ["default"]`) the bundle notifies exactly as the
# default bundle would, so it can omit every [defaults.notify.*] block
# of its own. Combined with explicit siblings
# (`notify-channels = ["default", "dialog-popup"]`) it notifies as the
# default bundle would PLUS those channels -- de-duped, so a channel in
# both fires once. Those siblings must be channels defined in THIS
# bundle or zero-config built-ins (e.g. dialog-popup); the sentinel
# already pulls in the whole default set, so there is no need to
# re-list the default bundle's channels by name. `default` is also the
# implicit default: a
# non-default bundle that says nothing about notify config inherits the
# default bundle's. Opt back out with an explicit `notify-channels = []`
# (silence). The default bundle cannot inherit itself, and `default` is
# a reserved channel name (no [defaults.notify.default] block).

# Each notify channel is a [defaults.notify.<name>] block. The name
# is whatever the user lists in `notify-channels`. The `transport`
# field selects the underlying sender ("email", "ntfy", or
# "dialog-popup"); when omitted, transport defaults to the channel
# name (so a block named `email`, `ntfy`, or `dialog-popup` picks up
# its like-named transport automatically). Optional `headers` is a
# table of arbitrary message headers crony attaches to email / ntfy
# -- crony-controlled headers (To/From/Subject for email;
# Authorization/Tags/Title for ntfy) are reserved and rejected.
#
# `dialog-popup` is a zero-config built-in -- it needs no block at
# all. Just list "dialog-popup" in `notify-channels` and a failing
# job pops a native desktop dialog (macOS) showing the failure
# summary and latest log. An explicit block is allowed but only ever
# sets `transport`.

# Embedded SMTP channel.
# [defaults.notify.email]
# # transport defaults to "email" since the channel is named "email"
# to                         = "you@example.com"
# from                       = "crony@example.com"
# smtp-host                  = "smtp.gmail.com"
# smtp-port                  = 587
# smtp-user                  = "you@gmail.com"
# smtp-starttls              = true
# # Password retrieval -- first match wins. On macOS prefer the
# # keychain item (the optional -account narrows the lookup when
# # multiple items share a service name); on Linux fall back to a
# # 0600 secrets file.
# smtp-pass-keychain-service = "crony-smtp"
# smtp-pass-keychain-account = "you@gmail.com"   # optional
# smtp-pass-file             = "~/.config/crony/secrets/smtp-password"
# headers                    = { "Reply-To" = "you@example.com" }

# ntfy channel.
# [defaults.notify.ntfy]
# url                    = "https://ntfy.example.com/automation"
# token-keychain-service = "ntfy-automation"
# token-keychain-account = "edp"                 # optional
# token-file             = "~/.config/crony/secrets/ntfy-token"

# Custom-named ntfy channel that also relays through ntfy's email
# integration. The transport field is required because the channel
# name `ntfy-email` doesn't match a built-in transport.
# [defaults.notify.ntfy-email]
# transport              = "ntfy"
# url                    = "https://ntfy.example.com/automation"
# token-keychain-service = "ntfy-automation"
# headers                = { "Email" = "you@example.com", "Priority" = "high" }

# Native desktop dialog (macOS). Zero-config -- normally you just put
# "dialog-popup" in notify-channels with no block at all; this
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
# # notify-channels = ["email"]   # or [] to silence just this job
# # job-timeout-sec = 7200        # 0 = no cap (job manages its own timeout)
# # priority = "high"             # process-priority class for the unit:
# #   "high"   un-throttle: macOS ProcessType=Interactive + normal CPU/IO
# #            (avoids the QoS throttling that slows IO-bound work); on
# #            Linux there is no such throttle to undo, so it has no
# #            runtime effect (recorded as a unit comment).
# #   "low"    throttle: macOS Background + low-priority IO + Nice 10;
# #            Linux Nice 10 + idle IO scheduling.
# #   "normal" (default) emit nothing. Applies on every fire path
# #            (scheduled, `crony trigger`, parent-group dispatch).
# # keep-awake = true            # hold a power assertion for the run so
# #            an idle / on-AC machine doesn't sleep mid-job. NOTE:
# #            closing the lid on battery still sleeps the machine --
# #            nothing in userspace prevents that.
# # flags = ["interactive", "keep-awake"]   # an alternative spelling for
# #            the per-flag booleans above; "flag=false" turns one off
# #            (e.g. ["keep-awake=false"]). A flag may be set by its own
# #            key OR in `flags`, never both at the same level.
# # success-exit-codes = [1]     # non-zero exit codes to classify as
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
# active continuously for `interactive-active` (default 10min).
# Interactive needs the macOS dialog, so the job runs only on
# darwin -- a non-darwin host silently skips it (and an explicit
# non-darwin `platforms` just never selects). May live in a
# [job-group.*]; the group dispatches it async (no wait) and the
# child's interactive wait runs independently of the group's deadline.
# [job.weekly-prompt]
# command            = "/usr/local/bin/some-interactive-task"
# schedule           = "Sun *-*-* 12:00"  # weekly at noon
# interactive        = true
# # Optional knobs (defaults shown):
# # interactive-active = "10min"          # required user-active window
# # interactive-delay  = "1h"             # sleep after "Delay Job"


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
# notify-channels = ["email"]

# [target.linux]
# jobs            = ["git-pull-utils"]
# notify-channels = ["ntfy"]

# [target.host.work-laptop]
# jobs            = ["brew-update", "git-pull-utils"]
# notify-channels = []   # no external dispatch (run.log + last-run.json only)
"""


def _schedule_display(timing: crony.unit.Timing | None) -> str:
    """Render a unit's timing into one status cell value.

    A Schedule shows its OnCalendar source; an Interval shows
    `interval=<spec>`. An entry with no timing -- a transit group or a
    group-only job -- displays as `grouped` to mirror the UNIT axis
    value for the same condition.
    """
    if isinstance(timing, crony.unit.Schedule):
        return str(timing)
    if isinstance(timing, crony.unit.Interval):
        return f"interval={timing}"
    return "grouped"


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


# Snapshot fields whose DIVERGED label isn't a plain underscore-to-dash
# translation of the dataclass attribute. `timing` serializes as
# `schedule`/`interval`; `timeout` is the `job-timeout-sec` config knob;
# the interactive `_sec` snapshot fields store resolved seconds but the
# config knobs take a time-span string (`interactive-active` /
# `interactive-delay`). Any field not listed falls back to its
# dash-translated attribute name (so `snapshot_schema` reads
# `snapshot-schema`, `state_dir_symlink` reads `state-dir-symlink`).
_DIVERGED_FIELD_LABELS: dict[str, str] = {
    "timing": "schedule",
    "timeout": "job-timeout-sec",
    "interactive_active_sec": "interactive-active",
    "interactive_delay_sec": "interactive-delay",
}


def _diverged_fields(
    pending: crony.model.Job | crony.model.JobGroup | None,
    current: crony.model.Job | crony.model.JobGroup | None,
    *,
    unit_stale: bool = False,
) -> str:
    """Comma-joined reasons a `config=stale` entry diverges: the
    snapshot fields that differ between the pending and applied
    versions, plus `unit` when the installed unit file has drifted from
    the snapshot (the two ways `config` reads stale).

    Only `compare=True` fields are diffed, mirroring the dataclass `==`
    the stale verdict itself uses. A config knob is reported by the name
    the config file uses for it (see `_DIVERGED_FIELD_LABELS`); any other
    field falls back to its dash-spelled attribute (`snapshot_schema` ->
    `snapshot-schema`, `state_dir_symlink` -> `state-dir-symlink`). The
    `flags` bitmask is expanded to the individual capability flags that
    changed (e.g. `keep-awake`) rather than the field name. Empty for a
    synced entry, or a stale verdict with no current snapshot to diff (a
    missing / unparseable remnant). A kind flip between job and group
    reports `kind`.
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
                else:
                    parts.append(
                        _DIVERGED_FIELD_LABELS.get(
                            f.name, f.name.replace("_", "-")
                        )
                    )
    if unit_stale:
        parts.append("unit")
    return ",".join(sorted(parts))


# =============================================================================
# RUNTIME STATE (enable / disable / status / linger)
# =============================================================================
# Runtime state is the platform scheduler's "will I fire this unit?"
# answer. It's orthogonal to CONFIG state: a unit can be `synced` with
# config but `disabled` at runtime because the user paused it. apply
# preserves this state across re-renders so a hand-disabled job stays
# off when the user runs `crony apply` to push other changes.


# Maps a recorded ExitClass to the JobStatus shown in the LAST cell:
# `signal` folds to `fail`; `dispatched` (absent here) and an
# unparseable class fall through to UNKNOWN at the call site.
_EXIT_TO_JOBSTATUS: dict[crony.model.ExitClass, crony.model.JobStatus] = {
    crony.model.ExitClass.OK: crony.model.JobStatus.OK,
    crony.model.ExitClass.FAIL: crony.model.JobStatus.FAIL,
    crony.model.ExitClass.SIGNAL: crony.model.JobStatus.FAIL,
    crony.model.ExitClass.TIMEOUT: crony.model.JobStatus.TIMEOUT,
    crony.model.ExitClass.GATED: crony.model.JobStatus.GATED,
    crony.model.ExitClass.CANCELED: crony.model.JobStatus.CANCELED,
}


def _last_run_state(
    config: crony.model.Config, full_name: str
) -> crony.model.JobStatus:
    """Return LAST axis value for a stamped entity.

    Lock-held implies a run is currently in flight: "pending" when
    the in-flight run is an interactive job sitting in its wait
    loop (signaled by a `pending.flag` file in the state dir),
    "running" otherwise. Otherwise "crashed" when the scheduler's
    last launch ended without recording a run -- killed, or exited
    before the runner wrote its record (so the last-run.json that
    remains is stale; see RuntimeState.crashed) -- then
    last-run.json's recorded exit_class. Absence of all of these
    means the entity has never run -- new install, group-only job
    that's never been triggered, etc.

    Resolution is current-first, pending-fallback. Runtime is
    uuid-keyed, and a rename keeps the uuid, so the run history
    lives at the same state dir under the new name. The current
    graph is keyed by the applied (old) name, so a not-yet-applied
    rename misses there; the pending graph carries the new name at
    the unchanged uuid and recovers the record. A genuinely new
    pending-only entry resolves to a uuid with no state dir, so
    `runtime.get` is None and it still reports "never".
    """
    js = crony.model.JobStatus
    ref = config.resolve_current(full_name) or config.resolve_pending(full_name)
    if ref is None:
        return js.NEVER
    rt = config.runtime.get(ref)
    if rt is None:
        return js.NEVER
    if rt.is_running:
        return js.PENDING if rt.is_pending else js.RUNNING
    if rt.crashed:
        return js.CRASHED
    if rt.last_run is None or rt.last_run.exit_class is None:
        return js.NEVER if rt.last_run is None else js.UNKNOWN
    return _EXIT_TO_JOBSTATUS.get(rt.last_run.exit_class, js.UNKNOWN)


def _last_ran_at(config: crony.model.Config, full_name: str) -> str:
    """Return a compact "when did this last run" string for status.

    Returns "never" when there's no last-run record and "unknown"
    when the recorded timestamp is unreadable or the recorded run
    was superseded by a launch that ended without recording (a
    crash, so the surviving timestamp is from an earlier run).
    Resolution is current-first, pending-fallback: runtime is
    uuid-keyed, so a not-yet-applied rename (new name, unchanged
    uuid) is recovered via the pending graph when the applied-name
    lookup misses.
    """
    ref = config.resolve_current(full_name) or config.resolve_pending(full_name)
    if ref is None:
        return "never"
    rt = config.runtime.get(ref)
    if rt is None:
        return "never"
    if rt.crashed:
        return "unknown"
    if rt.last_run is None:
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
    config: crony.model.Config,
    full: str,
    entry: crony.config.TomlJob | crony.config.TomlJobGroup | None,
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
        ref: crony.unit.EntityRef | None = crony.unit.EntityRef(bn, entry.uuid)
    else:
        ref = config.resolve_current(full) or config.resolve_pending(full)
    if ref is None or ref not in config.all_refs():
        # Nothing the in-memory model knows by this ref: an
        # in-config-but-host-masked entry (absent from the pending
        # graph) or a bare name with no on-disk state. "missing"
        # is the pre-mask base the mask layer turns into "masked".
        return "missing"
    state = config.config_state(ref)
    lingering = config.orphans_by_full_name.get(full)
    if (
        state == "missing"
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
    crony.runtime.scheduler(platform).enable(name)


def _disable_unit(name: str, platform: str | None = None) -> None:
    """Move the scheduler to the `disabled` state for `name` (delegates)."""
    crony.runtime.scheduler(platform).disable(name)


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
        if bundle == crony.config.DEFAULT_BUNDLE_NAME:
            raise crony.errors.UsageError(
                f"--bundle {crony.config.DEFAULT_BUNDLE_NAME!r} would shadow "
                f"config.toml; use plain `crony config init` (without "
                f"--bundle) for the default bundle"
            )
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
        target.write_text(_DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
        logger.info("wrote bundle config to %s", target)
        return
    if crony.paths.CONFIG_FILE.exists() and not force:
        raise crony.errors.UsageError(
            f"{crony.paths.CONFIG_FILE} already exists; "
            "pass --force to overwrite"
        )
    crony.paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    crony.paths.CONFIG_FILE.write_text(
        _DEFAULT_CONFIG_TEMPLATE, encoding="utf-8"
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
            # genuinely-unused ones at this layer; `_resolve_state_axes`
            # promotes them back to `error` so the user sees the
            # actual problem instead of a generic mask.
            masked_by_full[full] = "unused"
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

    for full in full_names_to_apply:
        ref = config.pending.by_full_name[full]
        result = crony.runtime.apply_one(config, ref)
        if verbose or result != "unchanged":
            logger.info("%s: %s", full, result)


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

    Also accepts the `<bundle>:<UUID>` input form so an operator can
    copy the JOB cell from a status row for an entity with no
    recoverable name (a broken snapshot or a snapshot-less leftover
    dir) and paste it here. The input validates iff it names a
    modeled on-disk entity -- a current node or an orphan. The
    entity's own name keys the platform-unit cleanup; a too-corrupt
    orphan carries none, so only its state dir is wiped.
    """
    if orphans and jobs:
        raise crony.errors.UsageError(
            "--orphans and positional names are mutually exclusive"
        )
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
        # A bare `destroy` factory-resets every crony-managed remnant;
        # `--orphans` limits it to entities the live config no longer
        # selects. Both go through the one reclamation `apply` shares.
        _reclaim(config, bundle, only_unselected=orphans)


def _applied_schedule_state(config: crony.model.Config, full: str) -> str:
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
    snap = config.current.job_from_ref(ref)
    if snap is None:
        return "unresolved"
    if snap.timing is None:
        return "unscheduled"
    return "scheduled"


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
    config: crony.model.Config,
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
        return [crony.config.resolve_cli_name(arg, bundle) for arg in jobs]
    if bundle is None:
        raise crony.errors.UsageError("specify job names or --bundle")
    expanded = sorted(crony.config.bundle_prefix_filter(stamped, bundle))
    if scheduled_only:
        expanded = [
            n
            for n in expanded
            if _applied_schedule_state(config, n) == "scheduled"
        ]
    return expanded


def _refuse_unscheduled_full(
    config: crony.model.Config, full_names: list[str], verb: str
) -> None:
    """Reject entries with no schedule (nothing to enable/disable)."""
    unscheduled = [
        full
        for full in full_names
        if _applied_schedule_state(config, full) == "unscheduled"
    ]
    if unscheduled:
        raise crony.errors.UsageError(
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
    config = crony.runtime.load_config()
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
    config = crony.runtime.load_config()
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
        raise crony.errors.UsageError(
            "--trigger-timeout requires --wait (only meaningful in "
            "synchronous mode)"
        )
    config = crony.runtime.load_config()
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
    `crony.config.TomlBundleConfig.from_raw`); under the single-parent
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
    "log-file": "LOG FILE",
    "flags": "FLAGS",
    "timeout": "TIMEOUT",
    "priority": "PRIORITY",
    "diverged": "DIVERGED",
}
# One opt-in column per capability flag, keyed by the flag's token, so
# the set tracks `JobFlags` automatically as members are added.
for _flag_member in crony.config.JobFlags.members():
    _STATUS_COL_HEADERS[_flag_member.token] = _flag_member.token.upper()
_DEFAULT_STATUS_COLS: tuple[str, ...] = (
    "job-or-uuid",
    "config",
    "schedule",
    "last",
    "last-ran",
)
_STATUS_COL_ALIAS_NAMES: tuple[str, ...] = ("default", "all", "unit-files")


def _expand_status_alias(
    name: str, *, masked_present: bool, platform: str
) -> tuple[str, ...]:
    """Expand a `--cols` alias to its column list for this context.

    `all` and `unit-files` trim columns that would only ever be blank
    here, so the wide views stay useful rather than padded with dead
    space:

    - `all` drops the per-flag columns (the compact `flags` column
      already carries them), `masked-by` when no displayed row carries
      a mask reason (`masked_present` is false), and `unit-timer` on
      darwin (launchd pins the schedule in the plist, so there is no
      timer file).
    - `unit-files` drops `unit-timer` on darwin for the same reason.

    Trimming applies only to the alias; a column named explicitly is
    always honored (`--cols all,unit-timer` still shows the timer).
    """
    if name == "default":
        return _DEFAULT_STATUS_COLS
    is_darwin = platform == "darwin"
    if name == "unit-files":
        if is_darwin:
            return ("unit-config",)
        return ("unit-config", "unit-timer")
    flag_tokens = {f.token for f in crony.config.JobFlags.members()}
    out: list[str] = []
    for col in _STATUS_COL_HEADERS:
        if col in flag_tokens:
            continue
        if col == "masked-by" and not masked_present:
            continue
        if col == "unit-timer" and is_darwin:
            continue
        out.append(col)
    return tuple(out)


_STATUS_HELP_EPILOG_TEMPLATE: str = """\
Columns
-------
  config            synced | stale | missing | orphan | masked | error.
                    `error` flags an entry whose bundle config was
                    rejected (e.g. unknown key); the installed unit,
                    if any, is left untouched and `crony apply`
                    refuses the name until the config is fixed.
  flags             Comma-separated capability flags enabled for the
                    entry (e.g. `interactive,keep-awake`). Opt-in.
                    Source-selected like `schedule`: default and
                    --config-pending list the pending (config) flags,
                    --config-current the applied ones; an enabled flag
                    is tagged `^` when the two sides disagree on it
                    (e.g. `interactive^,keep-awake`). Empty when no
                    flag is enabled, or for a row with no resolved view
                    (a masked or orphan entry absent from the graph). A
                    group shows its resolved cascade value -- the flags
                    it inherits and seeds into its children -- which has
                    no runtime effect on the group but makes inheritance
                    visible. A flag
                    disabled in config but still enabled in the applied
                    state (a not-yet-applied removal) isn't listed here
                    -- it surfaces in that flag's own column (as
                    `false^`) and in the stale footer.
  <flag>            One opt-in column per capability flag, each reading
                    true / false for whether the flag is enabled (one
                    column per member:
                    {flag_tokens}). Same source rules and `^`
                    divergence flag as the `flags` summary; a group
                    shows its inherited cascade value, the same as a
                    job. Request by name; the `all` alias omits these in
                    favor of the compact `flags` column.
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
                    | crashed | running | pending | never | unknown.
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
  log-file          Filesystem path of the entry's log file (the path
                    `crony logs <name>` reads). Opt-in. Source-selected
                    like `schedule`: default and --config-pending show
                    the config path, --config-current the applied one,
                    flagged with `^` when the two diverge (a not-yet-
                    applied rename).
  timeout           Entry wallclock cap: `<n>s`, or `none` for uncapped.
                    A job's job-timeout-sec or a group's cumulative
                    budget. Opt-in. Source-selected and `^`-flagged like
                    `schedule`.
  priority          Job scheduling priority: high | normal | low. Opt-in,
                    job-only (blank for groups). Source-selected and
                    `^`-flagged like `schedule`.
  diverged          Why a `stale` entry diverges: the snapshot fields
                    that differ between the config and applied versions,
                    a config knob named as the config file spells it
                    (e.g. `command,env,job-timeout-sec`), each changed
                    capability flag by its own token (e.g. `keep-awake`),
                    plus `unit` when the installed unit file has drifted
                    from the snapshot -- a direct answer to "why is this
                    stale?". Opt-in; blank for a synced entry, or a
                    stale verdict with no current snapshot to diff. Pair
                    with `--cols all` to see the diverging cells flagged
                    with `^`.

Aliases
-------
  default     {default_cols}
  all         Every column except the per-flag columns (use the compact
              `flags` instead), `masked-by` (kept only when a masked
              entry is present), and -- on macOS -- `unit-timer`
              (launchd has no timer file). Naming an excluded column
              explicitly still shows it.
  unit-files  unit-config, plus unit-timer on Linux.

Color
-----
  When stdout is a TTY (and NO_COLOR is unset), broken / failed states
  are red -- CONFIG `missing` / `error` / `broken` / `orphan` and LAST
  `fail` / `timeout` / `canceled` / `crashed` -- and reconcilable drift
  is yellow --
  a `stale` CONFIG verdict and any cell carrying the `^` divergence
  marker (the `^` itself stays uncolored). Redirected or piped output
  is always plain.
"""


STATUS_HELP_EPILOG: str = _STATUS_HELP_EPILOG_TEMPLATE.format(
    default_cols=", ".join(_DEFAULT_STATUS_COLS),
    flag_tokens=", ".join(f.token for f in crony.config.JobFlags.members()),
)


def _parse_status_cols(
    spec: str | None, *, masked_present: bool, platform: str
) -> list[str]:
    """Parse the `--cols` argument into an ordered column list.

    Comma-separated; whitespace around names is ignored.
    `job-or-uuid` is always included (and forced to the first
    column) because everything else is meaningless without an
    entity identity, and it's the one column guaranteed to be
    pasteable back into `crony destroy` even for nameless or
    name-shadowed rows. `default`, `all`, and `unit-files` are
    aliases expanded by `_expand_status_alias` (which trims columns
    that would be blank in this context); mixing aliases with
    explicit names is allowed (`default,masked-by`), and an
    explicitly named column is never trimmed. Order is preserved
    across the resolved list with duplicates dropped. Unknown names
    raise UsageError so a typo is loud, not a silent missing column.
    """
    if not spec:
        return list(_DEFAULT_STATUS_COLS)
    raw = [c.strip() for c in spec.split(",") if c.strip()]
    valid = set(_STATUS_COL_HEADERS) | set(_STATUS_COL_ALIAS_NAMES)
    unknown = [c for c in raw if c not in valid]
    if unknown:
        raise crony.errors.UsageError(
            f"unknown status column(s): {sorted(unknown)} "
            f"(valid: {sorted(_STATUS_COL_HEADERS)}; "
            f"aliases: {sorted(_STATUS_COL_ALIAS_NAMES)})"
        )
    expanded: list[str] = []
    for c in raw:
        if c in _STATUS_COL_ALIAS_NAMES:
            expanded.extend(
                _expand_status_alias(
                    c, masked_present=masked_present, platform=platform
                )
            )
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


def _resolve_state_axes(
    config: crony.model.Config,
    full: str,
    remnants: set[str],
    *,
    mask_reason: str = "",
) -> tuple[str, str, crony.model.JobStatus]:
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
        crony.unit.EntityRef(bn, entry.uuid)
        if entry is not None
        else config.resolve_current(full) or config.resolve_pending(full)
    )
    pending_node = config.pending.job_from_ref(ref) if ref is not None else None
    current_node = config.current.job_from_ref(ref) if ref is not None else None
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
                str(current_node.entity_name)
                if current_node is not None
                else full
            )
            unit_state = crony.runtime.unit_state(installed_name)
    last_state = _last_run_state(config, full)
    return cfg_state, unit_state, last_state


def _entry_is_scheduled(
    entry: crony.config.TomlJob | crony.config.TomlJobGroup | None,
) -> bool:
    if entry is None:
        return False
    return entry.timing is not None


def _snapshot_says_scheduled(
    config: crony.model.Config, full: str
) -> bool | None:
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
    snap = config.current.job_from_ref(ref)
    if snap is not None:
        return snap.timing is not None
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
_ANSI_RED: str = "\033[31m"
_ANSI_YELLOW: str = "\033[33m"
_ANSI_RESET: str = "\033[0m"

# CONFIG values worth a red flag. `last` is folded so `signal` never
# reaches the cell (it renders as `fail`); `timeout` / `canceled` /
# `crashed` are the other non-clean terminal outcomes (`crashed` = the
# launch ended without recording a run -- killed, or exited before the
# runner wrote its record).
_STATUS_RED_CONFIG: frozenset[str] = frozenset(
    {"missing", "error", "broken", "orphan"}
)
_STATUS_RED_LAST: frozenset[crony.model.JobStatus] = frozenset(
    {
        crony.model.JobStatus.FAIL,
        crony.model.JobStatus.TIMEOUT,
        crony.model.JobStatus.CANCELED,
        crony.model.JobStatus.CRASHED,
    }
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
    """ANSI code for a status cell by (column, value), or None.

    Only the verdict columns carry a value-based color; the
    divergence marker is handled separately by the renderer.
    """
    if col == "config":
        if value in _STATUS_RED_CONFIG:
            return _ANSI_RED
        if value == "stale":
            return _ANSI_YELLOW
    elif col == "last" and value in _STATUS_RED_LAST:
        return _ANSI_RED
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
        return f"{_ANSI_YELLOW}{body}{_ANSI_RESET}{_DIVERGENCE_MARKER}{pad}"
    code = _status_value_color(col, value)
    if code is None:
        return value + pad
    return f"{code}{value}{_ANSI_RESET}{pad}"


def _build_group_membership(
    config: crony.model.Config,
) -> tuple[
    dict[crony.unit.EntityRef, list[str]], dict[crony.unit.EntityRef, list[str]]
]:
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

    def _membership(
        graph: crony.model.Graph,
    ) -> dict[crony.unit.EntityRef, list[str]]:
        table: dict[crony.unit.EntityRef, list[str]] = {}
        for parent in graph.groups.values():
            for child_uuid in parent.children:
                child_ref = crony.unit.EntityRef(parent.bundle, child_uuid)
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
    schedule, groups, unit-name, log-file, flags and the per-flag
    columns) to a single source; the `^` divergence flag still fires
    whenever the two sources differ. The two flags are mutually
    exclusive.

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
        raise crony.errors.UsageError(
            "--config-current and --config-pending are mutually exclusive"
        )
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

        pkind = (
            "group"
            if isinstance(pending_node, crony.model.JobGroup)
            else "job"
            if pending_node is not None
            else None
        )
        ckind = (
            "group"
            if isinstance(current_node, crony.model.JobGroup)
            else "job"
            if current_node is not None
            else None
        )
        if pkind is not None or ckind is not None:
            kind = _mark(pkind, ckind)
        elif isinstance(entry, crony.config.TomlJobGroup):
            kind = "group"
        elif entry is not None:
            kind = "job"
        else:
            kind = config.current.kind_of(ref) or "" if ref is not None else ""

        # CONFIG / UNIT / LAST are single-source verdicts (not flag-
        # selected); resolve them against the config name so the
        # TOML-entry-based grouped check and errored detection land.
        cfg_state, unit_state, last = _resolve_state_axes(
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
        # `unit-config` / `unit-timer`: the platform unit paths captured
        # at load time (uuid-keyed RuntimeState). Empty for entries with
        # no runtime, and unit-timer is empty where the platform has no
        # separate timer unit / for an unscheduled entry.
        rt = config.runtime.get(row_ref) if row_ref is not None else None
        unit_config_cell = str(rt.unit_config) if rt and rt.unit_config else ""
        unit_timer_cell = str(rt.unit_timer) if rt and rt.unit_timer else ""
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
        # `diverged` summarizes why an entry reads stale -- the snapshot
        # fields that differ, plus `unit` for an installed-unit drift.
        diverged_cell = _diverged_fields(
            pending_node,
            current_node,
            unit_stale=bool(rt is not None and rt.unit_is_stale),
        )
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
                token = member.token
                if member_diverged:
                    token = f"{token}{_DIVERGENCE_MARKER}"
                flags_summary_parts.append(token)
        row_cells: dict[str, str] = {
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
            "log-file": log_file_cell,
            "flags": ",".join(flags_summary_parts),
            "timeout": timeout_cell,
            "priority": priority_cell,
            "diverged": diverged_cell,
        }
        row_cells.update(flag_cells)
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
        # AND `unit` not in {none, disabled} AND `last` in
        # {ok, never, gated}. A disabled unit is unhealthy
        # because it isn't firing.
        healthy_last = {
            crony.model.JobStatus.OK,
            crony.model.JobStatus.NEVER,
            crony.model.JobStatus.GATED,
        }
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

    # Deferred until the displayed rows exist: the `all` alias's
    # masked-by trim keys on whether any shown row carries a reason,
    # which only the built rows can answer.
    masked_present = any(row["masked-by"] for row in rows)
    selected_cols = _parse_status_cols(
        cols, masked_present=masked_present, platform=platform
    )

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
    use_color = _color_supported()
    for row in rows:
        line = sep.join(
            _render_status_cell(c, row[c], widths[c], use_color)
            for c in selected_cols
        )
        print(line.rstrip())
    # Print the divergence footer only when a displayed cell actually
    # carries the `^` marker -- a column set that shows no flagged cell
    # has nothing for the legend to explain. This keys on what's on
    # screen, so any marker-carrying column counts without a separate
    # list to keep in sync. The marker can sit mid-cell -- the `flags`
    # summary tags individual flags (e.g. `interactive^,keep-awake`) --
    # so test for it anywhere in the cell, not only at the end.
    if any(_DIVERGENCE_MARKER in row[c] for row in rows for c in selected_cols):
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
    full = crony.config.normalize_full_name(name)
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
    if tail and latest:
        raise crony.errors.UsageError(
            "--tail and --latest are mutually exclusive"
        )
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


def _parse_since(spec: str) -> datetime.datetime:
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
        raise crony.errors.UsageError(
            f"unparseable --since value: {spec!r} "
            f"(use NUMs/m/h/d or ISO timestamp)"
        ) from e
    if ts.tzinfo is None:
        raise crony.errors.UsageError(
            f"--since {spec!r} is missing a timezone offset; "
            f"use a form like 2026-04-01T12:00:00-07:00"
        )
    return ts


def _filter_since(text: str, since: str) -> str:
    """Drop log content older than --since by run-header timestamp."""
    cutoff = _parse_since(since)
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
    for t in list(config.platform_targets.values()) + list(
        config.host_targets.values()
    ):
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
    uses the legacy underscore key spelling additionally prints one
    deprecation warning and exits WARNING.
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
    )
    if errored:
        raise crony.errors.ConfigError(
            f"{path}: {errored} invalid config "
            f"{'entry' if errored == 1 else 'entries'}"
        )
    print(f"ok: {path} validates as bundle {name!r}")
    if cfg.legacy_underscore_keys:
        print("warnings:")
        print(f"  - {path}: {_legacy_keys_warning(cfg.legacy_underscore_keys)}")
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


def do_validate(bundle: str | None, file: str | None) -> None:
    """Lint configs; report linger status and broken secret files.

    TomlConfig.load_all already enforces per-bundle structural rules
    and isolates failed bundles. This subcommand surfaces linger /
    per-bundle warnings as informational output and exits WARNING
    (1) when any are present, CONFIG (3) when no bundles load. A bundle
    still using the legacy underscore key spelling draws one
    deprecation warning naming its underscore keys (the dash spelling is
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
        if config.legacy_underscore_keys:
            warnings.append(
                f"{b.source}: "
                f"{_legacy_keys_warning(config.legacy_underscore_keys)}"
            )

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
        gate="none",
        log_path="(synthetic)",
        log_bytes_this_run=0,
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

    # Compose --bundle and --channel's bundle prefix.
    if channel_bundle is not None and bundle is not None:
        if channel_bundle != bundle:
            raise crony.errors.UsageError(
                f"--bundle {bundle!r} contradicts --channel "
                f"{channel!r} (bundle {channel_bundle!r}); pick one"
            )
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
