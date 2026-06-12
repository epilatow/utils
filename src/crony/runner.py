# This is AI generated code

"""crony's run pipeline.

The `crony run <bundle>:<uuid>` entry the platform scheduler invokes:
load the pinned snapshot and dispatch to the per-job or per-group
pipeline. The job pipeline holds a per-entry lock, runs the optional
gate, execs the command under a wallclock cap (with the interactive
wait / approval dialog for interactive jobs), writes the last-run
record, and hands a failed run to the notification layer. The group
pipeline fires each child through its own platform unit and waits
synchronously for it to exit via a kernel pid-exit notification, then
rolls the children up into the group's result.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import json
import math
import os
import shlex
import signal
import string
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import (
    IO,
    Any,
    NamedTuple,
)

import crony.errors
import crony.model
import crony.notify
import crony.platform
import crony.runtime
import crony.unit


def timeout_to_wait(sec: int) -> float:
    """Map a resolved timeout (0 = no cap) to the float the pid-watch
    waiters take: math.inf for no cap, else the value. Used wherever a
    resolved/snapshot timeout feeds `trigger_unit_sync`, so the
    uncapped sentinel reaches it as inf (which it converts to a
    no-timeout wait) rather than as 0 (a near-instant wait)."""
    return math.inf if sec == 0 else float(sec)


def _rollup_group_exit_class(
    children: list[crony.model.GroupChildResult],
) -> crony.model.ExitClass:
    """Worst-of children's exit_class, with a precedence ladder.

    A group with no children, or one whose children are all `ok`,
    `gated`, or `dispatched`, rolls up as `ok`: gating is a per-
    child concept ("intentionally not run") that doesn't describe
    a group-level outcome, and `dispatched` records an interactive
    child the group fired async (it makes no claim about whether
    the user later clicked Run Job). Any child failure (fail /
    signal / timeout) surfaces as the worst child's exit_class --
    timeout outranks fail / signal so a group with a timed-out
    child shows TIMEOUT in the status view rather than being
    masked by a sibling's plain fail.
    """
    ec = crony.model.ExitClass
    precedence = {
        ec.OK: 0,
        ec.GATED: 0,
        ec.DISPATCHED: 0,
        ec.FAIL: 1,
        ec.SIGNAL: 1,
        ec.TIMEOUT: 2,
    }
    worst_score = 0
    worst_class = ec.OK
    for c in children:
        score = precedence.get(c.exit_class, 1)
        if score > worst_score:
            worst_score = score
            worst_class = c.exit_class
    return worst_class


def _expand_env_value(raw: str, env: dict[str, str]) -> str:
    """Expand `$VAR` / `${VAR}` references in `raw` against `env`.

    Uses `string.Template.safe_substitute` so unresolved references
    stay literal (shell-like behavior: `$NOTHING` becomes the string
    `"$NOTHING"`, not an error). `$$` escapes to a literal `$`. The
    expansion is single-pass: a value that contains another expanded
    value's reference resolves once, not recursively.
    """
    return string.Template(raw).safe_substitute(env)


def _runtime_env(user_env: dict[str, str]) -> dict[str, str]:
    """Build the env dict passed to the wrapped command.

    The command runs from the platform scheduler's unit, which hands
    the runner an already minimal, curated environment. crony passes
    that inherited env through unchanged: setting it up is the
    scheduler's job, and
    re-filtering it strips session locators a job may need --
    notably XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS, without
    which a job that shells out to `systemctl --user` (e.g. one
    whose command is `crony apply`) cannot reach the user bus.

    `user_env` (the literal toml `env` dict) is overlaid on top and
    each value is expanded against the running merged env, so a job
    can write `PATH = "/extra:$PATH"` and have $PATH resolve to the
    inherited PATH (or to a value an earlier user_env key set).

    Built at fire time, not apply time, so the snapshot stores
    `user_env` only -- pinning the inherited env would make the
    snapshot churn across the shells an apply runs from.
    """
    env: dict[str, str] = dict(os.environ)
    for k, raw in user_env.items():
        env[k] = _expand_env_value(raw, env)
    return env


def _command_argv(snap: crony.model.Job) -> list[str]:
    """argv for the job's main command, drawn from the snapshot.

    Snapshot fields are pre-resolved at apply time (script paths
    absolute, `~` and `$VAR` expanded), so this is just argv
    assembly with no further substitution.
    """
    if snap.command is not None:
        return ["/bin/sh", "-c", snap.command]
    assert snap.script is not None
    return [snap.script, *snap.args]


def _gate_argv(snap: crony.model.Job) -> list[str] | None:
    """argv for the gate, or None if the snapshot has no gate."""
    if snap.gate is not None:
        return ["/bin/sh", "-c", snap.gate]
    if snap.gate_script is not None:
        return [snap.gate_script, *snap.gate_args]
    return None


def _full_disk_access_argv(argv: list[str], snap: crony.model.Job) -> list[str]:
    """Wrap `argv` so a full-disk-access job runs with macOS Full Disk
    Access, via the HostPlatform (Crony.app on darwin, a no-op
    elsewhere).

    Raises PreconditionError when the wrapper is missing or the grant is
    not in effect -- the run is recorded `canceled` rather than firing
    the command without the access it needs. A stale-but-present wrapper
    still runs. A job without the flag is returned unchanged.
    """
    if not snap.full_disk_access:
        return argv
    return crony.runtime.host().full_disk_access_argv(argv)


def _keep_awake_argv(
    argv: list[str], snap: crony.model.Job
) -> tuple[list[str], str | None]:
    """Wrap `argv` so the machine stays awake while the command runs.

    Returns (argv, note). When `snap.keep_awake` is set, delegates to
    the HostPlatform to wrap the command in the host's sleep-inhibitor,
    which propagates the wrapped command's exit code and tears it down
    when killed. A missing helper binary must never fail the job, so the
    command runs unwrapped and `note` (logged by the caller) explains
    why. Lid-close on battery still sleeps the machine -- no userspace
    mechanism prevents that.
    """
    if not snap.keep_awake:
        return argv, None
    return crony.runtime.host().keep_awake_argv(argv, str(snap.entity_name))


class _ExitOutcome(NamedTuple):
    """Outcome of a wrapped subprocess invocation.

    `signal` is set to the killing signal number when the process was
    terminated by a signal, and None when it exited normally. `rc` is
    0 in the signal case and the process's own exit code otherwise.
    """

    rc: int
    signal: int | None


def _exec_with_timeout(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: int | None,
    log_file: IO[bytes],
) -> _ExitOutcome:
    """Exec argv, piping stdout+stderr into log_file.

    On timeout, sends SIGTERM, then SIGKILL after a grace window, and
    re-raises subprocess.TimeoutExpired. `timeout=None` runs the command
    with no wallclock cap (the caller passes None when the entry's
    resolved timeout is 0).
    """
    proc = subprocess.Popen(
        argv,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise
    if rc < 0:
        return _ExitOutcome(rc=0, signal=-rc)
    return _ExitOutcome(rc=rc, signal=None)


def _wait_for_pid_exit(
    pid: int, timeout: float | None
) -> crony.platform.PidWait:
    """Block until `pid` exits via the host's kernel-level pid-exit
    notification; delegates to the HostPlatform backend. `timeout=None`
    waits indefinitely."""
    return crony.runtime.host().wait_for_pid_exit(pid, timeout)


def _pid_alive(pid: int) -> bool:
    """True if `pid` names a live process. Signal 0 probes existence
    without delivering anything; a process owned by another user
    (PermissionError) still exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_user_active(
    required_sec: int,
    *,
    bypass_check: Callable[[], bool] | None = None,
    poll_sec: int = 30,
    idle_break_sec: int = 60,
) -> bool:
    """Block until the user has been active for `required_sec`
    continuous seconds, polling every `poll_sec`. Returns True on
    success; returns False when `bypass_check` returns True so
    the caller can short-circuit (a user trigger arriving while a
    pending waiter holds the lock signals via this path).

    "Active" means HID idle <= idle_break_sec and the screen is not
    locked, both read from the HostPlatform backend. Continuity is
    measured from the moment the wait first observed the user as active
    -- a user who was already typing when the wait began still has to
    satisfy the full `required_sec` of polled-active time before the
    wait returns. Any idle break (locked screen, idle > break) resets
    the accumulator. `poll_sec` must be < `idle_break_sec` so a brief
    active blip between polls doesn't get missed; the defaults (30s
    poll, 60s break) give a healthy margin.
    """
    host = crony.runtime.host()
    active_since: float | None = None
    while True:
        if bypass_check is not None and bypass_check():
            return False
        idle = host.hid_idle_seconds()
        locked = host.screen_locked()
        present = not locked and idle <= idle_break_sec
        if present:
            now = time.monotonic()
            if active_since is None:
                active_since = now - idle
            elif now - active_since >= required_sec:
                return True
        else:
            active_since = None
        time.sleep(poll_sec)


def _delay_or_bypass(
    delay_sec: int,
    *,
    bypass_check: Callable[[], bool],
    poll_sec: int = 30,
) -> bool:
    """Sleep up to `delay_sec`, polling `bypass_check` every
    `poll_sec`. Returns True if the full delay elapsed; False if
    `bypass_check` returned True mid-sleep. Used by the
    interactive delay-then-reprompt path so a "Delay Job" choice
    doesn't strand the runner in an unbreakable hour-long sleep
    when the user changes their mind and triggers manually.
    """
    elapsed = 0
    while elapsed < delay_sec:
        if bypass_check():
            return False
        chunk = min(poll_sec, delay_sec - elapsed)
        time.sleep(chunk)
        elapsed += chunk
    return True


_INTERACTIVE_BUTTONS = ["Cancel Job", "Delay Job", "Run Job"]


def _show_interactive_dialog(job_name: str, message: str) -> str:
    """Pop the three-button approval dialog. Returns one of
    'run' / 'delay' / 'cancel'.

    Delegates the dialog to the HostPlatform backend and maps the
    clicked button: Run Job -> 'run', Delay Job -> 'delay'. Clicking
    the cancel button, dismissing the dialog, or any backend failure
    yields no choice and maps to 'cancel' -- silently running without
    user confirmation would defeat the whole point of the flag.
    """
    clicked = crony.runtime.host().show_dialog(
        f"crony: {job_name}", message, _INTERACTIVE_BUTTONS
    )
    if clicked == "Run Job":
        return "run"
    if clicked == "Delay Job":
        return "delay"
    return "cancel"


def _interactive_wait_and_prompt(
    snap: crony.model.Job, log_file: IO[bytes]
) -> str:
    """Run the wait/prompt/delay loop for an interactive job.

    Returns 'run' or 'cancel'. Logs each phase transition into
    the run.log so a post-mortem reader can see how long the
    user took, whether they delayed, etc.

    Polls `consume_user_trigger_flag` inside both the active-
    wait and the delay-sleep so a `crony trigger` arriving while
    this run is pending breaks the waiter out and runs the
    command. `crony trigger` writes that flag in its own process
    (its fire of the already-running unit is a no-op), so polling
    is how the pending run learns of it. Without the poll, the
    flag would sit on disk until the next scheduled fire and
    silently bypass *that* wait.
    """

    sd = snap.state_dir

    def _bypass() -> bool:
        return crony.runtime.consume_user_trigger_flag(sd)

    while True:
        log_file.write(
            (
                f"interactive: waiting for "
                f"{snap.interactive_active_sec}s continuous active "
                f"input\n"
            ).encode()
        )
        if not _wait_for_user_active(
            snap.interactive_active_sec, bypass_check=_bypass
        ):
            log_file.write(
                b"interactive: bypass (user-triggered during wait)\n"
            )
            return "run"
        log_file.write(b"interactive: prompting user\n")
        choice = _show_interactive_dialog(
            str(snap.entity_name),
            f"crony wants to run '{snap.entity_name}'. Now?",
        )
        log_file.write(f"interactive: user chose {choice}\n".encode())
        if choice in ("run", "cancel"):
            return choice
        log_file.write(
            f"interactive: delaying for "
            f"{snap.interactive_delay_sec}s\n".encode()
        )
        if not _delay_or_bypass(
            snap.interactive_delay_sec, bypass_check=_bypass
        ):
            log_file.write(
                b"interactive: bypass (user-triggered during delay)\n"
            )
            return "run"


def _run_job(
    snap: crony.model.Job,
    *,
    dry_run: bool = False,
    skip_gate: bool = False,
) -> int:
    """Run a single job from its applied snapshot.

    Returns an exit code suitable for the platform scheduler: the
    wrapped command's exit code on completion (0 on success or any
    nonzero code from the command), ExitCode.LOCK_BUSY on lock
    contention, ExitCode.TIMEOUT on wallclock cap, or 0 for a
    gated/dry-run skip. Precondition failures (missing script) are
    signalled by raising PreconditionError -- cli() maps that to
    ExitCode.PRECONDITION.
    """
    full_name = str(snap.entity_name)

    if snap.script is not None:
        sp = Path(snap.script)
        if not sp.exists():
            raise crony.errors.PreconditionError(f"script not found: {sp}")
        if not os.access(sp, os.X_OK):
            raise crony.errors.PreconditionError(f"script not executable: {sp}")

    sd = snap.state_dir
    sd.mkdir(parents=True, exist_ok=True)
    lock_path = sd / "run.lock"
    log_path = snap.log_path_resolved
    last_run_path = sd / "last-run.json"
    pid_path = sd / "run.pid"

    notify_channels, notify_defaults = crony.notify.resolve_notify_at_runtime(
        full_name
    )
    # snap.env stores the user-written env literally; overlay it on
    # the inherited env at fire time (see _runtime_env).
    env = _runtime_env(snap.env)
    # Name the in-flight entity to descendants so a job whose command
    # runs `crony apply` can detect an apply targeting its own unit and
    # decline to reload it mid-run.
    env[crony.runtime.RUNNING_REF_ENV] = str(snap.entity_ref)
    started = time.time()
    started_iso = crony.runtime.now_iso()
    host = crony.platform.current_host()
    platform = crony.platform.current_platform()

    try:
        with crony.runtime.acquire_lock(lock_path):
            if dry_run:
                return 0
            # Publish our pid for waiters (parent groups, `crony
            # trigger --wait`) to watch for exit. The pid file lives
            # only for the duration of the lock-holding run; cleaned
            # up in `finally`.
            pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

            log_size_before = (
                log_path.stat().st_size if log_path.exists() else 0
            )
            log_file = open(log_path, "ab", buffering=0)
            try:
                timeout_label = (
                    "none" if snap.timeout == 0 else f"{snap.timeout}s"
                )
                header = (
                    f"\n=== {started_iso} {full_name} "
                    f"timeout={timeout_label} pid={os.getpid()} ===\n"
                ).encode()
                log_file.write(header)

                gate: str = "none"
                gate_cmd = _gate_argv(snap)
                if gate_cmd is not None and not skip_gate:
                    log_file.write(f"gate: {shlex.join(gate_cmd)}\n".encode())
                    gate_rc: int
                    try:
                        gate_proc = subprocess.run(
                            gate_cmd,
                            stdout=log_file,
                            stderr=subprocess.STDOUT,
                            env=env,
                            timeout=30,
                        )
                        gate_rc = gate_proc.returncode
                    except subprocess.TimeoutExpired:
                        gate_rc = -1
                    gate = "passed" if gate_rc == 0 else "failed"

                    if gate == "failed":
                        log_file.write(
                            (f"gate exited {gate_rc}: skipping job\n").encode()
                        )
                        result = crony.model.JobRunResult(
                            host=host,
                            platform=platform,
                            started_at=started_iso,
                            ended_at=crony.runtime.now_iso(),
                            duration_sec=time.time() - started,
                            exit_class=crony.model.ExitClass.GATED,
                            exit_code=0,
                            signal=None,
                            process_exit=0,
                            gate=gate,
                            log_path=str(log_path),
                            log_bytes_this_run=(
                                log_path.stat().st_size - log_size_before
                            ),
                            notifications={},
                        )
                        crony.runtime.write_last_run(
                            last_run_path, dataclasses.asdict(result)
                        )
                        return 0

                # Build and FDA-wrap the command past the gate but before
                # the interactive prompt: a gated-skip job never reaches
                # here (FDA is irrelevant when the command won't run),
                # while a full-disk-access job whose wrapper is missing /
                # ungranted is canceled now -- without prompting the user
                # for a run that can't proceed. keep-awake wraps this
                # outermost at the exec site below.
                argv = _full_disk_access_argv(_command_argv(snap), snap)

                if snap.interactive:
                    bypass = crony.runtime.consume_user_trigger_flag(sd)
                    if bypass:
                        log_file.write(
                            b"interactive: bypass (user-triggered)\n"
                        )
                    else:
                        pending_flag = sd / "pending.flag"
                        pending_flag.write_bytes(b"")
                        try:
                            choice = _interactive_wait_and_prompt(
                                snap, log_file
                            )
                        finally:
                            pending_flag.unlink(missing_ok=True)
                        if choice == "cancel":
                            result = crony.model.JobRunResult(
                                host=host,
                                platform=platform,
                                started_at=started_iso,
                                ended_at=crony.runtime.now_iso(),
                                duration_sec=time.time() - started,
                                exit_class=crony.model.ExitClass.CANCELED,
                                exit_code=0,
                                signal=None,
                                process_exit=0,
                                gate=gate,
                                log_path=str(log_path),
                                log_bytes_this_run=(
                                    log_path.stat().st_size - log_size_before
                                ),
                                notifications={},
                            )
                            crony.runtime.write_last_run(
                                last_run_path, dataclasses.asdict(result)
                            )
                            return 0

                # keep-awake stays outermost so the power assertion is
                # held for the whole run, the FDA wrapper included.
                argv, keep_awake_note = _keep_awake_argv(argv, snap)
                if keep_awake_note is not None:
                    log_file.write(f"{keep_awake_note}\n".encode())
                log_file.write(f"exec: {shlex.join(argv)}\n".encode())
                try:
                    rc, sig = _exec_with_timeout(
                        argv,
                        env=env,
                        timeout=snap.timeout or None,
                        log_file=log_file,
                    )
                    if sig is not None:
                        exit_class = crony.model.ExitClass.SIGNAL
                        exit_code: int | None = None
                        surfaced_rc = 128 + sig
                    elif rc == 0 or rc in snap.success_exit_codes:
                        # A code the job declares as success (exit 0, or
                        # a configured non-zero like borg's warning exit
                        # 1): classify "ok" and surface 0 so the platform
                        # scheduler doesn't record the unit as failed.
                        exit_class = crony.model.ExitClass.OK
                        exit_code = rc
                        surfaced_rc = 0
                    else:
                        exit_class = crony.model.ExitClass.FAIL
                        exit_code = rc
                        surfaced_rc = rc
                except subprocess.TimeoutExpired:
                    exit_class = crony.model.ExitClass.TIMEOUT
                    exit_code = None
                    sig = None
                    surfaced_rc = int(crony.errors.ExitCode.TIMEOUT)

                # Pre-populate per-channel slots with sent=False so
                # the dispatcher can update each entry in place. Order
                # is preserved (Python dict insertion order) so the
                # JSON record reflects the configured channel order.
                notifications: dict[str, crony.model.NotificationResult] = {
                    ch: crony.model.NotificationResult(sent=False)
                    for ch in notify_channels
                }
                result = crony.model.JobRunResult(
                    host=host,
                    platform=platform,
                    started_at=started_iso,
                    ended_at=crony.runtime.now_iso(),
                    duration_sec=time.time() - started,
                    exit_class=exit_class,
                    exit_code=exit_code,
                    signal=sig,
                    process_exit=surfaced_rc,
                    gate=gate,
                    log_path=str(log_path),
                    log_bytes_this_run=(
                        log_path.stat().st_size - log_size_before
                    ),
                    notifications=notifications,
                )

                if exit_class != crony.model.ExitClass.OK and notify_channels:
                    log_text = ""
                    try:
                        log_text = log_path.read_text(
                            encoding="utf-8", errors="replace"
                        )[-200_000:]
                    except OSError:
                        pass
                    crony.notify.dispatch_notify(
                        result, full_name, log_text, notify_defaults
                    )

                crony.runtime.write_last_run(
                    last_run_path, dataclasses.asdict(result)
                )
                return surfaced_rc
            finally:
                log_file.close()
                pid_path.unlink(missing_ok=True)
    except crony.errors.LockBusyError:
        with open(log_path, "ab", buffering=0) as f:
            f.write(
                f"[{crony.runtime.now_iso()}] LOCK_BUSY: skipping "
                f"(already running)\n".encode()
            )
        return int(crony.errors.ExitCode.LOCK_BUSY)


def _child_full_name_from_uuid(child_ref: crony.unit.EntityRef) -> str | None:
    """Resolve a child's ref (as stored in a parent's
    `snapshot.children` -- uuids paired with the parent's bundle)
    to its full namespaced name by reading the child's own
    snapshot. The runner dispatches each child via the platform
    unit label, which is keyed by full name; storing uuids on the
    parent keeps the parent's snapshot stable across child renames,
    at the cost of one snapshot read per child at dispatch time.
    Returns None when the child snapshot is missing or unreadable.
    """
    snap_p = crony.model.Job.state_dir_from_ref(child_ref) / "snapshot.json"
    if not snap_p.is_file():
        return None
    try:
        raw = json.loads(snap_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    name = raw.get("name")
    return name if isinstance(name, str) else None


def _child_timeout_from_snapshot(child_ref: crony.unit.EntityRef) -> float:
    """Per-child wait cap for the group runner. Reads the child's
    pinned snapshot rather than the live config, so the cap is
    consistent with what the child's own runner will enforce.
    Falls back to a generous default if the child snapshot is
    missing -- `crony status` will surface that as a broken or
    stale entry, but the trigger itself will fail fast and the
    group's `timeout` budget still bounds the wait.

    Returns `math.inf` for an uncapped child (a `timeout` of 0); the
    caller converts that to "wait with no cap" at the pid-watch.
    """
    try:
        cs = crony.runtime.load_snapshot(child_ref)
    except crony.errors.PreconditionError:
        return float(_DEFAULT_CHILD_TIMEOUT_FALLBACK)
    return timeout_to_wait(cs.timeout)


def _child_is_interactive(child_ref: crony.unit.EntityRef) -> bool:
    """True iff the child is a job whose pinned snapshot has
    `interactive = True`. Groups and non-interactive jobs return
    False. A missing snapshot returns False so the existing
    UnitNotInstalledError path inside the sync dispatch still
    fires and surfaces the issue with its usual error.
    """
    try:
        cs = crony.runtime.load_snapshot(child_ref)
    except crony.errors.PreconditionError:
        return False
    return isinstance(cs, crony.model.Job) and cs.interactive


_DEFAULT_CHILD_TIMEOUT_FALLBACK: int = 1800


def _run_group(
    snap: crony.model.JobGroup,
    *,
    dry_run: bool = False,
) -> int:
    """Run a job-group from its applied snapshot.

    Fires each child through the platform scheduler in order,
    waiting for completion via kernel-level pid-exit notification
    (`trigger_unit_sync`). The group's `run.log` becomes a
    sequencer trace (fired/finished lines per child); child
    stdout/stderr stays in each child's own log. The group never
    sends notifications; each child's own runner handles notify
    per the cascade.

    The cumulative deadline (`snap.timeout`) was pinned at apply
    time. Per-child wait cap pulls the child's pinned timeout from
    its own snapshot so cap and child enforcement agree. A group
    never applies a run-gate of its own -- each child's runner reads
    its own snapshot and gates itself.
    """
    full_name = str(snap.entity_name)
    trigger_timeout = snap.trigger_timeout_sec

    sd = snap.state_dir
    sd.mkdir(parents=True, exist_ok=True)
    lock_path = sd / "run.lock"
    log_path = snap.log_path_resolved
    last_run_path = sd / "last-run.json"
    pid_path = sd / "run.pid"

    started = time.time()
    started_iso = crony.runtime.now_iso()
    host = crony.platform.current_host()
    platform = crony.platform.current_platform()

    try:
        with crony.runtime.acquire_lock(lock_path):
            if dry_run:
                return 0
            pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

            log_file = open(log_path, "ab", buffering=0)
            children: list[crony.model.GroupChildResult] = []
            try:
                # snap.children stores uuids; resolve each to its
                # current full name via the child's own snapshot.
                # A None resolution means the child has no state
                # dir on this host -- log and treat as a fail row
                # so the group rollup catches it.
                resolved_children: list[
                    tuple[crony.unit.EntityRef, str | None]
                ] = [
                    (
                        crony.unit.EntityRef(snap.bundle, child_uuid),
                        _child_full_name_from_uuid(
                            crony.unit.EntityRef(snap.bundle, child_uuid)
                        ),
                    )
                    for child_uuid in snap.children
                ]
                resolved_pairs = [
                    (r, n) for r, n in resolved_children if n is not None
                ]
                header_children = [
                    n if n is not None else str(r) for r, n in resolved_children
                ]
                timeout_label = (
                    "none" if snap.timeout == 0 else f"{snap.timeout}s"
                )
                header = (
                    f"\n=== {started_iso} group {full_name} "
                    f"children={header_children} "
                    f"timeout={timeout_label} pid={os.getpid()} ===\n"
                ).encode()
                log_file.write(header)

                deadline = (
                    math.inf
                    if snap.timeout == 0
                    else time.monotonic() + snap.timeout
                )
                for child_ref, child_full_name in resolved_pairs:
                    child_sd = crony.model.Job.state_dir_from_ref(child_ref)
                    if _child_is_interactive(child_ref):
                        # Interactive children fire async (no wait,
                        # no budget deduction). Their own runner does
                        # the user-active wait + dialog independently;
                        # the parent group's outcome doesn't depend on
                        # whether the user ever clicked Run Job.
                        log_file.write(
                            (
                                f"-> {child_full_name}: dispatched "
                                f"(interactive, async)\n"
                            ).encode()
                        )
                        try:
                            trigger_unit(child_full_name)
                            children.append(
                                crony.model.GroupChildResult(
                                    name=child_full_name,
                                    exit_class=crony.model.ExitClass.DISPATCHED,
                                    exit_code=0,
                                )
                            )
                        except crony.errors.UnitNotInstalledError as e:
                            log_file.write(f"   {e}\n".encode())
                            children.append(
                                crony.model.GroupChildResult(
                                    name=child_full_name,
                                    exit_class=crony.model.ExitClass.FAIL,
                                    exit_code=int(
                                        crony.errors.ExitCode.PRECONDITION
                                    ),
                                )
                            )
                        continue

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        log_file.write(
                            (
                                f"-> {child_full_name}: TIMEOUT "
                                f"(group budget exhausted)\n"
                            ).encode()
                        )
                        children.append(
                            crony.model.GroupChildResult(
                                name=child_full_name,
                                exit_class=crony.model.ExitClass.TIMEOUT,
                                exit_code=int(crony.errors.ExitCode.TIMEOUT),
                            )
                        )
                        continue

                    child_timeout = _child_timeout_from_snapshot(child_ref)
                    child_wait = min(child_timeout, remaining)

                    wait_label = (
                        "none"
                        if math.isinf(child_wait)
                        else f"{child_wait:.0f}s"
                    )
                    log_file.write(
                        (
                            f"-> {child_full_name} (timeout={wait_label})\n"
                        ).encode()
                    )
                    try:
                        rec = trigger_unit_sync(
                            child_full_name,
                            state_dir=child_sd,
                            job_timeout=child_wait,
                            trigger_timeout=trigger_timeout,
                        )
                    except crony.errors.JobTimeoutError as e:
                        # A child that never started (TriggerStartTimeout)
                        # or that overran / wedged past its wait budget
                        # (TriggerWaitTimeout): record it timed-out and
                        # move on rather than letting it stall the group.
                        log_file.write(f"   {e}\n".encode())
                        children.append(
                            crony.model.GroupChildResult(
                                name=child_full_name,
                                exit_class=crony.model.ExitClass.TIMEOUT,
                                exit_code=int(crony.errors.ExitCode.TIMEOUT),
                            )
                        )
                        continue
                    except crony.errors.UnitNotInstalledError as e:
                        log_file.write(f"   {e}\n".encode())
                        children.append(
                            crony.model.GroupChildResult(
                                name=child_full_name,
                                exit_class=crony.model.ExitClass.FAIL,
                                exit_code=int(
                                    crony.errors.ExitCode.PRECONDITION
                                ),
                            )
                        )
                        continue
                    cls = (
                        crony.model.ExitClass.parse(rec.get("exit_class"))
                        or crony.model.ExitClass.FAIL
                    )
                    rc = trigger_exit_code(rec)
                    log_file.write(f"   finished: {cls} (exit {rc})\n".encode())
                    children.append(
                        crony.model.GroupChildResult(
                            name=child_full_name,
                            exit_class=cls,
                            exit_code=rc,
                        )
                    )

                # Synthetic FAIL rows for children whose snapshot is
                # absent on this host (rename mid-flight, partial
                # state wipe, or a uuid the parent's snapshot still
                # references but no child snapshot resolves). Emitted
                # after the dispatch loop so log order matches the
                # header's child list.
                for child_ref, resolved_name in resolved_children:
                    if resolved_name is None:
                        log_file.write(
                            (
                                f"-> ref {child_ref}: "
                                f"no snapshot on this host (FAIL)\n"
                            ).encode()
                        )
                        children.append(
                            crony.model.GroupChildResult(
                                name=str(child_ref),
                                exit_class=crony.model.ExitClass.FAIL,
                                exit_code=int(
                                    crony.errors.ExitCode.PRECONDITION
                                ),
                            )
                        )

                result = crony.model.GroupRunResult(
                    host=host,
                    platform=platform,
                    started_at=started_iso,
                    ended_at=crony.runtime.now_iso(),
                    duration_sec=time.time() - started,
                    exit_class=_rollup_group_exit_class(children),
                    process_exit=0,
                    log_path=str(log_path),
                    jobs_run=children,
                )
                crony.runtime.write_last_run(
                    last_run_path, dataclasses.asdict(result)
                )
                return 0
            finally:
                log_file.close()
                pid_path.unlink(missing_ok=True)
    except crony.errors.LockBusyError:
        with open(log_path, "ab", buffering=0) as f:
            f.write(
                f"[{crony.runtime.now_iso()}] LOCK_BUSY: skipping "
                f"(already running)\n".encode()
            )
        return int(crony.errors.ExitCode.LOCK_BUSY)


def trigger_unit(
    name: str,
    platform: str | None = None,
    *,
    triggered_by_user: bool = False,
    state_dir: Path | None = None,
) -> None:
    """Ask the platform scheduler to fire `name` immediately.

    Runs the unit now -- the scheduler's run-immediately path, as
    opposed to queuing the next scheduled fire.

    Both platforms treat "trigger an already-running unit" as a
    no-op -- the in-flight run continues and there is no fresh
    fire. Waiters that need to know the next completion's outcome
    use `trigger_unit_sync` instead.

    `triggered_by_user` writes a one-shot sentinel flag in the
    entity's state dir, consumed by `crony run` on startup to
    skip the interactive wait. The caller passes `state_dir`
    (resolved from the entity's `(bundle, uuid)` via Config) so
    the flag lands in the right uuid-keyed dir; when
    `triggered_by_user` is True and `state_dir` is None the call
    raises `ValueError`. Group-dispatched fires
    (`_run_group` -> `trigger_unit_sync` -> `trigger_unit`) keep
    the default False so a group never accidentally bypasses an
    interactive wait. Under the current validation rules groups
    can't contain interactive jobs so this is defense-in-depth,
    but it keeps the signal scoped to the dispatcher rather than
    sprinkled at the CLI handler.

    Raises `UnitNotInstalledError` if the platform unit file
    doesn't exist. Refusing early matters for the group-dispatch
    path: a parent's snapshot can name a child whose unit was
    destroyed (or never applied here), and we don't want the
    scheduler's fire to either traceback at the CLI or leave
    side-effects (a created state dir, a hung waiter) before the
    caller can decide to soft-fail.
    """
    if not crony.runtime.dispatch_unit_path(name, platform).exists():
        raise crony.errors.UnitNotInstalledError(
            f"unit for {name!r} is not installed on this host "
            f"(run `crony apply` to re-pin the parent's snapshot)"
        )
    if triggered_by_user and state_dir is None:
        raise ValueError(
            "triggered_by_user requires a state_dir (caller resolves "
            "via Config; this avoids a name->uuid disk scan inside "
            "the trigger path)"
        )
    if triggered_by_user and state_dir is not None:
        crony.runtime.write_user_trigger_flag(state_dir)
    succeeded = False
    try:
        crony.runtime.scheduler(platform).trigger(name)
        succeeded = True
    finally:
        if triggered_by_user and state_dir is not None and not succeeded:
            # Don't strand the bypass flag on disk if the kickstart
            # never reached the scheduler. The next legitimately
            # scheduled fire would otherwise consume it and skip
            # its wait silently.
            crony.runtime.user_trigger_flag_path(state_dir).unlink(
                missing_ok=True
            )


def trigger_unit_sync(
    full_name: str,
    *,
    state_dir: Path,
    job_timeout: float,
    trigger_timeout: float,
    triggered_by_user: bool = False,
) -> dict[str, Any]:
    """Fire `full_name` via the platform and wait for completion.

    `state_dir` is the entity's uuid-keyed state directory,
    resolved by the caller (via `Config.current` or
    `Job.state_dir_from_ref(child_ref)`). The
    path may not exist yet at call time (first
    fire); the watcher polls run.pid / last-run.json under it
    without pre-creating the dir, so a unit that fails to load
    leaves no phantom remnant behind.

    Returns the parsed `last-run.json` dict for the run we observed
    completing. Raises `TriggerStartTimeout` if no live runner and no
    fresh last-run.json appears within `trigger_timeout` seconds -- the
    "platform never started anything" detector (broken plist, stalled
    queue, unloaded unit), which also covers a launch that wrote run.pid
    then died without recording (the dead pid no longer counts as a
    live runner). Raises `JobTimeoutError` when `job_timeout` is finite
    and the wait reaches that budget without completing; the budget
    hard-bounds the whole wait. A dead or vanished pid can never spin
    this loop -- a liveness probe gates the pid-exit wait, so a corpse
    falls through to the bounded start-timeout poll instead of re-arming
    the wait. An uncapped child (`job_timeout` is `math.inf`) with a
    genuinely live runner waits as long as that runner lives.

    Mechanism: a pre-trigger timestamp T is taken, the platform
    is asked to fire the unit, and the waiter watches for the
    runner's pid (published via `<state>/<bundle>/<uuid>/run.pid`) to
    exit via kernel-level pid-exit notification. When the
    pid is gone, last-run.json is read; if its `ended_at` is at
    or after T, the run is "ours" (semantics: the next completion
    after the trigger). Otherwise we sleep briefly, re-read the
    pid file, and try again.

    Triggering an already-running unit is a no-op on both
    platforms; the waiter still attaches to the in-flight pid and
    returns its result. That's what we want -- "the next
    completion" is well-defined and serves the group-dispatch use
    case (we wanted the child's run; an in-flight run satisfies).
    """
    # Match the runner's `now_iso()` precision (whole seconds): if
    # pre_trigger captured microseconds and the runner's ended_at
    # truncated them, a sub-second run could compare ended_at <
    # pre_trigger and the loop would conclude "no fresh run yet"
    # for the entire trigger_timeout window.
    pre_trigger_dt = datetime.datetime.fromisoformat(crony.runtime.now_iso())
    pid_path = state_dir / "run.pid"
    last_run_path = state_dir / "last-run.json"

    trigger_unit(
        full_name,
        triggered_by_user=triggered_by_user,
        state_dir=state_dir if triggered_by_user else None,
    )

    started_at = time.monotonic()
    while True:
        elapsed = time.monotonic() - started_at
        # The fresh-result read precedes the budget cap so a child that
        # completed right at the boundary returns its real record rather
        # than a spurious timeout. It also covers a sub-second run whose
        # pid file came and went between iterations, still observable via
        # the file the runner wrote at exit.
        if last_run_path.exists():
            rec: dict[str, Any] | None
            try:
                parsed = json.loads(last_run_path.read_text(encoding="utf-8"))
                rec = parsed if isinstance(parsed, dict) else None
            except (OSError, json.JSONDecodeError):
                rec = None
            ended_at = rec.get("ended_at") if rec is not None else None
            if ended_at:
                try:
                    ended_dt = datetime.datetime.fromisoformat(ended_at)
                except ValueError:
                    ended_dt = None
                if (
                    rec is not None
                    and ended_dt is not None
                    and ended_dt >= pre_trigger_dt
                ):
                    return rec
        # Hard cap on the whole wait: a capped child can't hold the
        # group past its budget regardless of pid state. Bounds a
        # wedged live child and a dead pid that never yields a fresh
        # result alike, so this loop can neither block nor spin forever.
        if not math.isinf(job_timeout) and elapsed > job_timeout:
            raise crony.errors.JobTimeoutError(
                f"{full_name!r} did not complete within {job_timeout:.0f}s"
            )
        pid = crony.runtime.read_pid_file(pid_path)
        if pid is not None and _pid_alive(pid):
            # A live runner: block on kernel-level pid-exit, capped at
            # the remaining budget (None = no cap, for an uncapped
            # child). After the wait, loop back to re-read last-run.json
            # (the runner writes it just before unlinking the pid).
            pid_wait = (
                None
                if math.isinf(job_timeout)
                else max(1.0, job_timeout - elapsed)
            )
            _wait_for_pid_exit(pid, timeout=pid_wait)
            continue
        # No live runner: not started yet, or it wrote run.pid and died
        # without a fresh result (a dead pid lingers). Bound this by
        # trigger_timeout and poll with a sleep -- never re-attach to a
        # corpse and busy-loop.
        if elapsed > trigger_timeout:
            raise crony.errors.TriggerStartTimeout(
                f"trigger of {full_name!r} never produced a fresh "
                f"run within {trigger_timeout}s"
            )
        time.sleep(1.0)


def trigger_exit_code(rec: dict[str, Any]) -> int:
    """Map a last-run.json record to a process exit code.

    A run classified "ok" maps to 0 even when its recorded `exit_code`
    is non-zero -- a `success_exit_codes` match preserves the real code
    but classifies the run a success, so `crony trigger --wait` exits 0
    just like the 0 `crony run` surfaces to the scheduler.

    `exit_code` is None whenever the job didn't reach a normal exit
    (timeout, signal, gate refusal, lock contention). `... or 0`
    would mask all of those as success; instead, derive the
    code from the exit_class so a `crony trigger --wait` against
    a timed-out job exits non-zero.
    """
    if rec.get("exit_class") == "ok":
        return 0
    rc = rec.get("exit_code")
    if rc is not None:
        return int(rc)
    cls = rec.get("exit_class", "ok")
    if cls == "timeout":
        return int(crony.errors.ExitCode.TIMEOUT)
    if cls == "signal":
        sig = rec.get("signal")
        return 128 + int(sig) if sig else int(crony.errors.ExitCode.ERROR)
    if cls == "lock_busy":
        return int(crony.errors.ExitCode.LOCK_BUSY)
    if cls == "gated":
        return 0
    return 0


# Grace between the guard's SIGTERM and its escalation to SIGKILL,
# matching `_exec_with_timeout`'s own command-kill grace.
_GUARD_KILL_GRACE_SEC = 10


def do_run_guard(cap: int, argv: list[str]) -> None:
    """Hard wallclock backstop wrapping the runner. Platform schedulers
    invoke this; not user-facing.

    Runs `argv` (a `crony run <ref>` invocation) under a `cap`-second
    deadline and, if it overruns, kills its whole process group. It is
    the last resort behind the runner's own soft timeout: a runner that
    wedges before honoring its deadline (a stuck syscall, a lock it can't
    take) is killed here instead of holding a unit forever. The cap is
    `entry timeout + padding`, so on a healthy run the soft timeout fires
    first and records a clean result; the guard only acts when that
    didn't happen.

    The child runs in its own session so the kill targets only its
    process group, never the guard. SIGTERM / SIGINT / SIGHUP are
    forwarded to that group: the guard is the process the scheduler
    tracks (launchd's process group, systemd's main pid), so a scheduler
    stopping the unit signals the guard, which must pass it on to the run
    that escaped into its own session. The child's exit status propagates
    unchanged (a signal death as 128 + signum); an overrun exits TIMEOUT.
    """
    # start_new_session makes the child a session/group leader, so its
    # pgid equals its pid -- the target for group signals. Set after the
    # spawn; the forwarder reads it lazily so it is a no-op for a signal
    # arriving during the spawn itself (handlers are installed first so
    # such a signal can't fall through to the default-terminate
    # disposition and leave the guard dead with a child still starting).
    pgid: int | None = None

    def _forward(signum: int, _frame: object) -> None:
        if pgid is None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signum)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _forward)

    proc = subprocess.Popen(argv, start_new_session=True)
    pgid = proc.pid

    try:
        rc = proc.wait(timeout=cap)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=_GUARD_KILL_GRACE_SEC)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pgid, signal.SIGKILL)
            proc.wait()
        raise SystemExit(int(crony.errors.ExitCode.TIMEOUT)) from None
    raise SystemExit(rc if rc >= 0 else 128 - rc)


def do_run(ref: str, dry_run: bool, skip_gate: bool) -> None:
    """Runner shim. Platform schedulers invoke this; not user-facing.

    `ref` is the entity's `<bundle>:<uuid>` address, the form
    `apply` writes into the platform unit's argv. The runner
    locates the state dir directly via `Job.state_dir_from_ref(ref)`,
    loads the pinned snapshot, and dispatches to the per-job or
    per-group pipeline. The wrapped command's exit code is
    surfaced via SystemExit so it propagates to the platform
    scheduler; cli() converts that into the process exit.

    When a run precondition fails before the command runs -- the
    pinned snapshot can't be loaded (missing, unreadable, wrong
    schema, unknown kind), or the job/group dispatch raises a
    `PreconditionError` (e.g. a missing script) -- the runner records
    a `last-run.json` with `exit_class="canceled"` so the failure
    surfaces in `crony status` and isn't silently dropped by the
    scheduler. The "canceled" label is shared with interactive-
    job cancels: both have the same operator-facing meaning
    ("crony run never got to the user's command"). Without this
    record the scheduled fire fails, leaving the prior outcome in
    place: a snapshot-schema bump looks like ordinary
    "edited config, not yet applied" drift, and a per-run precondition
    surfaces only as a `crashed` launch with no explanation.
    """
    parsed = crony.unit.EntityRef.from_str(ref)
    if parsed is None:
        raise crony.errors.UsageError(
            f"crony run takes <bundle>:<uuid>, got {ref!r}; "
            f"this entry point is for platform-unit invocation. "
            f"Use `crony trigger <name>` to fire by name."
        )
    try:
        snap = crony.runtime.load_snapshot(parsed)
        if isinstance(snap, crony.model.Job):
            rc = _run_job(snap, dry_run=dry_run, skip_gate=skip_gate)
        else:
            rc = _run_group(snap, dry_run=dry_run)
    except crony.errors.PreconditionError as exc:
        _record_precondition_cancel(parsed, exc)
        raise
    raise SystemExit(rc)


def _record_precondition_cancel(
    ref: crony.unit.EntityRef, exc: crony.errors.PreconditionError
) -> None:
    """Write a minimal `last-run.json` recording a precondition
    failure as `canceled` so it surfaces in `crony status` -- same
    `canceled` label interactive-job cancels use. Covers any
    `PreconditionError` raised before the command runs (snapshot
    load, missing script, and other per-run preconditions).

    Skipped when the state dir doesn't exist on disk: creating it
    just to hold the error record would leave an orphaned dir
    that subsequently has to be cleaned up. In that case the
    operator discovers the issue via the platform scheduler's
    own exit-code surface and the unit-only orphan that surfaces
    in `crony status`.

    A run.log line accompanies the JSON so `crony logs` shows
    the underlying reason -- the JSON keeps the schema flat
    (just enough for `LastRun.from_raw` to surface
    `exit_class="canceled"`) rather than extending the
    `JobRunResult` / `GroupRunResult` dataclasses with a new
    error variant.
    """
    state_dir = crony.model.Job.state_dir_from_ref(ref)
    if not state_dir.is_dir():
        return
    now = crony.runtime.now_iso()
    payload: dict[str, Any] = {
        "host": crony.platform.current_host(),
        "platform": crony.platform.current_platform(),
        "started_at": now,
        "ended_at": now,
        "duration_sec": 0.0,
        "exit_class": "canceled",
        "exit_code": int(crony.errors.ExitCode.PRECONDITION),
        # The process exits with this code (do_run re-raises and cli
        # maps it), so it matches what the scheduler records for this
        # launch -- otherwise status would reconcile the cancel as a
        # `crashed` launch that left no record (RuntimeState.crashed).
        "process_exit": int(crony.errors.ExitCode.PRECONDITION),
        "reason": str(exc),
    }
    crony.runtime.write_last_run(state_dir / "last-run.json", payload)
    log_path = state_dir / crony.model.RUN_LOG_NAME
    line = f"\n=== {now} CANCELED {ref} ===\n   {exc}\n"
    with open(log_path, "ab") as f:
        f.write(line.encode("utf-8"))
