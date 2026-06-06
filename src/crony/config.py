# This is AI generated code

"""crony's TOML configuration layer.

The input model (the Toml* / Notify* / Defaults / Target / HostList
dataclasses), the parsers that build it from raw TOML, cross-cutting
validation, multi-bundle loading, target selection, and the resolved_*
cascade that derives effective per-job settings. ConfigError on any
malformed config; per-entity failures demote into the bundle's errored_*
maps rather than aborting the whole bundle.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit
import tomlkit.exceptions

import crony.paths
import crony.platform
from crony.errors import ConfigError, UsageError
from crony.unit import (
    EntityRef,
    Interval,
    PriorityClass,
    Schedule,
    Timing,
)

logger = logging.getLogger(__name__)


DEFAULT_BUNDLE_NAME: str = "default"
# Sentinel value for a `notify_channels` list: in a non-default
# bundle it pulls in the default bundle's channels, definitions, and
# attach settings. It may stand alone (notify exactly as the default
# bundle would) or sit alongside explicit siblings (notify as the
# default bundle would PLUS those channels, de-duped). It is also the
# implicit default for non-default bundles that omit notify config.
# The token is the default bundle's own name, so it doubles as a
# reserved channel name (a [defaults.notify.default] block is
# rejected).
NOTIFY_INHERIT_TOKEN: str = DEFAULT_BUNDLE_NAME


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class NotifyEmail:
    """SMTP transport settings."""

    to: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    from_addr: str | None = None
    smtp_starttls: bool = True
    smtp_pass_keychain_service: str | None = None
    smtp_pass_keychain_account: str | None = None
    smtp_pass_file: str | None = None


@dataclass
class NotifyNtfy:
    """ntfy transport settings."""

    url: str
    token_keychain_service: str | None = None
    token_keychain_account: str | None = None
    token_file: str | None = None


@dataclass
class NotifyChannel:
    """A user-named notification channel.

    `name` is the identifier the user lists in `notify_channels`.
    `transport` selects which sender to use ("email", "ntfy", or
    "dialog-popup"). For "email" / "ntfy" the matching `email` / `ntfy`
    config is populated; the zero-config "dialog-popup" transport
    leaves both None. `headers` is an optional dict of user-supplied
    headers merged into the message at send time (email / ntfy only)
    -- see `_send_*_for` for which keys are crony-controlled and may
    not be overridden.
    """

    name: str
    transport: str
    headers: dict[str, str] = field(default_factory=dict)
    email: NotifyEmail | None = None
    ntfy: NotifyNtfy | None = None

    @classmethod
    def from_raw(cls, name: str, raw: dict[str, Any]) -> NotifyChannel:
        """Parse a single [defaults.notify.<name>] block.

        Channel-level keys: `transport` (defaults to the channel name
        when name matches a built-in transport; required otherwise) and
        `headers` (optional dict of user-supplied headers).
        Transport-specific keys live alongside. The zero-config
        `dialog-popup` transport takes only `transport` -- no headers,
        no endpoint -- and is usually listed by name without a block at
        all.
        """
        where = f"[defaults.notify.{name}]"
        _validate_name(name, where)

        transport = _typed_field(raw, "transport", str, where)
        if transport is None:
            if name in VALID_NOTIFY_TRANSPORTS:
                transport = name
            else:
                raise ConfigError(
                    f"{where}: 'transport' required for channel "
                    f"{name!r} (only "
                    f"{sorted(VALID_NOTIFY_TRANSPORTS)} can omit it)"
                )
        if transport not in VALID_NOTIFY_TRANSPORTS:
            raise ConfigError(
                f"{where}: transport {transport!r} not in "
                f"{sorted(VALID_NOTIFY_TRANSPORTS)}"
            )

        if transport == "dialog-popup":
            # Zero-config built-in: only `transport` is permitted -- no
            # headers, no endpoint, no secrets. Listing the channel
            # name in notify_channels is the whole configuration.
            _reject_unknown_keys(raw, frozenset({"transport"}), where)
            return cls(name=name, transport=transport)

        headers = _string_dict(raw, "headers", where)
        reserved = (
            _RESERVED_HEADERS_EMAIL
            if transport == "email"
            else _RESERVED_HEADERS_NTFY
        )
        for k in headers:
            if k.lower() in reserved:
                raise ConfigError(
                    f"{where}: header {k!r} is set by crony and cannot "
                    f"be overridden"
                )

        # Reject keys outside (channel-level + this transport's keys).
        transport_keys = (
            _KNOWN_TRANSPORT_EMAIL
            if transport == "email"
            else _KNOWN_TRANSPORT_NTFY
        )
        _reject_unknown_keys(raw, _KNOWN_NOTIFY_CHANNEL | transport_keys, where)

        email_cfg: NotifyEmail | None = None
        ntfy_cfg: NotifyNtfy | None = None
        if transport == "email":
            email_cfg = _parse_notify_email_settings(raw, where)
        else:
            ntfy_cfg = _parse_notify_ntfy_settings(raw, where)
        return cls(
            name=name,
            transport=transport,
            headers=headers,
            email=email_cfg,
            ntfy=ntfy_cfg,
        )


@dataclass
class Defaults:
    """Tool-wide default settings cascaded to jobs."""

    # Empty list = no external dispatch (log + last-run.json are
    # always written regardless). On non-empty, every listed channel
    # fires per failure; per-channel results are recorded
    # independently in last-run.json's `notifications` dict. Each
    # entry must name a key in `notify_channel_defs` below, or a
    # zero-config built-in channel (`BUILTIN_NOTIFY_CHANNELS`, e.g.
    # "dialog-popup") that needs no definition. The [NOTIFY_INHERIT_TOKEN]
    # sentinel inherits the default bundle's notify config -- alone, or
    # unioned with explicit siblings listed alongside it (and is the
    # implicit default for a non-default bundle that omits notify
    # config). The default bundle itself defaults to [] and may not use
    # the sentinel.
    notify_channels: list[str] = field(default_factory=list)
    notify_attach_log: bool = True
    # Cap on log content included in EMAIL notifications. ntfy
    # bodies use a fixed 3 KB inline cap (ntfy's per-message limit
    # is 4 KB) and ignore this setting -- adjusting it here will
    # not affect ntfy.
    notify_attach_max_kb: int = 256
    # Default per-job wallclock cap. 0 = no cap (for jobs that manage
    # their own timeout); see TomlJob.job_timeout_sec.
    job_timeout_sec: int = 1800
    # How long `crony trigger --wait` waits for a runner to come
    # online after asking the platform scheduler to fire the unit.
    # Catches "trigger seemed to succeed but nothing happened"
    # cases (broken plist, queue stalled, etc.).
    trigger_timeout_sec: int = 15
    log_keep_runs: int = 30
    # Cascaded to jobs that don't set their own; see TomlJob.priority /
    # TomlJob.keep_awake. None / False keep today's per-job behavior
    # when a bundle sets no default.
    priority: PriorityClass | None = None
    keep_awake: bool = False
    # Base env merged under every job's own `env` (job keys win); see
    # resolved_env. Values are expanded at fire time like a job's env.
    env: dict[str, str] = field(default_factory=dict)
    # Per-bundle channel definitions: { name -> NotifyChannel }.
    notify_channel_defs: dict[str, NotifyChannel] = field(default_factory=dict)


@dataclass
class HostList:
    """A `hosts = [...]` filter.

    Empty `names` = applies everywhere (the common case). Non-empty
    with `negated=False` is an allowlist (apply only on listed
    hosts). Non-empty with `negated=True` is a denylist (apply on
    every host except listed). The parser enforces that entries
    are all-positive or all-negated (`!host`) within a single
    list -- mixing is rejected.
    """

    names: list[str] = field(default_factory=list)
    negated: bool = False


# Resolution-time defaults for interactive jobs whose user did not
# override `interactive_active` / `interactive_delay`. 10 min of
# continuous active input is conservative enough that a passing
# wiggle of the mouse doesn't trigger the dialog; a 1h delay after
# "Delay Job" gives breathing room before crony asks again.
INTERACTIVE_ACTIVE_DEFAULT_SEC: int = 600
INTERACTIVE_DELAY_DEFAULT_SEC: int = 3600


@dataclass
class TomlJob:
    """A single schedulable unit of work."""

    name: str
    # Stable per-bundle identity, decoupled from `name`. Every live
    # TomlJob in `TomlBundleConfig.jobs` carries one; `_parse_job`
    # rejects bodies without `uuid` into `errored_jobs`. `crony config
    # update` populates the field in place via tomlkit round-trip for
    # configs that don't yet have one.
    uuid: str
    command: str | None = None
    script: str | None = None
    args: list[str] = field(default_factory=list)
    gate: str | None = None
    gate_script: str | None = None
    gate_args: list[str] = field(default_factory=list)
    timing: Timing | None = None
    # Process-priority class baked into the platform unit: HIGH (run
    # un-throttled, app-like QoS), LOW (throttle CPU + IO), or NORMAL /
    # None (emit nothing). None inherits the bundle [defaults].
    priority: PriorityClass | None = None
    # Hold a power assertion for the command's duration so an idle /
    # on-AC machine doesn't sleep mid-run. Lid-close on battery still
    # sleeps. None = inherit [defaults]; True / False explicitly
    # override.
    keep_awake: bool | None = None
    # `platforms`: empty list = applies to every platform. Non-empty
    # = applies only on listed platforms; otherwise the entry is
    # silently skipped at selection time. `hosts` works the same
    # way but additionally supports negation -- see HostList.
    platforms: list[str] = field(default_factory=list)
    hosts: HostList = field(default_factory=HostList)
    job_timeout_sec: int | None = None
    # None = inherit from target/defaults; 0 = no wallclock cap (the
    # job caps itself). An uncapped job propagates up: any group that
    # contains it is uncapped too.
    notify_channels: list[str] | None = None
    # None = inherit from target/defaults; [] = explicit empty (this
    # job sends no external notifications even if defaults / target
    # would).
    # Non-zero exit codes to classify as success (exit 0 is always
    # success). A run whose code lands here is "ok" -- not failed, no
    # notification -- and `crony run` surfaces 0 to the scheduler. For
    # commands that exit non-zero on transient / non-fatal conditions
    # (e.g. borg's exit 1 on backup warnings).
    success_exit_codes: list[int] = field(default_factory=list)
    # Merged over [defaults.env] (a key here wins) by resolved_env.
    env: dict[str, str] = field(default_factory=dict)
    # Interactive jobs sit pending in the background after their
    # scheduled fire and prompt the user before running. The two
    # `_sec` knobs are None when the user didn't set them; the
    # snapshot resolver substitutes baked defaults. Validation
    # auto-tags `platforms = ["darwin"]` (the dialog and idle-
    # detection helpers are macOS-only) and rejects placement
    # inside any [job-group.*].
    interactive: bool = False
    interactive_active_sec: int | None = None
    interactive_delay_sec: int | None = None


@dataclass
class TomlJobGroup:
    """A scheduled sequencer that fires named jobs in order.

    Groups don't carry notify settings: children run independently
    (via the platform scheduler dispatch that crony orchestrates)
    and resolve their own notify_channels through the target / job
    / defaults cascade. To apply a notify channel to all members of
    a group, set it at the target level (typical case) or per-job.

    Groups also don't carry `job_timeout_sec` -- timeouts are a
    per-leaf-job concern. A group's effective deadline is
    auto-computed from its children (`resolved_group_timeout_sec`,
    1.05 * sum of children's effective timeouts) and is not a
    user-facing knob: it acts as defense-in-depth so each child
    can hit its own per-job timeout before the parent's
    cumulative deadline fires. A child that is itself uncapped
    (`job_timeout_sec = 0`) makes the group uncapped too -- there is
    no finite cumulative deadline that could bound it.

    `platforms` / `hosts` work the same way as on TomlJob: empty
    means "applies everywhere", non-empty restricts selection.
    `hosts` supports negation via `!host` entries -- see HostList.
    A group filtered out doesn't recurse into its children; a
    child filtered out is skipped while siblings continue.
    """

    name: str
    uuid: str
    jobs: list[str] = field(default_factory=list)
    timing: Timing | None = None
    platforms: list[str] = field(default_factory=list)
    hosts: HostList = field(default_factory=HostList)


@dataclass
class Target:
    """Per-platform or per-host selection of jobs/groups + cascading settings.

    `kind` is "platform" (e.g. darwin/linux) or "host" (a specific host).

    A target deliberately carries no timeout knob: timeouts are a
    per-leaf-job concern (`TomlJob.job_timeout_sec` cascading to
    `Defaults.job_timeout_sec`) because a target / group is a
    selection-or-sequencing concept, not an executor. Effective
    deadlines for groups are auto-computed from their children
    (`resolved_group_timeout_sec`) as defense-in-depth, not as a
    user-tunable surface.
    """

    name: str
    kind: str
    jobs: list[str] = field(default_factory=list)
    # None = inherit from job/defaults; [] = explicit empty.
    notify_channels: list[str] | None = None


@dataclass
class TomlBundleConfig:
    """Top-level parsed config (one bundle's content).

    Per-entity ConfigErrors from `[job.*]`, `[job-group.*]`, and
    `[target.*]` sections are caught and recorded in
    `errored_jobs` / `errored_job_groups` /
    `errored_platform_targets` / `errored_host_targets` rather
    than aborting the whole bundle. Each errored short-name maps
    to its error message. The corresponding entries do NOT appear
    in the live `jobs` / `job_groups` / `platform_targets` /
    `host_targets` maps -- consumers that need to act on a parsed
    entity must check the errored map first.

    Surface points for errored entries:
      - Always: logged at ERROR by `TomlBundle.load`, and
        included as warnings by `crony config validate` (which then
        exits non-zero).
      - Jobs and groups also: shown as a `config=error` row in
        `crony status` (the row namespace is keyed by
        `<bundle>.<short>`, which targets don't inhabit -- they're
        keyed by platform / hostname instead).
      - Lifecycle commands treat errored jobs / groups as
        defined-but-inert; an errored target is simply absent from
        target resolution (matching the existing "no target for
        this host" semantics).
    """

    defaults: Defaults = field(default_factory=Defaults)
    jobs: dict[str, TomlJob] = field(default_factory=dict)
    job_groups: dict[str, TomlJobGroup] = field(default_factory=dict)
    errored_jobs: dict[str, str] = field(default_factory=dict)
    errored_job_groups: dict[str, str] = field(default_factory=dict)
    platform_targets: dict[str, Target] = field(default_factory=dict)
    host_targets: dict[str, Target] = field(default_factory=dict)
    errored_platform_targets: dict[str, str] = field(default_factory=dict)
    errored_host_targets: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_raw(
        cls,
        raw: dict[str, Any],
        *,
        bundle_name: str = DEFAULT_BUNDLE_NAME,
    ) -> TomlBundleConfig:
        """Parse a top-level config dict into a validated TomlBundleConfig.

        `bundle_name` identifies the source bundle; it gates the
        notify-inherit sentinel (only non-default bundles may inherit,
        and they do so implicitly when notify config is omitted).
        Callers with a single-bundle view default it to the default
        bundle, the conservative choice (no implicit inherit, sentinel
        rejected).
        """
        _reject_unknown_keys(raw, _KNOWN_TOPLEVEL, "(top level)")
        is_default = bundle_name == DEFAULT_BUNDLE_NAME

        config = cls()

        if "defaults" in raw:
            if not isinstance(raw["defaults"], dict):
                raise ConfigError("[defaults] must be a table")
            config.defaults = _parse_defaults(
                raw["defaults"], is_default=is_default
            )
        elif not is_default:
            # No [defaults] block at all still inherits the default
            # bundle's notify config for a non-default bundle.
            config.defaults = Defaults(notify_channels=[NOTIFY_INHERIT_TOKEN])

        job_section = raw.get("job", {})
        if not isinstance(job_section, dict):
            raise ConfigError("[job] must be a table")
        for name, body in job_section.items():
            # Per-entity ConfigError tolerance: a parse failure on
            # one job records its error and continues so sibling jobs
            # still parse. Catching ConfigError (not Exception) keeps
            # genuine bugs surfacing as tracebacks.
            try:
                if not isinstance(body, dict):
                    raise ConfigError(f"[job.{name}] must be a table")
                config.jobs[name] = _parse_job(name, body)
            except ConfigError as exc:
                config.errored_jobs[name] = str(exc)

        group_section = raw.get("job-group", {})
        if not isinstance(group_section, dict):
            raise ConfigError("[job-group] must be a table")
        for name, body in group_section.items():
            try:
                if not isinstance(body, dict):
                    raise ConfigError(f"[job-group.{name}] must be a table")
                config.job_groups[name] = _parse_job_group(name, body)
            except ConfigError as exc:
                config.errored_job_groups[name] = str(exc)

        target_section = raw.get("target", {})
        if not isinstance(target_section, dict):
            raise ConfigError("[target] must be a table")
        for name, body in target_section.items():
            if name == "host":
                # [target.host.<host>] entries
                if not isinstance(body, dict):
                    raise ConfigError("[target.host] must be a table")
                for hostname, hostbody in body.items():
                    if not isinstance(hostbody, dict):
                        raise ConfigError(
                            f"[target.host.{hostname}] must be a table"
                        )
                    config.host_targets[hostname] = _parse_target(
                        hostname, "host", hostbody
                    )
            else:
                # [target.<platform>] entry
                if not isinstance(body, dict):
                    raise ConfigError(f"[target.{name}] must be a table")
                config.platform_targets[name] = _parse_target(
                    name, "platform", body
                )

        _validate_config(config, is_default=is_default)
        return config

    @classmethod
    def load(cls, path: Path) -> TomlBundleConfig:
        """Load a single config file as a `TomlBundleConfig`.

        Suited to tests and any caller that has one specific config
        path in hand and wants the parsed `TomlBundleConfig` for that
        file alone. Production code paths walk every bundle and should
        use `TomlConfig.load_all()` instead.
        """
        if not path.exists():
            raise ConfigError(f"config not found: {path}")
        try:
            raw = tomlkit.loads(path.read_text(encoding="utf-8"))
        except tomlkit.exceptions.ParseError as e:
            raise ConfigError(f"TOML parse error in {path}: {e}") from e
        config = cls.from_raw(raw)
        _demote_duplicate_uuids(config, DEFAULT_BUNDLE_NAME)
        return config

    def resolve_target(
        self, host: str | None = None, platform: str | None = None
    ) -> Target | None:
        """Pick the effective target for (host, platform).

        Host and platform default to the current machine's values
        when omitted. Host target wins; otherwise the platform
        target; otherwise None (nothing selected on this host).
        """
        if host is None:
            host = crony.platform.current_host()
        if platform is None:
            platform = crony.platform.current_platform()
        if host in self.host_targets:
            return self.host_targets[host]
        if platform in self.platform_targets:
            return self.platform_targets[platform]
        return None

    def selected_jobs_and_groups(
        self, target: Target | None
    ) -> tuple[set[str], set[str]]:
        """Compute the set of job and group names selected by target.

        A target's `jobs` list names roots; each root's transitive
        descendants (group children, which may themselves be groups)
        are also selected so they get stamped on this host and don't
        appear as orphans. Per-entry `platforms` / `hosts` filters
        exclude entries from the selected sets; a child whose only
        reachable parent is filtered out gets excluded along with it.
        Validation enforces single-parent within a target's subtree,
        so there is at most one path to any name on this host.

        Cycle protection is defensive -- `_validate_config` should
        have already rejected cycles.
        """
        jobs, groups, _ = self.selected_and_masked_jobs_and_groups(target)
        return jobs, groups

    def selected_and_masked_jobs_and_groups(
        self, target: Target | None
    ) -> tuple[set[str], set[str], dict[str, str]]:
        """Walk the target's selection, distinguishing selected from
        masked.

        Mirrors `selected_jobs_and_groups` for the selected sets; in
        addition, returns a `masked` mapping of name -> reason (axis
        string from `_mask_reason`) for entries reached through the
        target whose own filters exclude them, plus children of a
        masked group (which inherit the parent's reason -- they're
        inactive on this host even if their own filters would pass).
        A group whose every direct child is itself masked on this
        host has nothing to dispatch and joins the masked set with
        reason `"empty"`; the cascade iterates to a fixed point so a
        parent whose only effective child was a now-empty group is
        demoted as well. The selected sets are identical to what
        `selected_jobs_and_groups` returns; `masked` is the strictly-
        additional set that `--all` exposes.
        """
        jobs: set[str] = set()
        groups: set[str] = set()
        masked: dict[str, str] = {}
        if target is None:
            return jobs, groups, masked
        host = crony.platform.current_host()
        platform = crony.platform.current_platform()

        def _walk(name: str, parent_mask: str | None, seen: set[str]) -> None:
            if name in seen:
                return
            seen = seen | {name}
            if name in self.jobs:
                j = self.jobs[name]
                own_reason = _mask_reason(
                    j.platforms, j.hosts, host=host, platform=platform
                )
                effective = own_reason or parent_mask
                if effective is None:
                    jobs.add(name)
                else:
                    masked[name] = effective
            elif name in self.job_groups:
                g = self.job_groups[name]
                own_reason = _mask_reason(
                    g.platforms, g.hosts, host=host, platform=platform
                )
                effective = own_reason or parent_mask
                if effective is None:
                    groups.add(name)
                else:
                    masked[name] = effective
                for child in g.jobs:
                    _walk(child, effective, seen)

        for name in target.jobs:
            _walk(name, None, set())
        # Empty-group cascade: a selected group with no unmasked
        # direct child has nothing to dispatch on this host, so the
        # reference is treated as a no-op and the group joins the
        # masked set with reason "empty". Iterate to a fixed point so
        # a parent whose only remaining child was itself a now-empty
        # group cascades too. Cycles are rejected by
        # `_validate_config`, so the loop terminates in
        # O(group_count) iterations.
        while True:
            empties: list[str] = []
            for gname in groups:
                g = self.job_groups[gname]
                if not g.jobs or not any(
                    (c in jobs) or (c in groups) for c in g.jobs
                ):
                    empties.append(gname)
            if not empties:
                break
            for gname in empties:
                groups.discard(gname)
                masked[gname] = "empty"
        return jobs, groups, masked

    def resolved_notify_channels(
        self, target: Target | None, job: TomlJob
    ) -> list[str]:
        """Cascade notify_channels: target > job > defaults.

        Each layer's value is either None ("inherit from below") or a
        list ("override; this is the value for this layer"). An empty
        list at any layer is a deliberate "no external dispatch" choice
        that wins over lower layers. The bottom-most fallback is the
        defaults' list, which may itself be empty.
        """
        if target is not None and target.notify_channels is not None:
            return list(target.notify_channels)
        if job.notify_channels is not None:
            return list(job.notify_channels)
        return list(self.defaults.notify_channels)

    def resolved_job_timeout_sec(self, job: TomlJob) -> int:
        """Cascade job_timeout_sec: job > defaults. 0 means no cap.

        Targets and groups intentionally have no user-tunable timeout
        knob; see the `Target` and `TomlJobGroup` docstrings for the
        rationale.
        """
        if job.job_timeout_sec is not None:
            return job.job_timeout_sec
        return self.defaults.job_timeout_sec

    def resolved_priority(self, job: TomlJob) -> PriorityClass | None:
        """Cascade priority: job > defaults. Targets carry no
        priority."""
        if job.priority is not None:
            return job.priority
        return self.defaults.priority

    def resolved_keep_awake(self, job: TomlJob) -> bool:
        """Cascade keep_awake: job > defaults. Targets carry no
        keep_awake."""
        if job.keep_awake is not None:
            return job.keep_awake
        return self.defaults.keep_awake

    def resolved_env(self, job: TomlJob) -> dict[str, str]:
        """Merge env: defaults under job (a job's own key wins).
        Targets carry no env. Values stay literal here -- `$VAR`
        expansion happens at fire time in `runtime_env`."""
        return {**self.defaults.env, **job.env}

    def resolved_group_timeout_sec(
        self, target: Target | None, group_name: str
    ) -> int:
        """Auto-computed effective timeout for a group.

        Returns 1.05 * sum of children's effective timeouts (jobs use
        `resolved_job_timeout_sec`; sub-groups recurse into this same
        helper). Floor at 1 second. Returns 0 ("no cap") if any
        selected, non-interactive child is itself uncapped
        (`job_timeout_sec = 0`, or a sub-group that resolved to 0) --
        an uncapped child can't be bounded by a finite cumulative
        deadline, so the whole group goes uncapped. Used by:
        - `run_group` to bound its cumulative dispatch loop.
        - The waiter in `trigger_unit_sync` to bound its pid-watch
          when waiting for a group.
        Cycle-safety: `_validate_config` rejects cycles in group
        references, so the recursion always terminates.

        Children not selected on this host contribute zero (own
        filter excludes them or empty-group cascade demoted them) --
        the parent won't trigger them here, so the budget shouldn't
        reserve their time either. Interactive children also
        contribute zero: the group fires them async (no wait), so
        their `job_timeout_sec` doesn't bound any actual wait inside
        `run_group` and would just inflate the budget.
        """
        group = self.job_groups[group_name]
        sel_jobs, sel_groups = self.selected_jobs_and_groups(target)
        total = 0.0
        for child in group.jobs:
            if child not in sel_jobs and child not in sel_groups:
                continue
            if child in self.jobs:
                child_job = self.jobs[child]
                if child_job.interactive:
                    continue
                child_timeout = self.resolved_job_timeout_sec(child_job)
            else:
                child_timeout = self.resolved_group_timeout_sec(target, child)
            if child_timeout == 0:
                return 0
            total += child_timeout
        return max(1, int(total * _GROUP_TIMEOUT_PADDING))


@dataclass
class TomlBundle:
    """One config file's contribution: a bundle name + parsed TomlBundleConfig.

    The bundle name namespaces the bundle's job and group names: a
    short name `daily-update` defined in bundle `borgadm` has the
    full name `borgadm.daily-update` everywhere on disk
    (state dir, platform unit).
    """

    name: str
    source: Path
    config: TomlBundleConfig

    def full_name(self, short: str) -> str:
        return f"{self.name}.{short}"

    @classmethod
    def load(cls, name: str, path: Path) -> TomlBundle:
        """Parse a single bundle file and validate it. Raises
        ConfigError on parse / schema failure, naming the source path
        for context.

        Per-entity ConfigErrors (a single bad job, group, or target
        inside an otherwise-valid bundle) do not raise:
        `TomlBundleConfig.from_raw` and `_validate_config` record them
        on `TomlBundleConfig.errored_jobs` / `errored_job_groups` /
        `errored_platform_targets` / `errored_host_targets`, and they
        surface at status time with `config=error` plus a logged line
        here so the user sees the problem regardless of subcommand.
        """
        try:
            raw = tomlkit.loads(path.read_text(encoding="utf-8"))
        except tomlkit.exceptions.ParseError as e:
            raise ConfigError(f"TOML parse error in {path}: {e}") from e
        try:
            config = TomlBundleConfig.from_raw(raw, bundle_name=name)
        except ConfigError as e:
            raise ConfigError(f"{path}: {e}") from e
        _demote_duplicate_uuids(config, name)
        # Per-entity error messages are already prefixed with
        # `[job.X]` / `[job-group.X]` / `[target.X]` /
        # `[target.host.X]`; we only prepend the bundle path so the
        # user sees which file produced each error.
        for msg in sorted(config.errored_jobs.values()):
            logger.error("%s: %s", path, msg)
        for msg in sorted(config.errored_job_groups.values()):
            logger.error("%s: %s", path, msg)
        for msg in sorted(config.errored_platform_targets.values()):
            logger.error("%s: %s", path, msg)
        for msg in sorted(config.errored_host_targets.values()):
            logger.error("%s: %s", path, msg)
        return cls(name=name, source=path, config=config)


@dataclass
class TomlConfig:
    """All loaded bundles, keyed by bundle name. Order is the load
    order (config.toml first, then config/*.toml lex-sorted).

    `errored_bundles` records per-file parse / validation
    failures so a config that's broken in one bundle doesn't
    block read-side subcommands (`status`, `destroy`, `logs`)
    from operating on the rest. Surfaced in `status`'s header so
    the operator notices.
    """

    bundles: list[TomlBundle] = field(default_factory=list)
    errored_bundles: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load_all(cls) -> TomlConfig:
        """Load every bundle: `config.toml` (-> bundle 'default') plus
        `config/*.toml` (each -> bundle named after its filename stem).

        Per-bundle failures are isolated: a bundle that fails to parse,
        fails schema validation, or has an invalid filename is recorded
        in `TomlConfig.errored_bundles` (source path -> error
        message) with a CONFIG-level error logged for that source.
        Subsequent bundles continue loading.

        Returns an empty `TomlConfig` when no candidate files exist or
        every candidate file fails -- read-side subcommands
        (`status`, `destroy`, `logs`, `crony run`) operate on the on-
        disk state alone (`current`, `broken`, `unit_only`), so the
        runner keeps firing through a config-broken state and the
        operator can still inspect / clean up the on-disk picture.
        `apply` is the only path that errors hard against the
        affected bundle -- it needs pending-side data to do its job.
        """
        bundles = cls()
        seen_names: set[str] = set()

        candidates: list[tuple[str, Path]] = []
        if crony.paths.CONFIG_FILE.exists():
            candidates.append((DEFAULT_BUNDLE_NAME, crony.paths.CONFIG_FILE))
        if crony.paths.CONFIG_DROPIN_DIR.exists():
            for path in sorted(crony.paths.CONFIG_DROPIN_DIR.glob("*.toml")):
                candidates.append((path.stem, path))

        for bundle_name, path in candidates:
            try:
                validate_bundle_name(bundle_name, str(path))
            except ConfigError as e:
                bundles.errored_bundles[str(path)] = str(e)
                continue
            if bundle_name in seen_names:
                bundles.errored_bundles[str(path)] = (
                    f"bundle name {bundle_name!r} collides with "
                    f"already-loaded bundle; this file will not load"
                )
                continue
            try:
                bundle = TomlBundle.load(bundle_name, path)
            except ConfigError as e:
                bundles.errored_bundles[str(path)] = str(e)
                continue
            bundles.bundles.append(bundle)
            seen_names.add(bundle_name)

        for src, msg in bundles.errored_bundles.items():
            logger.error("%s: %s", src, msg)
        return bundles

    def require_known(self, bundle: str | None) -> None:
        """Reject `--bundle <name>` if `<name>` isn't a loaded bundle.

        Used by the subcommands that need the bundle's parsed config:
        `apply` and `validate` act on the pending entries, and
        `notify-test` reads the bundle's notify settings. A bundle
        whose file failed to parse has none of that, so scoping to it
        is an error. Subcommands that address installed units
        (`status` / `destroy` / `enable` / `disable` / `trigger`) use
        `Config.require_addressable` instead, which also accepts a
        bundle present only as on-disk state.
        """
        if bundle is not None and self.by_name(bundle) is None:
            raise UsageError(f"unknown bundle: {bundle!r}")

    def by_name(self, bundle_name: str) -> TomlBundle | None:
        for b in self.bundles:
            if b.name == bundle_name:
                return b
        return None

    def all_full_names(self) -> set[str]:
        """Return every defined `<bundle>.<short>` (jobs + groups).

        Errored entries count as defined for this view: even though
        the parser rejected their config, the bundle still claims
        the name. `crony destroy` uses this to allow cleanup of an
        errored entry's previously-applied installation without
        the user having to first fix the config.
        """
        out: set[str] = set()
        for b in self.bundles:
            for short in b.config.jobs:
                out.add(b.full_name(short))
            for short in b.config.job_groups:
                out.add(b.full_name(short))
            for short in b.config.errored_jobs:
                out.add(b.full_name(short))
            for short in b.config.errored_job_groups:
                out.add(b.full_name(short))
        return out


# =============================================================================
# SCHEDULE / INTERVAL PARSING
# =============================================================================
# Schedules use systemd OnCalendar syntax; intervals use systemd
# time-span syntax. Both are parsed and validated by the crony.unit
# value objects (Schedule / Interval); `_parse_timing` wraps them for
# the config loader.


def _parse_timing(
    schedule_str: str | None, interval_str: str | None, where: str
) -> Timing | None:
    """Build a unit's timing from the config's mutually-exclusive
    `schedule` / `interval` keys, or None for an on-demand entry.
    Surfaces the value objects' validation as a config error tied to
    `where`."""
    if schedule_str is not None and interval_str is not None:
        raise ConfigError(
            f"{where}: 'schedule' and 'interval' are mutually exclusive"
        )
    try:
        if schedule_str is not None:
            return Schedule.from_str(schedule_str)
        if interval_str is not None:
            return Interval.from_str(interval_str)
    except ValueError as e:
        raise ConfigError(f"{where}: {e}") from e
    return None


def _parse_priority(text: str | None, where: str) -> PriorityClass | None:
    """Build a PriorityClass from a config string, or None. Surfaces
    the value object's validation as a config error tied to `where`."""
    if text is None:
        return None
    try:
        return PriorityClass.from_str(text)
    except ValueError as e:
        raise ConfigError(f"{where}: {e}") from e


# =============================================================================
# CONFIG LOADING
# =============================================================================
# Reads TOML, validates structurally and cross-cuttingly, and returns a
# fully-typed TomlBundleConfig. Raises ConfigError on any problem.


_KNOWN_TOPLEVEL: frozenset[str] = frozenset(
    {
        "defaults",
        "job",
        "job-group",
        "target",
    }
)

_KNOWN_DEFAULTS: frozenset[str] = frozenset(
    {
        "notify_channels",
        "notify_attach_log",
        "notify_attach_max_kb",
        "job_timeout_sec",
        "trigger_timeout_sec",
        "log_keep_runs",
        "priority",
        "keep_awake",
        "env",
        "notify",
    }
)

_KNOWN_JOB: frozenset[str] = frozenset(
    {
        "uuid",
        "command",
        "script",
        "args",
        "gate",
        "gate_script",
        "gate_args",
        "schedule",
        "interval",
        "priority",
        "keep_awake",
        "platforms",
        "hosts",
        "job_timeout_sec",
        "notify_channels",
        "success_exit_codes",
        "env",
        "interactive",
        "interactive_active",
        "interactive_delay",
    }
)

_KNOWN_JOB_GROUP: frozenset[str] = frozenset(
    {
        "uuid",
        "jobs",
        "schedule",
        "interval",
        "platforms",
        "hosts",
    }
)

_KNOWN_TARGET: frozenset[str] = frozenset(
    {
        "jobs",
        "notify_channels",
    }
)

# Channel-level keys -- valid in any [defaults.notify.<name>] block
# regardless of transport.
_KNOWN_NOTIFY_CHANNEL: frozenset[str] = frozenset(
    {
        "transport",
        "headers",
    }
)

# Transport-specific keys. Valid alongside the channel-level keys
# inside a [defaults.notify.<name>] block whose transport matches.
_KNOWN_TRANSPORT_EMAIL: frozenset[str] = frozenset(
    {
        "to",
        "from",
        "smtp_host",
        "smtp_port",
        "smtp_user",
        "smtp_starttls",
        "smtp_pass_keychain_service",
        "smtp_pass_keychain_account",
        "smtp_pass_file",
    }
)

_KNOWN_TRANSPORT_NTFY: frozenset[str] = frozenset(
    {
        "url",
        "token_keychain_service",
        "token_keychain_account",
        "token_file",
    }
)

# Built-in transport names. A channel whose name matches one of
# these may omit `transport=` -- the shorthand
# `[defaults.notify.email]` is equivalent to
# `[defaults.notify.email] transport = "email"`. A channel named
# anything else must declare `transport=` explicitly so the
# parser knows which schema to validate the block against.
VALID_NOTIFY_TRANSPORTS: frozenset[str] = frozenset(
    {
        "email",
        "ntfy",
        "dialog-popup",
    }
)

# Zero-config built-in channels. These names may be listed in a
# `notify_channels` with no `[defaults.notify.<name>]` block at all:
# the transport carries no per-channel settings (no secrets, no
# endpoint), so dispatch synthesizes a default channel def on the fly
# (see `_builtin_notify_channel`). A built-in's channel name equals
# its transport name. An explicit block is still allowed but never
# required.
BUILTIN_NOTIFY_CHANNELS: frozenset[str] = frozenset({"dialog-popup"})

# Headers crony controls per transport. User-supplied `headers`
# entries that match (case-insensitively) are rejected at parse
# time so a config can never silently overwrite them.
_RESERVED_HEADERS_EMAIL: frozenset[str] = frozenset({"to", "from", "subject"})
# `filename` is reserved even though _post_ntfy never sets it: a
# user-supplied `Filename` would make ntfy render the body as a
# downloadable file (publicly addressable by URL guessing), which
# is exactly what the inline-body design avoids. Reserve it to
# keep that behavior consistent across configs.
_RESERVED_HEADERS_NTFY: frozenset[str] = frozenset(
    {"authorization", "tags", "title", "filename"}
)

_VALID_PLATFORMS: frozenset[str] = frozenset({"darwin", "linux"})


def _reject_unknown_keys(
    raw: dict[str, Any], known: frozenset[str], where: str
) -> None:
    """Raise ConfigError if raw has keys not in `known`."""
    unknown = set(raw.keys()) - known
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")


def _typed_field(
    raw: dict[str, Any],
    key: str,
    expected: type,
    where: str,
    *,
    default: Any = None,
) -> Any:
    """Extract `key` from `raw` with a type check. Returns `default` if absent.

    `bool` and `int` are distinct here, even though `isinstance(True, int)`
    is True in Python -- we explicitly reject booleans for int-typed fields
    so `job_timeout_sec = true` raises clearly rather than silently meaning 1.
    """
    if key not in raw:
        return default
    val = raw[key]
    # bool is a subclass of int; require strict separation here.
    if expected is int and isinstance(val, bool):
        raise ConfigError(f"{where}: '{key}' must be int, got bool")
    if expected is bool and not isinstance(val, bool):
        raise ConfigError(
            f"{where}: '{key}' must be bool, got {type(val).__name__}"
        )
    if not isinstance(val, expected):
        raise ConfigError(
            f"{where}: '{key}' must be {expected.__name__}, "
            f"got {type(val).__name__}"
        )
    return val


# Short job/group/host names appear inside a single bundle's TOML.
# They become part of filesystem paths (alongside the bundle name)
# and platform unit labels, so they must be safe filename characters.
# Dots are allowed: a job written as `[job."foo.bar"]` still maps
# cleanly into `<bundle>.foo.bar` on disk.
_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# TomlBundle names come from filenames (config.toml -> "default";
# config/<x>.toml -> "x"). The dot is reserved as the namespace
# separator between bundle and short name, so bundle names cannot
# themselves contain dots.
_BUNDLE_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _validate_name(name: str, where: str) -> None:
    """Reject names that would break filesystem paths or unit labels."""
    if not _NAME_RE.match(name):
        raise ConfigError(
            f"{where}: name {name!r} must match "
            f"[A-Za-z0-9][A-Za-z0-9._-]* (no slashes, spaces, "
            f"empty, or leading punctuation)"
        )


def _parse_uuid_field(raw: dict[str, Any], where: str) -> str | None:
    """Read the optional `uuid` field as a canonical UUID string.

    Returns None when the key is absent. Rejects non-string values
    and any form other than canonical lowercase 8-4-4-4-12 (the
    `uuid.uuid4()` default). The strict-format check catches
    copy-paste mistakes like missing dashes or uppercase early
    rather than letting them flow into the identity comparison.
    """
    value = _typed_field(raw, "uuid", str, where)
    if value is None:
        return None
    try:
        parsed = uuid.UUID(value)
    except ValueError as e:
        raise ConfigError(
            f"{where}: 'uuid' is not a valid UUID: {value!r} ({e})"
        ) from e
    canonical = str(parsed)
    if value != canonical:
        raise ConfigError(
            f"{where}: 'uuid' must be canonical lowercase "
            f"8-4-4-4-12 form (got {value!r}, expected "
            f"{canonical!r})"
        )
    return canonical


def validate_bundle_name(name: str, where: str) -> None:
    """Reject filenames whose stem can't be a bundle name."""
    if not _BUNDLE_NAME_RE.match(name):
        raise ConfigError(
            f"{where}: bundle name {name!r} must match "
            f"[A-Za-z0-9][A-Za-z0-9_-]* (no dots -- the dot is "
            f"reserved as the bundle/job-name separator)"
        )


def parse_full_name(arg: str) -> tuple[str, str]:
    """Parse a CLI job/group reference into (bundle_name, short).

    Bare 'foo' -> ('default', 'foo') -- bare input only ever
    selects the default bundle, never falls through to others.
    'borgadm.foo' -> ('borgadm', 'foo'). Multi-dot forms split on
    the first dot, so '<bundle>.<short>' where short itself
    contains dots stays intact.
    """
    if "." not in arg:
        return (DEFAULT_BUNDLE_NAME, arg)
    bundle, _, short = arg.partition(".")
    if not bundle or not short:
        raise UsageError(f"invalid job reference: {arg!r}")
    return (bundle, short)


def normalize_full_name(arg: str) -> str:
    """CLI input -> canonical form.

    For the dot-separated name form (`<bundle>.<short>` or bare
    `<short>`): bare 'foo' becomes 'default.foo', already-
    namespaced inputs round-trip. For the colon-separated ref
    form (`<bundle>:<UUID>`): pass through unchanged so the
    downstream lookup can recognize it. Used at the entry point
    of every CLI handler that takes user-supplied job references.
    """
    if EntityRef.from_str(arg) is not None:
        return arg
    bundle, short = parse_full_name(arg)
    return f"{bundle}.{short}"


def resolve_cli_name(arg: str, scope_bundle: str | None) -> str:
    """CLI input -> canonical form honoring `-b`.

    With `scope_bundle` None this is `normalize_full_name`. With
    `scope_bundle` set, bare `arg` resolves in that bundle (so
    `-b foo bar` -> `foo.bar`), already-qualified `<scope>.<short>`
    round-trips, and `<other>.<short>` is rejected -- under `-b`
    every name on the command line must belong to that bundle so
    a bulk operation can't sneak in a cross-bundle reference.
    Ref-form inputs (`<bundle>:<UUID>`) must also match the
    scope under `-b`.
    """
    if scope_bundle is None:
        return normalize_full_name(arg)
    ref = EntityRef.from_str(arg)
    if ref is not None:
        if ref.bundle != scope_bundle:
            raise UsageError(
                f"{arg!r} is in bundle {ref.bundle!r} but --bundle "
                f"{scope_bundle!r} is set"
            )
        return arg
    bundle, short = parse_full_name(arg)
    if "." not in arg:
        return f"{scope_bundle}.{short}"
    if bundle != scope_bundle:
        raise UsageError(
            f"{arg!r} is in bundle {bundle!r} but --bundle "
            f"{scope_bundle!r} is set"
        )
    return f"{bundle}.{short}"


def bundle_prefix_filter(names: Iterable[str], bundle: str) -> set[str]:
    """Subset of `names` whose `<bundle>.` prefix matches."""
    prefix = f"{bundle}."
    return {n for n in names if n.startswith(prefix)}


def _string_list(raw: dict[str, Any], key: str, where: str) -> list[str]:
    """Extract a list-of-strings field, defaulting to []."""
    val = raw.get(key)
    if val is None:
        return []
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        raise ConfigError(f"{where}: '{key}' must be a list of strings")
    return list(val)


def _parse_platforms_field(raw: dict[str, Any], where: str) -> list[str]:
    """Parse a `platforms = [...]` field with allowed-value check.

    Both TomlJob and TomlJobGroup carry `platforms`; the allowed values
    (`darwin`, `linux`) are the same. Hosts have no fixed allow-
    list -- they're whatever the user names their machines.
    """
    platforms = _string_list(raw, "platforms", where)
    for p in platforms:
        if p not in _VALID_PLATFORMS:
            raise ConfigError(
                f"{where}: platforms entry {p!r} not in "
                f"{sorted(_VALID_PLATFORMS)}"
            )
    return platforms


def _parse_hosts_field(raw: dict[str, Any], where: str) -> HostList:
    """Parse a `hosts = [...]` field with optional `!` negation.

    Entries prefixed with `!` make the whole list a denylist
    (apply on every host except listed); unprefixed entries make
    it an allowlist. Mixing the two forms within one list is
    rejected -- the intent is ambiguous (does `!b` subtract from
    the allowlist, or deny `b` while allowing everything else?).
    An entry that is just `!` is rejected as empty.
    """
    entries = _string_list(raw, "hosts", where)
    if not entries:
        return HostList()
    negated = [e.startswith("!") for e in entries]
    if any(negated) and not all(negated):
        raise ConfigError(
            f"{where}: 'hosts' entries must all be negated ('!host') or none"
        )
    if not any(negated):
        return HostList(names=entries, negated=False)
    stripped: list[str] = []
    for e in entries:
        name = e[1:]
        if not name:
            raise ConfigError(
                f"{where}: 'hosts' entry '!' is empty after the negation prefix"
            )
        stripped.append(name)
    return HostList(names=stripped, negated=True)


def _parse_interactive_fields(
    raw: dict[str, Any], where: str
) -> tuple[bool, int | None, int | None]:
    """Parse the three `interactive*` fields together.

    Returns `(interactive, active_sec, delay_sec)`. The two `_sec`
    values are `None` when the user did not provide a time-span
    string; the snapshot resolver substitutes the baked default.
    Either time-span set without `interactive = true` is a config
    error (catches "I wrote the knob but forgot the flag"), and a
    zero / negative time-span is rejected.
    """
    raw_interactive = _typed_field(raw, "interactive", bool, where)
    interactive = bool(raw_interactive)
    active_sec = _parse_interactive_timespan(
        raw, "interactive_active", where, interactive
    )
    delay_sec = _parse_interactive_timespan(
        raw, "interactive_delay", where, interactive
    )
    return interactive, active_sec, delay_sec


def _parse_interactive_timespan(
    raw: dict[str, Any], key: str, where: str, interactive: bool
) -> int | None:
    """Parse one `interactive_active` / `interactive_delay` knob."""
    text = _typed_field(raw, key, str, where)
    if text is None:
        return None
    if not interactive:
        raise ConfigError(f"{where}: {key!r} set without 'interactive = true'")
    try:
        # from_str validates the time-span and rejects non-positive.
        return Interval.from_str(text).total_seconds
    except ValueError as e:
        raise ConfigError(f"{where}: {key!r} {e}") from e


def _string_dict(raw: dict[str, Any], key: str, where: str) -> dict[str, str]:
    """Extract a dict-of-string-to-string field, defaulting to {}."""
    val = raw.get(key)
    if val is None:
        return {}
    if not isinstance(val, dict):
        raise ConfigError(f"{where}: '{key}' must be a table (dict)")
    out: dict[str, str] = {}
    for k, v in val.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ConfigError(
                f"{where}: '{key}' values must be string -> string"
            )
        out[k] = v
    return out


def _parse_notify_email_settings(
    raw: dict[str, Any], where: str
) -> NotifyEmail:
    """Parse the email-transport keys from a channel block."""
    to = _typed_field(raw, "to", str, where)
    smtp_host = _typed_field(raw, "smtp_host", str, where)
    smtp_user = _typed_field(raw, "smtp_user", str, where)
    smtp_port = _typed_field(raw, "smtp_port", int, where, default=587)
    if to is None or smtp_host is None or smtp_user is None:
        raise ConfigError(
            f"{where}: 'to', 'smtp_host', 'smtp_user' are required"
        )
    return NotifyEmail(
        to=to,
        from_addr=_typed_field(raw, "from", str, where),
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_starttls=_typed_field(
            raw, "smtp_starttls", bool, where, default=True
        ),
        smtp_pass_keychain_service=_typed_field(
            raw, "smtp_pass_keychain_service", str, where
        ),
        smtp_pass_keychain_account=_typed_field(
            raw, "smtp_pass_keychain_account", str, where
        ),
        smtp_pass_file=_typed_field(raw, "smtp_pass_file", str, where),
    )


def _parse_notify_ntfy_settings(raw: dict[str, Any], where: str) -> NotifyNtfy:
    """Parse the ntfy-transport keys from a channel block."""
    url = _typed_field(raw, "url", str, where)
    if url is None:
        raise ConfigError(f"{where}: 'url' is required")
    return NotifyNtfy(
        url=url,
        token_keychain_service=_typed_field(
            raw, "token_keychain_service", str, where
        ),
        token_keychain_account=_typed_field(
            raw, "token_keychain_account", str, where
        ),
        token_file=_typed_field(raw, "token_file", str, where),
    )


def _positive_int(
    raw: dict[str, Any], key: str, where: str, default: int
) -> int:
    """Read a positive int defaulting to `default`. Reject 0 / negative."""
    val: int = _typed_field(raw, key, int, where, default=default)
    if val <= 0:
        raise ConfigError(f"{where}: '{key}' must be positive, got {val}")
    return val


def _nonneg_int(raw: dict[str, Any], key: str, where: str, default: int) -> int:
    """Read a non-negative int defaulting to `default`. Reject negative.

    0 is accepted: `job_timeout_sec` uses it as the "no wallclock cap"
    sentinel, so the only invalid value is a negative one.
    """
    val: int = _typed_field(raw, key, int, where, default=default)
    if val < 0:
        raise ConfigError(f"{where}: '{key}' must be >= 0, got {val}")
    return val


def _parse_notify_channels(
    raw: dict[str, Any], where: str, *, required: bool
) -> list[str] | None:
    """Parse a `notify_channels` list (structural validation only).

    `required=True` (Defaults) returns [] when the key is absent.
    `required=False` (TomlJob/Target) returns None when absent so the
    cascade can inherit from a layer below. Duplicate entries and
    non-string entries raise. Whether each entry actually resolves
    to a defined channel is checked later in `_validate_config`,
    once the bundle's channel definitions are known.
    """
    if "notify_channels" not in raw:
        return [] if required else None
    val = raw["notify_channels"]
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        raise ConfigError(
            f"{where}: 'notify_channels' must be a list of strings"
        )
    seen: set[str] = set()
    out: list[str] = []
    for ch in val:
        if ch in seen:
            raise ConfigError(
                f"{where}: notify_channels entry {ch!r} listed twice"
            )
        seen.add(ch)
        out.append(ch)
    return out


def _parse_defaults(raw: dict[str, Any], *, is_default: bool) -> Defaults:
    """Parse [defaults].

    `is_default` flags the default bundle (config.toml). A non-default
    bundle that omits `notify_channels` entirely inherits the default
    bundle's notify config (the [NOTIFY_INHERIT_TOKEN] sentinel);
    explicit `notify_channels = []` opts back out to silence.
    """
    where = "[defaults]"
    _reject_unknown_keys(raw, _KNOWN_DEFAULTS, where)
    channels = _parse_notify_channels(raw, where, required=True)
    if "notify_channels" not in raw and not is_default:
        channels = [NOTIFY_INHERIT_TOKEN]
    notify_channel_defs: dict[str, NotifyChannel] = {}
    nested = raw.get("notify", {})
    if not isinstance(nested, dict):
        raise ConfigError(f"{where}.notify must be a table (dict)")
    for sub_key, sub_body in nested.items():
        if sub_key == NOTIFY_INHERIT_TOKEN:
            raise ConfigError(
                f"[defaults.notify.{sub_key}]: {sub_key!r} is a reserved "
                f"channel name (the notify-inherit sentinel)"
            )
        if not isinstance(sub_body, dict):
            raise ConfigError(f"[defaults.notify.{sub_key}]: must be a table")
        notify_channel_defs[sub_key] = NotifyChannel.from_raw(sub_key, sub_body)
    return Defaults(
        notify_channels=channels or [],
        notify_attach_log=_typed_field(
            raw, "notify_attach_log", bool, where, default=True
        ),
        notify_attach_max_kb=_positive_int(
            raw, "notify_attach_max_kb", where, default=256
        ),
        job_timeout_sec=_nonneg_int(
            raw, "job_timeout_sec", where, default=1800
        ),
        trigger_timeout_sec=_positive_int(
            raw, "trigger_timeout_sec", where, default=15
        ),
        log_keep_runs=_positive_int(raw, "log_keep_runs", where, default=30),
        priority=_parse_priority_field(raw, where),
        keep_awake=_typed_field(raw, "keep_awake", bool, where, default=False),
        env=_string_dict(raw, "env", where),
        notify_channel_defs=notify_channel_defs,
    )


def _parse_success_exit_codes(raw: dict[str, Any], where: str) -> list[int]:
    """Parse a job's `success_exit_codes` list (absent -> [])."""
    if "success_exit_codes" not in raw:
        return []
    val = raw["success_exit_codes"]
    # bool is a subclass of int; reject it so `[true]` isn't read as [1].
    if not isinstance(val, list) or not all(
        isinstance(x, int) and not isinstance(x, bool) for x in val
    ):
        raise ConfigError(
            f"{where}: 'success_exit_codes' must be a list of integers"
        )
    for code in val:
        if not 0 <= code <= 255:
            raise ConfigError(
                f"{where}: success_exit_codes entry {code} is out of the "
                f"valid 0-255 exit-code range"
            )
    return val


def _parse_priority_field(
    raw: dict[str, Any], where: str
) -> PriorityClass | None:
    """Parse + validate a `priority` field (None if absent)."""
    return _parse_priority(_typed_field(raw, "priority", str, where), where)


def _parse_job(name: str, raw: dict[str, Any]) -> TomlJob:
    """Parse [job.<name>]."""
    _validate_name(name, f"[job.{name}]")
    where = f"[job.{name}]"
    _reject_unknown_keys(raw, _KNOWN_JOB, where)
    job_uuid = _parse_uuid_field(raw, where)
    if job_uuid is None:
        raise ConfigError(
            f"{where}: 'uuid' is required; run `crony config update` "
            f"to assign UUIDs"
        )
    command = _typed_field(raw, "command", str, where)
    script = _typed_field(raw, "script", str, where)
    if (command is None) == (script is None):
        raise ConfigError(
            f"{where}: must have exactly one of 'command' or 'script'"
        )
    args = _string_list(raw, "args", where)
    if args and command is not None:
        raise ConfigError(
            f"{where}: 'args' is only valid with 'script', not 'command'"
        )
    gate = _typed_field(raw, "gate", str, where)
    gate_script = _typed_field(raw, "gate_script", str, where)
    if gate is not None and gate_script is not None:
        raise ConfigError(
            f"{where}: 'gate' and 'gate_script' are mutually exclusive"
        )
    gate_args = _string_list(raw, "gate_args", where)
    if gate_args and gate_script is None:
        raise ConfigError(
            f"{where}: 'gate_args' is only valid with 'gate_script'"
        )
    schedule_str = _typed_field(raw, "schedule", str, where)
    interval_str = _typed_field(raw, "interval", str, where)
    timing = _parse_timing(schedule_str, interval_str, where)
    priority = _parse_priority_field(raw, where)
    keep_awake = _typed_field(raw, "keep_awake", bool, where, default=None)
    platforms = _parse_platforms_field(raw, where)
    hosts = _parse_hosts_field(raw, where)
    channels = _parse_notify_channels(raw, where, required=False)
    success_exit_codes = _parse_success_exit_codes(raw, where)
    job_timeout_sec = _typed_field(raw, "job_timeout_sec", int, where)
    if job_timeout_sec is not None and job_timeout_sec < 0:
        raise ConfigError(
            f"{where}: 'job_timeout_sec' must be >= 0, got {job_timeout_sec}"
        )
    interactive, interactive_active_sec, interactive_delay_sec = (
        _parse_interactive_fields(raw, where)
    )
    if interactive:
        if not platforms:
            platforms = ["darwin"]
        elif platforms != ["darwin"]:
            raise ConfigError(
                f"{where}: 'interactive = true' implies "
                f"platforms = ['darwin']; remove or change the "
                f"'platforms' override"
            )
    return TomlJob(
        name=name,
        uuid=job_uuid,
        command=command,
        script=script,
        args=args,
        gate=gate,
        gate_script=gate_script,
        gate_args=gate_args,
        timing=timing,
        priority=priority,
        keep_awake=keep_awake,
        platforms=platforms,
        hosts=hosts,
        job_timeout_sec=job_timeout_sec,
        notify_channels=channels,
        success_exit_codes=success_exit_codes,
        env=_string_dict(raw, "env", where),
        interactive=interactive,
        interactive_active_sec=interactive_active_sec,
        interactive_delay_sec=interactive_delay_sec,
    )


def _parse_job_group(name: str, raw: dict[str, Any]) -> TomlJobGroup:
    """Parse [job-group.<name>].

    schedule / interval are both optional: a group with neither is a
    "transit" group that fires only when a parent group dispatches
    it. Cross-cutting validation (per-target chain) ensures every
    selected group is reachable from a scheduled root.
    """
    _validate_name(name, f"[job-group.{name}]")
    where = f"[job-group.{name}]"
    _reject_unknown_keys(raw, _KNOWN_JOB_GROUP, where)
    group_uuid = _parse_uuid_field(raw, where)
    if group_uuid is None:
        raise ConfigError(
            f"{where}: 'uuid' is required; run `crony config update` "
            f"to assign UUIDs"
        )
    jobs = _string_list(raw, "jobs", where)
    if not jobs:
        raise ConfigError(f"{where}: 'jobs' must be a non-empty list")
    schedule_str = _typed_field(raw, "schedule", str, where)
    interval_str = _typed_field(raw, "interval", str, where)
    timing = _parse_timing(schedule_str, interval_str, where)
    platforms = _parse_platforms_field(raw, where)
    hosts = _parse_hosts_field(raw, where)
    return TomlJobGroup(
        name=name,
        uuid=group_uuid,
        jobs=jobs,
        timing=timing,
        platforms=platforms,
        hosts=hosts,
    )


def _parse_target(name: str, kind: str, raw: dict[str, Any]) -> Target:
    """Parse [target.<platform>] or [target.host.<name>]."""
    where = (
        f"[target.{name}]" if kind == "platform" else f"[target.host.{name}]"
    )
    if kind == "host":
        _validate_name(name, where)
    _reject_unknown_keys(raw, _KNOWN_TARGET, where)
    jobs = _string_list(raw, "jobs", where)
    channels = _parse_notify_channels(raw, where, required=False)
    return Target(
        name=name,
        kind=kind,
        jobs=jobs,
        notify_channels=channels,
    )


def _collect_target_parents(
    config: TomlBundleConfig, target: Target
) -> dict[str, list[str]]:
    """Map each name reachable from `target` to its parent reference(s).

    A direct entry in `target.jobs` records `"target"` as the
    parent. A child of a group records `"group <gname>"`. The list
    preserves visit order: target-direct entries first, then group
    children in the order their parent groups were walked. Each
    group is walked at most once per call (so a name that appears
    as a child of two distinct groups records two parent entries,
    but a group reachable through two paths still only contributes
    its children once).

    Used by `_validate_config` to enforce the single-parent
    invariant within a target's dispatch graph. The per-walk
    visited-groups set both deduplicates work for diamond shapes
    and bounds the walk if a cycle ever sneaks in -- so the result
    is well-defined regardless of whether the chain walk that
    rejects cycles ran first.
    """
    parents: dict[str, list[str]] = {}
    seen_groups: set[str] = set()

    def _walk(group_name: str) -> None:
        if group_name in seen_groups:
            return
        seen_groups.add(group_name)
        g = config.job_groups.get(group_name)
        if g is None:
            return
        for child in g.jobs:
            parents.setdefault(child, []).append(f"group {group_name!r}")
            _walk(child)

    for ref in target.jobs:
        parents.setdefault(ref, []).append("target")
        _walk(ref)
    return parents


def validate_notify_channels(
    channels: list[str],
    defined_channels: set[str],
    label: str,
    *,
    is_default: bool,
) -> str | None:
    """Validate one `notify_channels` list. Returns an error message
    (for raise / demote at the call site) or None when valid.

    The inherit sentinel `NOTIFY_INHERIT_TOKEN` pulls in the default
    bundle's notify config and may be combined with explicit sibling
    channels (the resolved set is their union); it is only valid in a
    non-default bundle (the default bundle cannot inherit itself).
    Every non-sentinel entry must name a channel defined in this bundle
    or a zero-config built-in (`BUILTIN_NOTIFY_CHANNELS`, e.g.
    "dialog-popup").
    """
    if NOTIFY_INHERIT_TOKEN in channels and is_default:
        return (
            f"{label}: the default bundle cannot inherit its own "
            f"notify config ({NOTIFY_INHERIT_TOKEN!r} sentinel)"
        )
    for ch in channels:
        if ch == NOTIFY_INHERIT_TOKEN:
            continue
        if ch not in defined_channels and ch not in BUILTIN_NOTIFY_CHANNELS:
            return (
                f"{label}: notify_channels entry {ch!r} is not "
                f"defined; expected one of "
                f"{sorted(defined_channels | BUILTIN_NOTIFY_CHANNELS)}"
            )
    return None


def _validate_config(config: TomlBundleConfig, *, is_default: bool) -> None:
    """Cross-cutting validation: name collisions, references, applicability.

    Per-entity validation failures (a single group with an
    undefined-name reference, a single target with a bad chain or
    bad notify_channels) demote the offending entity into the
    matching errored_* map and remove it from the live map, so
    sibling entries remain loadable and the bundle as a whole keeps
    resolving. Only bundle-level structural failures (name
    collision across `[job.*]` / `[job-group.*]`, `[defaults]`
    notify_channels references) raise and abort the bundle.

    Errored entries participate in name-resolution so other groups
    / targets that reference them don't ALSO fail with
    `undefined name`, but they're skipped in checks that depend on
    per-entity fields (chain-walk schedule discovery,
    notify_channels lookups).
    """
    # Job/group name collision -- errored names participate so a
    # typo'd `[job.x]` plus a valid `[job-group.x]` still surfaces
    # the collision. Structural / unattributable -> raise.
    all_job_names = set(config.jobs) | set(config.errored_jobs)
    all_group_names = set(config.job_groups) | set(config.errored_job_groups)
    overlap = all_job_names & all_group_names
    if overlap:
        raise ConfigError(
            f"name collision: {sorted(overlap)} appear as both "
            f"[job.*] and [job-group.*]"
        )

    # Group children must reference a defined job or group. A bad
    # reference demotes just the offending group; siblings remain
    # loadable. Nested groups are supported -- the per-target chain
    # validation below ensures every reachable path bottoms out in
    # a schedule.
    all_names = all_job_names | all_group_names
    bad_group: dict[str, str] = {}
    for gname, group in config.job_groups.items():
        for child in group.jobs:
            if child not in all_names:
                bad_group[gname] = (
                    f"[job-group.{gname}]: 'jobs' references "
                    f"undefined name {child!r}"
                )
                break
    for gname, msg in bad_group.items():
        del config.job_groups[gname]
        config.errored_job_groups[gname] = msg

    # notify_channels references must resolve to defined channels
    # in this bundle's [defaults.notify.*] section. `[defaults]`
    # references are unattributable -> raise; per-job and per-target
    # references are per-entity -> demote.
    defined_channels = set(config.defaults.notify_channel_defs.keys())
    defaults_msg = validate_notify_channels(
        config.defaults.notify_channels,
        defined_channels,
        "[defaults]",
        is_default=is_default,
    )
    if defaults_msg is not None:
        raise ConfigError(defaults_msg)

    bad_job_channel: dict[str, str] = {}
    for jname, job in config.jobs.items():
        if job.notify_channels is None:
            continue
        job_msg = validate_notify_channels(
            job.notify_channels,
            defined_channels,
            f"[job.{jname}]",
            is_default=is_default,
        )
        if job_msg is not None:
            bad_job_channel[jname] = job_msg
    for jname, msg in bad_job_channel.items():
        del config.jobs[jname]
        config.errored_jobs[jname] = msg

    # Recompute name sets now that demoted groups and jobs sit in
    # the errored_* maps. `all_names` feeds target undefined-name
    # checks; `errored_names` lets the chain walk skip past an
    # errored leaf without piling on a derived "would never fire"
    # error.
    all_job_names = set(config.jobs) | set(config.errored_jobs)
    all_group_names = set(config.job_groups) | set(config.errored_job_groups)
    all_names = all_job_names | all_group_names
    errored_names = set(config.errored_jobs) | set(config.errored_job_groups)

    # Per-target validation: undefined refs in `jobs`, invalid
    # platform name for a `[target.<platform>]`, undefined
    # notify_channels reference, chain cycle, chain with no
    # schedule, and multi-parent within the target's subtree all
    # demote just the offending target. `platforms` / `hosts`
    # filters apply at selection time, not validate time -- a
    # bundle is allowed to describe both darwin and linux entries
    # and have each host pick up only its applicable subset.
    def _validate_target(
        label: str, tname: str, target: Target, is_host: bool
    ) -> str | None:
        if not is_host and tname not in _VALID_PLATFORMS:
            return (
                f"{label}: platform must be one of {sorted(_VALID_PLATFORMS)}"
            )
        for ref in target.jobs:
            if ref not in all_names:
                return f"{label}: 'jobs' references undefined name {ref!r}"
        if target.notify_channels is not None:
            notify_msg = validate_notify_channels(
                target.notify_channels,
                defined_channels,
                label,
                is_default=is_default,
            )
            if notify_msg is not None:
                return notify_msg
        # Chain walk: every path from the target through groups to
        # a leaf job must contain a schedule somewhere; cycles in
        # the group graph are caught on the way down. A walk into
        # an errored entry stops without complaint -- the per-entity
        # error is already attributed and piling on a derived "would
        # never fire" would just hide it.
        chain_error: list[str] = []

        def _walk_chain(
            ref: str, path: tuple[str, ...], seen_schedule: bool
        ) -> None:
            if chain_error:
                return
            if ref in path:
                cycle = " -> ".join(path[path.index(ref) :] + (ref,))
                chain_error.append(f"{label}: cycle in group chain: {cycle}")
                return
            path = path + (ref,)
            if ref in errored_names:
                # Errored leaf: skip. The per-entity error is
                # already attributed; a derived "would never
                # fire" would just hide it. Consequence: a
                # target whose every root resolves only to
                # errored entries passes target validation and
                # dispatches nothing -- the user learns about
                # the dead chain via the load-time ERROR log
                # and `crony config validate`.
                return
            if ref in config.jobs:
                job = config.jobs[ref]
                scheduled = job.timing is not None
                if not seen_schedule and not scheduled:
                    chain_error.append(
                        f"{label}: chain {' -> '.join(path)} has "
                        f"no schedule anywhere -- {ref!r} would "
                        f"never fire"
                    )
                return
            group = config.job_groups[ref]
            scheduled = group.timing is not None
            new_seen = seen_schedule or scheduled
            for child in group.jobs:
                _walk_chain(child, path, new_seen)

        for ref in target.jobs:
            _walk_chain(ref, (), False)
            if chain_error:
                return chain_error[0]

        # Single-parent invariant: within a single target's subtree
        # (the dispatch graph one host activates) each name appears
        # as a child of at most one parent. A duplicate reference
        # inside a single parent's `jobs` list still doubles
        # dispatch and counts. Cross-target overlap is fine: only
        # one target ever activates on a given host.
        parents = _collect_target_parents(config, target)
        for child, parent_list in parents.items():
            if len(parent_list) > 1:
                return (
                    f"{label}: {child!r} has multiple parents in "
                    f"this target's subtree: "
                    f"{', '.join(parent_list)}"
                )
        return None

    bad_platform: dict[str, str] = {}
    for tname, target in config.platform_targets.items():
        err = _validate_target(
            f"[target.{tname}]", tname, target, is_host=False
        )
        if err is not None:
            bad_platform[tname] = err
    for tname, msg in bad_platform.items():
        del config.platform_targets[tname]
        config.errored_platform_targets[tname] = msg

    bad_host: dict[str, str] = {}
    for hname, target in config.host_targets.items():
        err = _validate_target(
            f"[target.host.{hname}]", hname, target, is_host=True
        )
        if err is not None:
            bad_host[hname] = err
    for hname, msg in bad_host.items():
        del config.host_targets[hname]
        config.errored_host_targets[hname] = msg


def _demote_duplicate_uuids(config: TomlBundleConfig, bundle_name: str) -> None:
    """Demote every job/group that shares a UUID with a sibling
    in the same bundle into the errored maps. Operates in-place.

    UUIDs are bundle-scoped, so this check runs after each bundle's
    `TomlBundleConfig.from_raw` rather than across bundles. Duplicates
    are almost
    always a copy-paste mistake; both sides are demoted rather than
    picking a winner so the user sees the conflict on both rows and
    is forced to resolve.
    """
    bucket: dict[str, list[tuple[str, str]]] = {}
    for short, job in config.jobs.items():
        bucket.setdefault(job.uuid, []).append(("job", short))
    for short, group in config.job_groups.items():
        bucket.setdefault(group.uuid, []).append(("job-group", short))

    for uuid_str, entries in bucket.items():
        if len(entries) < 2:
            continue
        full_names = sorted(
            f"{bundle_name}.{short}" for _kind, short in entries
        )
        quoted = [repr(fn) for fn in full_names]
        if len(quoted) == 2:
            sites = f"{quoted[0]} and {quoted[1]}"
        else:
            sites = f"{', '.join(quoted[:-1])}, and {quoted[-1]}"
        msg = (
            f"duplicate uuid {uuid_str} on {sites}; "
            f"edit the config so each entry has a distinct uuid "
            f"(`crony config generate-uuid` prints a fresh one)"
        )
        for kind, short in entries:
            where = f"[{kind}.{short}]"
            entity_msg = f"{where}: {msg}"
            if kind == "job":
                config.jobs.pop(short, None)
                config.errored_jobs[short] = entity_msg
            else:
                config.job_groups.pop(short, None)
                config.errored_job_groups[short] = entity_msg


# =============================================================================
# RESOLUTION
# =============================================================================


def _mask_reason(
    platforms: list[str],
    hosts: HostList,
    *,
    host: str,
    platform: str,
) -> str | None:
    """Return the axis (or axes) that mask this entry, or None.

    Empty `platforms` / empty `hosts.names` = "applies everywhere"
    (the common case). A non-empty `platforms` with no match
    contributes "platform" to the reason. A non-empty `hosts`
    contributes "host" when the membership check fails -- the
    sense flips with `hosts.negated` (allowlist vs denylist).
    Both axes can mask simultaneously (`"host,platform"`); a
    single-axis mask returns `"platform"` or `"host"`. None means
    the entry applies on this (host, platform).
    """
    parts: list[str] = []
    if hosts.names:
        listed = host in hosts.names
        masked = listed if hosts.negated else not listed
        if masked:
            parts.append("host")
    if platforms and platform not in platforms:
        parts.append("platform")
    if not parts:
        return None
    return ",".join(parts)


def _entry_applies_here(
    platforms: list[str],
    hosts: HostList,
    *,
    host: str,
    platform: str,
) -> bool:
    """True if the entry's `platforms` / `hosts` allow this host.

    Empty list = "applies everywhere" (the common case).
    Non-empty = restricts selection to listed values. A non-match
    on either axis filters the entry out at selection time;
    apply / status then never see it on this host.
    """
    return _mask_reason(platforms, hosts, host=host, platform=platform) is None


# Padding factor applied to a group's effective timeout. The 5%
# slack lets a leaf job hit its own timeout (and propagate the
# error up through last-run.json) before the parent group's own
# cumulative deadline fires. Compounds with nesting depth, by
# design.
_GROUP_TIMEOUT_PADDING: float = 1.05
