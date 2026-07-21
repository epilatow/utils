# This is AI generated code

"""crony's notification layer.

Resolves a firing entry's notify channels from the live config (notify
routing is deliberately not pinned in the snapshot, so channel edits
take effect without a re-apply) and dispatches a per-channel
notification for a completed run. One sender per transport -- email,
ntfy, and the native desktop failure dialog -- behind a transport
dispatch table; each records its outcome as a NotificationResult on the
run result. Channel secrets (SMTP password, ntfy token) resolve from the
host keychain or a mode-checked file. Best-effort by contract: a single
channel's failure is captured and never suppresses the others.
"""

import dataclasses
import os
import re
import smtplib as smtplib  # noqa: PLC0414  re-exported for tests
import urllib as urllib  # noqa: PLC0414  re-exported for tests
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import EmailMessage
from pathlib import Path

import crony.config
import crony.errors
import crony.model
import crony.platform
import crony.runtime

_NOTIFY_TIMEOUT_SEC: int = 15


def retrieve_secret(
    *,
    keychain_service: str | None,
    keychain_account: str | None = None,
    file_path: str | None,
) -> str | None:
    """Look up a secret value from the OS keychain or a 0600 file.

    Tries the host keychain first when a keychain service is configured,
    then falls back to reading file_path if the file exists.
    `keychain_account` is optional and disambiguates when multiple
    keychain items share the same service name. Returns None if no
    source is configured or yields a value. Raises PreconditionError if
    a secrets file exists but its mode allows group / world access -- a
    config-time check that surfaces leaked-credential risk before the
    runtime path tries to use it.
    """
    if keychain_service is not None:
        secret = crony.runtime.host().keychain_secret(
            keychain_service, keychain_account
        )
        if secret is not None:
            return secret
    if file_path is not None:
        p = Path(os.path.expanduser(file_path))
        if p.exists():
            # Parent directory: require no group / world bits. A
            # 0700 (or 0500) parent prevents leaks of file names /
            # mtimes / sibling presence even when the secret file
            # itself is locked down to 0600.
            parent = p.parent
            parent_mode = parent.stat().st_mode & 0o777
            if parent_mode & 0o077:
                raise crony.errors.PreconditionError(
                    f"secret directory {parent} is mode "
                    f"0o{parent_mode:o}; require no group / world "
                    f"bits (e.g. 0700)"
                )
            mode = p.stat().st_mode & 0o777
            if mode & 0o077:
                raise crony.errors.PreconditionError(
                    f"secret file {p} is mode 0o{mode:o}; require 0600 "
                    f"(group / world readable secrets are unsafe)"
                )
            return p.read_text(encoding="utf-8").rstrip("\n")
    return None


def _format_summary(result: crony.model.JobRunResult, full_name: str) -> str:
    """One-line-per-field human summary for notification bodies."""
    return (
        f"Job:        {full_name}\n"
        f"Host:       {result.host}\n"
        f"Platform:   {result.platform}\n"
        f"Started:    {result.started_at}\n"
        f"Ended:      {result.ended_at}\n"
        f"Duration:   {result.duration_sec:.1f}s\n"
        f"Exit class: {result.exit_class}\n"
        f"Exit code:  {result.exit_code}\n"
        f"Signal:     {result.signal}\n"
        f"Gate:       {result.gate}\n"
        f"Log path:   {result.log_path}\n"
    )


# Each `crony _run` invocation writes a header line to run.log.
# Two shapes:
#   === <ISO ts> <full.name> pid=<N> ===
#       Ordinary run: name resolved, snapshot loaded, pid known.
#   === <ISO ts> CANCELED <bundle>:<uuid> ===
#       Snapshot couldn't be loaded (missing, unreadable, wrong
#       schema, unknown kind). No pid; the line surfaces the
#       cancel for `crony logs --latest`.
# `extract_latest_log_entry` finds the last `=== ... ===` header
# and returns everything from there to EOF (the most recent run's
# trace). Used by `crony logs --latest` (single-run readout) and
# the ntfy notify path (3KB inline body instead of a full-log
# attachment).
_RUN_HEADER_RE = re.compile(r"^=== ", re.MULTILINE)


def extract_latest_log_entry(text: str) -> str:
    """Return the slice from the last `=== ... ===` header to EOF.

    If there is no header (log empty or pre-header content), the
    full text is returned as-is so callers degrade gracefully.
    """
    matches = list(_RUN_HEADER_RE.finditer(text))
    if not matches:
        return text
    return text[matches[-1].start() :]


def _head_truncate_to_kb(text: str, max_kb: int) -> tuple[str, bool]:
    """Tail a string to at most max_kb KB, head-truncating.

    When truncation occurs, the returned text is prepended with a
    one-line marker noting how many bytes were dropped, so
    recipients know the head is missing. The marker counts toward
    the byte cap so the result still fits within max_kb.

    Used by ntfy notifications (where the goal is "show the most
    recent few KB of failure output") and reusable by any other
    transport with a similar size constraint.
    """
    max_bytes = max_kb * 1024
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    dropped = len(encoded) - max_bytes
    marker = f"[... {dropped} bytes truncated ...]\n"
    marker_bytes = marker.encode("utf-8")
    keep = max_bytes - len(marker_bytes)
    if keep <= 0:
        # Cap is smaller than the marker itself; degrade to marker
        # alone so the result still indicates truncation rather
        # than silently returning a marker-less stub.
        return marker[:max_bytes], True
    tail = encoded[-keep:].decode("utf-8", errors="replace")
    return marker + tail, True


def _head_truncate_to_lines(text: str, max_lines: int) -> tuple[str, bool]:
    """Tail a string to at most max_lines lines, head-truncating.

    When truncation occurs, a one-line `[... N lines truncated ...]`
    marker replaces the dropped head (and counts toward max_lines, so
    the result never exceeds it). Bounds a native dialog's height,
    where a byte cap alone does not: a `display dialog` has no scroll
    and grows vertically with its line count, so a large byte budget
    still overflows the screen.
    """
    lines = text.splitlines(keepends=True)
    if len(lines) <= max_lines:
        return text, False
    kept = lines[-(max_lines - 1) :] if max_lines > 1 else []
    dropped = len(lines) - len(kept)
    marker = f"[... {dropped} lines truncated ...]\n"
    return marker + "".join(kept), True


# ANSI escape sequences (color, cursor moves, window-title OSC) and other
# non-printable control characters carry no meaning outside a terminal: in
# a notification body -- an osascript dialog, an email, an ntfy message --
# they render as garbage (and a stray control char can even confuse the
# renderer). The escape matcher takes OSC first (`ESC ] ... BEL/ST`, whole
# payload) so its `]` introducer is not half-consumed by the trailing
# single-char Fe alternative, then CSI, then any other Fe escape. The
# control matcher drops C0 and C1 controls plus DEL, keeping only tab and
# newline (the whitespace controls that structure the text).
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: ESC ] ... BEL or ST
    r"|\x1b\[[0-?]*[ -/]*[@-~]"  # CSI: ESC [ ... final
    r"|\x1b[@-Z\\-_]"  # other two-char Fe escapes
)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences and non-printable control characters
    from `text`, keeping tab and newline, so a colorized / progress-bar
    log reads as plain text in a notification instead of control-code
    noise. Applied once at dispatch, so every transport shares it."""
    return _CONTROL_CHARS_RE.sub("", _ANSI_ESCAPE_RE.sub("", text))


def _build_email_message(
    result: crony.model.JobRunResult,
    full_name: str,
    log_text: str,
    cfg: crony.config.NotifyEmail,
    attach_log: bool,
    attach_max_kb: int,
    extra_headers: dict[str, str],
) -> EmailMessage:
    """Construct the RFC822 EmailMessage for a job failure.

    `extra_headers` is the channel's user-supplied `headers` dict
    -- arbitrary headers like `Reply-To` or `X-Priority` to attach
    to the message. Crony-controlled headers (To / From / Subject)
    are validated out at parse time, so anything reaching here
    is safe to set directly.
    """
    msg = EmailMessage()
    msg["Subject"] = (
        f"[crony/{result.host}] {full_name} {result.exit_class} "
        f"(exit {result.exit_code})"
    )
    msg["From"] = cfg.from_addr or cfg.smtp_user
    msg["To"] = cfg.to
    if extra_headers:
        for k, v in extra_headers.items():
            msg[k] = v
    body = _format_summary(result, full_name)
    if attach_log and log_text:
        body += _LOG_SEPARATOR + _format_log_for_notification(
            log_text, attach_max_kb
        )
    msg.set_content(body)
    return msg


def _send_email(
    msg: EmailMessage, cfg: crony.config.NotifyEmail, password: str
) -> None:
    """SMTP send with optional STARTTLS + AUTH. Raises smtplib errors."""
    with smtplib.SMTP(
        cfg.smtp_host, cfg.smtp_port, timeout=_NOTIFY_TIMEOUT_SEC
    ) as smtp:
        if cfg.smtp_starttls:
            smtp.starttls()
        smtp.login(cfg.smtp_user, password)
        smtp.send_message(msg)


_NTFY_MAX_BODY_KB: int = 3
_LOG_SEPARATOR: str = "\n--- log (latest run) ---\n"


def _format_log_for_notification(log_text: str, max_kb: int) -> str:
    """Most recent run's log entry, head-truncated to max_kb.

    Both notify transports show only the latest run's slice of
    `run.log`: the structured summary already has the metadata
    for the failing run, and the log content older than the
    most recent header would just be noise (and on ntfy would
    bump out the actual diagnostic detail under the 3 KB cap).
    """
    latest = extract_latest_log_entry(log_text)
    truncated, _trunc = _head_truncate_to_kb(latest, max_kb)
    return truncated


def _build_ntfy_body(summary: str, log_text: str, attach_log: bool) -> bytes:
    """Construct the ntfy POST body.

    Mirrors the email layout (human summary block followed by the
    log) so a recipient subscribed to both transports sees the same
    structured information. The whole body fits inside ntfy's per-
    message budget by sizing the log section to whatever remains
    after the summary; if the log is bigger than that, head-
    truncate it (keeping the tail) so the most recent failure
    output stays visible.

    `attach_log = false` returns the summary alone (matches the
    email path's "summary only when the user opted out of log
    content").
    """
    summary_bytes = summary.encode("utf-8")
    if not (attach_log and log_text):
        return summary_bytes
    sep_bytes = _LOG_SEPARATOR.encode("utf-8")
    max_bytes = _NTFY_MAX_BODY_KB * 1024
    log_budget_bytes = max_bytes - len(summary_bytes) - len(sep_bytes)
    if log_budget_bytes <= 0:
        # Pathological: summary alone exceeds the budget. Keep the
        # summary intact (its structured fields are more useful to
        # the recipient than a truncated stub) and skip the log.
        return summary_bytes
    log_budget_kb = max(1, log_budget_bytes // 1024)
    tail = _format_log_for_notification(log_text, log_budget_kb)
    # Tighten to the byte budget if the kb-rounded helper produced
    # a slightly oversized result (happens when log_budget_bytes
    # isn't a clean multiple of 1024).
    tail_bytes = tail.encode("utf-8")
    if len(tail_bytes) > log_budget_bytes:
        tail_bytes = tail_bytes[-log_budget_bytes:]
    return summary_bytes + sep_bytes + tail_bytes


def _post_ntfy(
    cfg: crony.config.NotifyNtfy,
    token: str,
    title: str,
    exit_class: crony.model.ExitClass,
    body: str,
    log_text: str,
    attach_log: bool,
    extra_headers: dict[str, str],
) -> None:
    """POST to ntfy. Body mirrors the email layout (human summary +
    latest log entry), capped at _NTFY_MAX_BODY_KB. urlopen raises
    HTTPError on >=400 responses; the caller's broad except records
    that as notify_error.

    Inline body, not an attachment: ntfy attachments are publicly
    addressable by URL guessing, so log content goes in the body
    where it's auth-gated by ntfy's regular delivery channel.
    ntfy's per-message limit is 4KB; we cap at 3KB to leave room
    for headers and to keep notifications scannable.

    `extra_headers` is the channel's user-supplied `headers` dict
    -- e.g. `Email = "you@host"` to relay through ntfy's email
    integration, or `Priority = "urgent"` to bump severity. ntfy's
    user-facing headers are unconstrained; crony-controlled keys
    (Authorization / Tags / Title / Filename) are filtered out at
    parse time.
    """
    headers = dict(extra_headers) if extra_headers else {}
    headers["Authorization"] = f"Bearer {token}"
    headers["Title"] = title
    headers["Tags"] = f"warning,{exit_class}"
    data = _build_ntfy_body(body, log_text, attach_log)
    req = urllib.request.Request(
        cfg.url, data=data, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=_NOTIFY_TIMEOUT_SEC):
        pass


def _send_email_for(
    channel: crony.config.NotifyChannel,
    result: crony.model.JobRunResult,
    full_name: str,
    log_text: str,
    defaults: crony.config.Defaults,
) -> None:
    """Send the failure email through the given channel. Raises on any error."""
    if channel.email is None:
        raise crony.errors.ConfigError(
            f"channel {channel.name!r} has no email transport config"
        )
    password = retrieve_secret(
        keychain_service=channel.email.smtp_pass_keychain_service,
        keychain_account=channel.email.smtp_pass_keychain_account,
        file_path=channel.email.smtp_pass_file,
    )
    if password is None:
        raise crony.errors.ConfigError(
            f"channel {channel.name!r}: no SMTP password available "
            f"(configure smtp_pass_keychain_service or smtp_pass_file)"
        )
    msg = _build_email_message(
        result,
        full_name,
        log_text,
        channel.email,
        defaults.notify_attach_log,
        defaults.notify_attach_max_kb,
        extra_headers=channel.headers,
    )
    _send_email(msg, channel.email, password)


def _send_ntfy_for(
    channel: crony.config.NotifyChannel,
    result: crony.model.JobRunResult,
    full_name: str,
    log_text: str,
    defaults: crony.config.Defaults,
) -> None:
    """Send the failure ntfy POST through the channel. Raises on error."""
    if channel.ntfy is None:
        raise crony.errors.ConfigError(
            f"channel {channel.name!r} has no ntfy transport config"
        )
    token = retrieve_secret(
        keychain_service=channel.ntfy.token_keychain_service,
        keychain_account=channel.ntfy.token_keychain_account,
        file_path=channel.ntfy.token_file,
    )
    if token is None:
        raise crony.errors.ConfigError(
            f"channel {channel.name!r}: no ntfy token available "
            f"(configure token_keychain_service or token_file)"
        )
    title = (
        f"[crony/{result.host}] {full_name} "
        f"{result.exit_class} (exit {result.exit_code})"
    )
    _post_ntfy(
        channel.ntfy,
        token,
        title,
        result.exit_class,
        _format_summary(result, full_name),
        log_text,
        defaults.notify_attach_log,
        extra_headers=channel.headers,
    )


# Log content (latest run's tail) shown inside a dialog-popup body,
# capped so the modal stays a normal, clickable size rather than a
# screen-filling wall of text (a `display dialog` has no scroll and
# grows with its content, and an oversized one renders an unresponsive
# OK button). Bounded by BOTH a line count -- the dominant driver of a
# dialog's height -- and a small byte cap that guards very wide lines
# that would wrap. The full log always remains on disk.
_DIALOG_POPUP_LOG_LINES: int = 20
_DIALOG_POPUP_LOG_KB: int = 2


def _format_log_for_dialog(log_text: str, max_lines: int, max_kb: int) -> str:
    """Latest run's log tail, bounded to fit a native dialog.

    Applies the byte cap first (bounds a pathologically wide line that
    would wrap into many display rows), then the line cap (the dominant
    driver of a non-scrolling dialog's height). Line-last keeps the line
    bound exact and leaves a single truncation marker -- the byte cap's
    own head marker, if any, is dropped by the line cap. Keeps the tail
    so the most recent failure output stays visible.
    """
    latest = extract_latest_log_entry(log_text)
    trimmed, _ = _head_truncate_to_kb(latest, max_kb)
    trimmed, _ = _head_truncate_to_lines(trimmed, max_lines)
    return trimmed


def _send_dialog_popup_for(
    _channel: crony.config.NotifyChannel,
    result: crony.model.JobRunResult,
    full_name: str,
    log_text: str,
    defaults: crony.config.Defaults,
) -> None:
    """Pop a native desktop dialog for a failed job. Raises on error.

    Routed through the HostPlatform's failure dialog, so it lands on a
    host with a desktop session. Where `supports_interactive` is False
    it raises, and dispatch records the channel as unsent -- the seam
    where a Linux backend (notify-send / zenity) slots in later. The
    channel is zero-config: `_channel` is unused, kept only to match the
    `_NOTIFY_DISPATCH` sender signature; only the run result + log feed
    the dialog.
    """
    host = crony.runtime.host()
    if not host.supports_interactive:
        raise crony.errors.CronyError(
            "dialog-popup notify channel not implemented on "
            f"{crony.platform.current_platform()!r}"
        )
    title = f"crony: {full_name} {result.exit_class} (exit {result.exit_code})"
    body = _format_summary(result, full_name)
    if defaults.notify_attach_log and log_text:
        tail = _format_log_for_dialog(
            log_text, _DIALOG_POPUP_LOG_LINES, _DIALOG_POPUP_LOG_KB
        )
        if tail.strip():
            body = f"{body}{_LOG_SEPARATOR}{tail}"
    host.show_failure_dialog(title, body)


# Per-transport sender table. Each entry takes (channel, result,
# full_name, log_text, defaults) and raises on failure. Adding a new
# transport is one new function plus one entry here plus a name in
# VALID_NOTIFY_TRANSPORTS.
_NOTIFY_DISPATCH: dict[
    str,
    Callable[
        [
            crony.config.NotifyChannel,
            crony.model.JobRunResult,
            str,
            str,
            crony.config.Defaults,
        ],
        None,
    ],
] = {
    "email": _send_email_for,
    "ntfy": _send_ntfy_for,
    "dialog-popup": _send_dialog_popup_for,
}

# Pin parser-side and dispatcher-side enumerations together so a
# new transport can never be accepted by the parser but unknown to
# dispatch (or vice versa).
assert set(_NOTIFY_DISPATCH.keys()) == crony.config.VALID_NOTIFY_TRANSPORTS, (
    "_NOTIFY_DISPATCH and VALID_NOTIFY_TRANSPORTS have drifted"
)


def _builtin_notify_channel(name: str) -> crony.config.NotifyChannel | None:
    """Synthesize a zero-config built-in channel def for `name`.

    Built-ins (`BUILTIN_NOTIFY_CHANNELS`) carry no per-channel
    settings, so a notify_channels list may name one with no
    `[defaults.notify.<name>]` block; dispatch builds the channel here
    instead of erroring. A built-in's name equals its transport.
    Returns None for non-built-in names.
    """
    if name in crony.config.BUILTIN_NOTIFY_CHANNELS:
        return crony.config.NotifyChannel(name=name, transport=name)
    return None


def dispatch_notify(
    result: crony.model.JobRunResult,
    full_name: str,
    log_text: str,
    defaults: crony.config.Defaults,
) -> None:
    """Fan out to every channel in result.notifications.

    The runner pre-populates result.notifications with the resolved
    channel set (one entry per channel, sent=False). For each, look
    up the bundle's channel definition (containing transport +
    headers + transport config), then dispatch through the
    transport's sender. Per-channel exceptions are recorded so one
    failure doesn't suppress the others.
    """
    # Sanitize once here so every transport body -- dialog, email, ntfy
    # -- shows plain text rather than a terminal's ANSI / control codes.
    # `crony logs` keeps the raw log (terminal colors intact); only the
    # notification path is cleaned.
    log_text = _strip_ansi(log_text)
    for channel_name in list(result.notifications.keys()):
        try:
            channel = defaults.notify_channel_defs.get(channel_name)
            if channel is None:
                channel = _builtin_notify_channel(channel_name)
            if channel is None:
                raise crony.errors.ConfigError(
                    f"unknown notify channel: {channel_name!r} "
                    f"(no [defaults.notify.{channel_name}] block)"
                )
            sender = _NOTIFY_DISPATCH.get(channel.transport)
            if sender is None:
                raise crony.errors.ConfigError(
                    f"unknown notify transport: {channel.transport!r}"
                )
            sender(channel, result, full_name, log_text, defaults)
            result.notifications[channel_name] = crony.model.NotificationResult(
                sent=True, error=None, error_class=None
            )
        except Exception as e:
            # Notify is best-effort; record the failure on this
            # channel and continue with the next one.
            result.notifications[channel_name] = crony.model.NotificationResult(
                sent=False,
                error=f"{type(e).__name__}: {e}",
                error_class=type(e).__name__,
            )


def expand_notify_inherit(
    channels: list[str],
    firing_bundle: str,
    bundles: crony.config.TomlConfig,
    local_defaults: crony.config.Defaults,
) -> tuple[list[str], crony.config.Defaults]:
    """Resolve the notify-inherit sentinel against the default bundle.

    When [NOTIFY_INHERIT_TOKEN] appears in `channels`, it expands in
    place to the default bundle's channel list and the result is the
    union (inherited first) of those channels with the explicit
    siblings listed alongside the sentinel -- de-duped, so a channel
    both inherited and listed locally (e.g. "dialog-popup" in both
    bundles) fires once, not twice. The returned Defaults carries the
    default bundle's channel definitions + attach settings, with the
    local definitions for any genuinely-new sibling channels merged in
    so dispatch can look every resolved channel up.

    Lists with no sentinel pass through unchanged. Degrades to the
    explicit siblings alone (no inheritance) when the default bundle is
    absent or when the firing bundle IS the default bundle, and never
    recurses: a sentinel carried by the default bundle's own list is
    dropped rather than re-expanded.
    """
    if crony.config.NOTIFY_INHERIT_TOKEN not in channels:
        return channels, local_defaults
    extras = [c for c in channels if c != crony.config.NOTIFY_INHERIT_TOKEN]
    if firing_bundle == crony.config.DEFAULT_BUNDLE_NAME:
        return extras, local_defaults
    default_bundle = bundles.by_name(crony.config.DEFAULT_BUNDLE_NAME)
    if default_bundle is None:
        return extras, local_defaults
    inherited = default_bundle.config.defaults
    inherited_channels = [
        c
        for c in inherited.notify_channels
        if c != crony.config.NOTIFY_INHERIT_TOKEN
    ]
    new_extras = [c for c in extras if c not in inherited_channels]
    resolved = inherited_channels + new_extras
    if not new_extras:
        return resolved, inherited
    # Dispatch resolves every channel through one Defaults, so the new
    # local siblings need their definitions alongside the inherited
    # ones. Channels already inherited keep the default bundle's
    # definition; built-ins (e.g. dialog-popup) need none.
    merged_defs = dict(inherited.notify_channel_defs)
    for c in new_extras:
        local_def = local_defaults.notify_channel_defs.get(c)
        if local_def is not None:
            merged_defs[c] = local_def
    merged = dataclasses.replace(inherited, notify_channel_defs=merged_defs)
    return resolved, merged


def resolve_notify_at_runtime(
    full_name: str,
) -> tuple[list[str], crony.config.Defaults, crony.config.SuccessRatio]:
    """Look up notify channels, bundle defaults, and the resolved
    notify-success-ratio from the live config.

    Notify routing is intentionally NOT pinned in the snapshot:
    edits to notify channels / defaults / the ratio take effect on
    the next fire without requiring a re-apply. Falls back gracefully
    when the toml entry has been removed -- `crony status` will
    already be surfacing the orphan, so log-only here is a
    coherent degraded behavior, and an orphan defaults to
    notify-on-every-failure (SuccessRatio(1, 1)).
    """
    bn, short = crony.config.parse_full_name(full_name)
    try:
        bundles = crony.config.TomlConfig.load_all()
    except OSError, crony.errors.ConfigError, crony.errors.UsageError:
        return [], crony.config.Defaults(), crony.config.DEFAULT_SUCCESS_RATIO
    bundle = bundles.by_name(bn)
    if bundle is None:
        return [], crony.config.Defaults(), crony.config.DEFAULT_SUCCESS_RATIO
    config = bundle.config
    target = config.resolve_target()
    job = config.jobs.get(short)
    if job is None:
        channels = list(config.defaults.notify_channels)
        ratio = config.defaults.notify_success_ratio
    else:
        channels = config.resolved_notify_channels(target, job)
        ratio = config.resolved_notify_success_ratio(target, job)
    expanded, defaults = expand_notify_inherit(
        channels, bn, bundles, config.defaults
    )
    return expanded, defaults, ratio
