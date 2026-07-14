# This is AI generated code

"""crony's run pipeline.

The `crony _run <bundle>:<uuid>` entry the platform scheduler invokes:
load the pinned snapshot and dispatch to the per-job or per-group
pipeline. The job pipeline holds a per-entry lock, runs the optional
gate, execs the command under a wallclock cap (with the interactive
wait / approval dialog for interactive jobs), writes the last-run
record, and hands a failed run to the notification layer. The group
pipeline fires each child through its own platform unit and waits
synchronously for it to exit via a kernel pid-exit notification. It
records what each child did, but reports only its own outcome: whether
it got every child run.
"""

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
from enum import StrEnum
from pathlib import Path
from typing import (
    IO,
    Any,
    Literal,
    NamedTuple,
    get_args,
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


# The only two outcomes a group can charge to ITSELF: `fail` (a child it
# could not fire) and `timeout` (one it ran out of time on before ever
# seeing it run). Every other ExitClass describes what a child did with
# its turn, which belongs on the child's row, not the group's -- spelling
# the fault set as a Literal makes recording one of those against the
# group a type error at the site that does it.
type _GroupFault = Literal[
    crony.model.ExitClass.FAIL, crony.model.ExitClass.TIMEOUT
]

# Severity of the group's own faults. `timeout` outranks `fail` so a
# group that blew its cumulative budget reads TIMEOUT rather than being
# masked by an earlier missing-unit fail.
_GROUP_FAULT_PRECEDENCE: dict[_GroupFault, int] = {
    crony.model.ExitClass.FAIL: 1,
    crony.model.ExitClass.TIMEOUT: 2,
}

# An unranked fault would raise KeyError from `_group_exit_class` -- at
# the END of a group run, escaping before the group's record is written
# and losing it entirely. Fail at import instead.
assert set(get_args(_GroupFault.__value__)) == set(_GROUP_FAULT_PRECEDENCE), (
    "every group fault needs a precedence rank"
)

# Dispatching a child can fail outright in two ways -- the unit isn't
# installed on this host, or the scheduler rejects the fire -- and both
# mean the same thing to the group: that child did not run, which is the
# group's own failure. Each carries the exit code its own row records, so
# they share one arm. Neither may escape `_run_group`: the group's record
# is written on the way out, and an exception through the dispatch loop
# would take it (and every child still to be fired) with it.
_CHILD_NOT_RUN = (
    crony.errors.UnitNotInstalledError,
    crony.errors.SubprocessError,
)


def _group_exit_class(
    faults: list[_GroupFault],
) -> crony.model.ExitClass:
    """The group's own outcome: `ok` when it got every child running,
    else the worst fault the group itself hit.

    A group's job is to run its children, not to succeed at their work.
    A child that fails, is signaled, times out against its own cap, or
    crashes is the child's problem: that shows on the child's own row
    and is never a fault of the group, so it never reaches here. The
    group fails only for a child it never got running -- one it could
    not fire (unit or snapshot missing here, or the scheduler refusing),
    and one it ran out of time on before ever seeing it run (its own
    cumulative budget spent, or the platform never producing a runner).

    Once a child is running the group has done its job, so what becomes
    of it -- including the group abandoning the wait when its budget
    runs out -- is not a fault. A child abandoned that way keeps running
    under its own unit and reports on its own row.
    """
    if not faults:
        return crony.model.ExitClass.OK
    return max(faults, key=_GROUP_FAULT_PRECEDENCE.__getitem__)


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


class _InteractiveChoice(StrEnum):
    """An interactive job's resolved decision: run the command now,
    delay and re-prompt later, or cancel this fire. Logged into the
    run.log, so a StrEnum that reads as its plain value."""

    RUN = "run"
    DELAY = "delay"
    CANCEL = "cancel"


def _show_interactive_dialog(job_name: str, message: str) -> _InteractiveChoice:
    """Pop the three-button approval dialog and map the click to a
    decision.

    Delegates the dialog to the HostPlatform backend: Run Job -> RUN,
    Delay Job -> DELAY. Clicking the cancel button, dismissing the
    dialog, or any backend failure yields no choice and maps to CANCEL
    -- silently running without user confirmation would defeat the
    whole point of the flag.
    """
    clicked = crony.runtime.host().show_dialog(
        f"crony: {job_name}", message, _INTERACTIVE_BUTTONS
    )
    if clicked == "Run Job":
        return _InteractiveChoice.RUN
    if clicked == "Delay Job":
        return _InteractiveChoice.DELAY
    return _InteractiveChoice.CANCEL


def _interactive_wait_and_prompt(
    snap: crony.model.Job, log_file: IO[bytes]
) -> _InteractiveChoice:
    """Run the wait/prompt/delay loop for an interactive job.

    Returns RUN or CANCEL. Logs each phase transition into the run.log
    so a post-mortem reader can see how long the user took, whether
    they delayed, etc.

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
            return _InteractiveChoice.RUN
        log_file.write(b"interactive: prompting user\n")
        choice = _show_interactive_dialog(
            str(snap.entity_name),
            f"crony wants to run '{snap.entity_name}'. Now?",
        )
        log_file.write(f"interactive: user chose {choice}\n".encode())
        if choice in (_InteractiveChoice.RUN, _InteractiveChoice.CANCEL):
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
            return _InteractiveChoice.RUN


def _run_job(snap: crony.model.Job) -> int:
    """Run a single job from its applied snapshot.

    Returns an exit code suitable for the platform scheduler: the
    wrapped command's exit code on completion (0 on success or any
    nonzero code from the command), ExitCode.LOCK_BUSY on lock
    contention, ExitCode.TIMEOUT on wallclock cap, or 0 for a gated
    skip. Precondition failures (missing script) are signalled by
    raising PreconditionError -- cli() maps that to
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
            # Publish our pid for waiters (parent groups, `crony
            # trigger --wait`) to watch for exit, and leave it in place
            # on exit: run.pid persists across runs as the record of the
            # last launch, read afterward by the dispatch waiter and when
            # status is built.
            pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

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

                gate = crony.model.GateResult.NONE
                gate_cmd = _gate_argv(snap)
                if gate_cmd is not None:
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
                    gate = (
                        crony.model.GateResult.PASSED
                        if gate_rc == 0
                        else crony.model.GateResult.FAILED
                    )

                    if gate == crony.model.GateResult.FAILED:
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
                        if choice == _InteractiveChoice.CANCEL:
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
    except crony.errors.LockBusyError:
        with open(log_path, "ab", buffering=0) as f:
            f.write(
                f"[{crony.runtime.now_iso()}] LOCK_BUSY: skipping "
                f"(already running)\n".encode()
            )
        return int(crony.errors.ExitCode.LOCK_BUSY)


def _child_full_name_from_uuid(child_ref: crony.unit.EntityRef) -> str | None:
    """Resolve a child's ref (one of the parent node's `children`,
    each the parent's bundle paired with a child uuid) to its full
    namespaced name by reading the child's own snapshot. The runner
    dispatches each child via the platform unit label, which is keyed
    by full name; keying the parent's children by uuid keeps its
    snapshot stable across child renames, at the cost of one snapshot
    read per child at dispatch time. Returns None when the child
    snapshot is missing or unreadable.
    """
    snap_p = crony.model.Job.state_dir_from_ref(child_ref) / "snapshot.json"
    if not snap_p.is_file():
        return None
    try:
        raw = json.loads(snap_p.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
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


def _child_is_disabled(child_ref: crony.unit.EntityRef) -> bool:
    """True iff the child's pinned snapshot is operator-disabled
    (`crony disable`). The parent group skips a disabled child instead
    of dispatching it. A missing snapshot returns False so the absent
    child still flows to the synthetic-FAIL / UnitNotInstalledError path
    that surfaces the missing unit.
    """
    try:
        cs = crony.runtime.load_snapshot(child_ref)
    except crony.errors.PreconditionError:
        return False
    return cs.unit_disabled


_DEFAULT_CHILD_TIMEOUT_FALLBACK: int = 1800


def _run_group(snap: crony.model.JobGroup) -> int:
    """Run a job-group from its applied snapshot.

    Fires each child through the platform scheduler in order,
    waiting for completion via kernel-level pid-exit notification
    (`trigger_unit_sync`). The group's `run.log` becomes a
    sequencer trace (fired/finished lines per child); child
    stdout/stderr stays in each child's own log. The group never
    sends notifications; each child's own runner handles notify
    per the cascade.

    The cumulative deadline (`snap.timeout`) was pinned at apply time,
    and bounds the wait on each child in turn. Each child is also waited
    on against the timeout pinned in its own snapshot, so the group's cap
    and the child's own enforcement agree. A group never applies a
    run-gate of its own -- each child's runner reads its own snapshot and
    gates itself.

    The group's `exit_class` is its OWN outcome, not a summary of its
    children's: it succeeds when it ran every child, and fails only for a
    child that did not get to run at all. What a child then did with its
    turn belongs on that child's row. See `_group_exit_class`.
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
            # Left in place on exit; see `_run_job` for the run.pid
            # lifecycle.
            pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

            log_file = open(log_path, "ab", buffering=0)
            children: list[crony.model.GroupChildResult] = []
            # The group's OWN faults -- the children it could not run.
            # A child that ran and failed is the child's problem and is
            # deliberately not recorded here (see `_group_exit_class`).
            faults: list[_GroupFault] = []
            try:
                # Resolve each child ref to its current full name via
                # the child's own snapshot. A None resolution means the
                # child has no state dir on this host -- log it, record a
                # fail row, and count it as a fault of the group (which
                # could not run that child).
                resolved_children: list[
                    tuple[crony.unit.EntityRef, str | None]
                ] = [
                    (child_ref, _child_full_name_from_uuid(child_ref))
                    for child_ref in snap.children
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
                    if _child_is_disabled(child_ref):
                        # An operator-disabled child is intentionally not
                        # run: skipping it is the group doing as it was
                        # told, not a failure to run it, so record it
                        # `gated` and move on without faulting the group.
                        log_file.write(
                            f"-> {child_full_name}: skipped "
                            f"(disabled)\n".encode()
                        )
                        children.append(
                            crony.model.GroupChildResult(
                                name=child_full_name,
                                exit_class=crony.model.ExitClass.GATED,
                                exit_code=0,
                            )
                        )
                        continue
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
                        except _CHILD_NOT_RUN as e:
                            # The group could not run this child at all.
                            log_file.write(f"   {e}\n".encode())
                            faults.append(crony.model.ExitClass.FAIL)
                            children.append(
                                crony.model.GroupChildResult(
                                    name=child_full_name,
                                    exit_class=crony.model.ExitClass.FAIL,
                                    exit_code=int(e.exit_code),
                                )
                            )
                        continue

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        # The group's own budget ran out before it got to
                        # this child, so the child never ran: the group's
                        # fault, not the child's.
                        log_file.write(
                            (
                                f"-> {child_full_name}: TIMEOUT "
                                f"(group budget exhausted)\n"
                            ).encode()
                        )
                        faults.append(crony.model.ExitClass.TIMEOUT)
                        children.append(
                            crony.model.GroupChildResult(
                                name=child_full_name,
                                exit_class=crony.model.ExitClass.TIMEOUT,
                                exit_code=int(crony.errors.ExitCode.TIMEOUT),
                            )
                        )
                        continue

                    child_timeout = _child_timeout_from_snapshot(child_ref)
                    # Both bounds are logged because they bound different
                    # things: neither alone says how long the group will
                    # sit here.
                    cap_label = (
                        "none"
                        if math.isinf(child_timeout)
                        else f"{child_timeout:g}s"
                    )
                    budget_label = (
                        "none" if math.isinf(remaining) else f"{remaining:.0f}s"
                    )
                    log_file.write(
                        (
                            f"-> {child_full_name} (timeout={cap_label}, "
                            f"budget-left={budget_label})\n"
                        ).encode()
                    )
                    try:
                        # The child's own cap and the group's remaining
                        # budget go to the waiter separately, not as the
                        # smaller of the two: it keeps them apart, and the
                        # exception it raises says which verdict to record.
                        rec = trigger_unit_sync(
                            child_full_name,
                            state_dir=child_sd,
                            job_timeout=child_timeout,
                            trigger_timeout=trigger_timeout,
                            wait_budget=remaining,
                        )
                    except crony.errors.RunnerCrashed as e:
                        # The child ran and was killed before it could
                        # record a result. The group DID run it, so the
                        # crash is the child's outcome -- its own row says
                        # `crashed`, reached independently by reconciling
                        # its stale pid -- and never a fault of the group.
                        log_file.write(f"   {e}\n".encode())
                        children.append(
                            crony.model.GroupChildResult(
                                name=child_full_name,
                                exit_class=crony.model.ExitClass.FAIL,
                                exit_code=int(e.exit_code),
                            )
                        )
                        continue
                    except crony.errors.TriggerStartTimeout as e:
                        # No runner ever appeared, so the child never ran:
                        # the group failed to run it. Caught before the
                        # JobTimeoutError arm below -- it is a subclass.
                        log_file.write(f"   {e}\n".encode())
                        faults.append(crony.model.ExitClass.TIMEOUT)
                        children.append(
                            crony.model.GroupChildResult(
                                name=child_full_name,
                                exit_class=crony.model.ExitClass.TIMEOUT,
                                exit_code=int(crony.errors.ExitCode.TIMEOUT),
                            )
                        )
                        continue
                    except crony.errors.JobTimeoutError as e:
                        # Raised only once the child was seen running, so
                        # the group got it running: no fault of the group,
                        # whichever allowance then ran out. That covers the
                        # group giving up on a still-live child because its
                        # own budget expired mid-wait -- deliberately, since
                        # the child goes on running under its own unit and
                        # its row reports what it does, and no later child
                        # was starved of its turn (a budget that starves one
                        # faults the group on the `remaining <= 0` path
                        # above, before dispatch).
                        log_file.write(f"   {e}\n".encode())
                        children.append(
                            crony.model.GroupChildResult(
                                name=child_full_name,
                                exit_class=crony.model.ExitClass.TIMEOUT,
                                exit_code=int(crony.errors.ExitCode.TIMEOUT),
                            )
                        )
                        continue
                    except _CHILD_NOT_RUN as e:
                        # The group could not run this child at all.
                        log_file.write(f"   {e}\n".encode())
                        faults.append(crony.model.ExitClass.FAIL)
                        children.append(
                            crony.model.GroupChildResult(
                                name=child_full_name,
                                exit_class=crony.model.ExitClass.FAIL,
                                exit_code=int(e.exit_code),
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
                        # Unresolvable child: the group could not run it.
                        log_file.write(
                            (
                                f"-> ref {child_ref}: "
                                f"no snapshot on this host (FAIL)\n"
                            ).encode()
                        )
                        faults.append(crony.model.ExitClass.FAIL)
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
                    exit_class=_group_exit_class(faults),
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
    entity's state dir, consumed by `crony _run` on startup to
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
    wait_budget: float = math.inf,
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

    Waiting has two sequential phases, each with its own allowance, so
    that a job which is merely slow to START is never mistaken for one
    that never started. Until a live runner is seen, the platform has
    `trigger_timeout` to produce one; the job's own `job_timeout` does
    not apply, since a cap on how long the job may RUN says nothing about
    how long the scheduler may take to launch it. Once a runner is seen,
    `job_timeout` caps the run. `wait_budget` hard-bounds both phases
    together for a caller that cannot wait indefinitely (a group passes
    its remaining cumulative budget); it defaults to no bound.

    Returns the parsed `last-run.json` dict for the run we observed
    completing. A wait that fails raises by what became of the runner --
    which is what says whether the job ran at all -- never by which
    allowance ran out:

    - `TriggerStartTimeout`: the start allowance elapsed with no runner
      ever seen and no fresh last-run.json. The "platform never started
      anything" detector (broken plist, stalled queue, unloaded unit).
      Raised whether it was the platform's own `trigger_timeout` that
      elapsed or a shorter `wait_budget`: either way no runner was ever
      seen, and that -- not the clock that stopped us -- is what says the
      job never ran.
    - `RunnerCrashed`: a live runner was seen and its pid then went away
      leaving no record -- the job ran and was killed before it could
      write one. Raised as soon as the lock is seen free, since the
      runner writes its record before releasing it: no record by then
      means there will never be one.
    - `JobTimeoutError`: the job ran and outlived an allowance -- its own
      `job_timeout`, or the caller's `wait_budget` (which gives up on a
      job that is still going, rather than declaring anything about it).

    The three are reported apart because a parent group must tell its own
    fault (it never got the child running) from its child's (the child
    ran, then crashed or overran). A dead, vanished, or leftover pid can
    never spin this loop -- run.pid persists across runs, so the waiter
    attaches only while run.lock is held (a run in flight; see
    Mechanism); a leftover with no held lock falls through to the bounded
    start-timeout poll instead of re-arming the wait. An uncapped job
    (`job_timeout` is `math.inf`) with a genuinely live runner waits as
    long as that runner lives, unless `wait_budget` bounds it.

    Mechanism: a pre-trigger timestamp T is taken, the platform is asked
    to fire the unit, and the waiter watches for the runner's pid
    (published via `<state>/<bundle>/<uuid>/run.pid`) to exit via
    kernel-level pid-exit notification. run.pid persists across runs, so
    the waiter attaches to its pid only while run.lock is held -- a run
    genuinely in flight, the kernel having released the lock on any
    earlier runner's exit -- and treats a leftover run.pid with no held
    lock as no live runner. When the pid is gone, last-run.json is read;
    if its `ended_at` is at or after T, the run is "ours" (semantics: the
    next completion after the trigger). Otherwise we sleep briefly,
    re-read the pid file, and try again.

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

    def _read_record() -> dict[str, Any] | None:
        """The last-run record on disk, whoever's run it is."""
        if not last_run_path.exists():
            return None
        try:
            parsed = json.loads(last_run_path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return None
        rec: dict[str, Any] | None = (
            parsed if isinstance(parsed, dict) else None
        )
        return rec

    def _fresh_record() -> dict[str, Any] | None:
        """This dispatch's completed record, or None if none has landed.

        run.pid can come and go between polls, but the file the runner
        writes at exit persists, so even a sub-second run is observable
        here. `ended_at` must be at or after the pre-trigger timestamp:
        an older record belongs to some earlier run, not to ours.
        """
        rec = _read_record()
        if rec is None:
            return None
        ended_at = rec.get("ended_at")
        if not ended_at:
            return None
        try:
            ended_dt = datetime.datetime.fromisoformat(ended_at)
        except ValueError:
            return None
        return rec if ended_dt >= pre_trigger_dt else None

    def _record_written_by(runner_pid: int) -> dict[str, Any] | None:
        """The record `runner_pid` wrote, or None if it wrote none.

        A runner records the pid it launched with, so this identifies its
        result regardless of when the run ended. That is what `ended_at`
        cannot do for a run that was already in flight when we fired: the
        platform coalesces the fire into that run, we attach to it and
        wait it out, and it is the run we were promised -- but it may well
        have ended before we ever asked, and a record is written after its
        `ended_at` is stamped (a slow notify dispatch sits between them).
        """
        rec = _read_record()
        if rec is None or rec.get("pid") != runner_pid:
            return None
        return rec

    def _live_runner_pid() -> int | None:
        """The pid of a live runner holding this job's lock, else None.

        run.pid persists across runs, so on its own it can name a stale
        (dead or recycled) pid from an earlier one. run.lock is held only
        while a run is genuinely in flight -- the kernel releases the
        flock when the runner exits -- so it, not run.pid's presence,
        tells a live runner from a leftover. A leftover must never be
        attached to: waiting on a corpse's pid-exit returns instantly and
        busy-spins the loop.
        """
        pid = crony.runtime.read_pid_file(pid_path)
        if (
            pid is not None
            and crony.runtime.run_in_progress(state_dir)
            and _pid_alive(pid)
        ):
            return pid
        return None

    # Whether a live, lock-holding runner was ever observed for this
    # dispatch. It is what separates "nothing ever started" from "the job
    # ran and was killed before recording" when no result lands.
    saw_runner: int | None = None
    # When the job was first seen running, which is when its own cap
    # starts to count. Charging its start latency against a cap on how
    # long it may RUN would time out a job that had run for no time at
    # all -- the same conflation the start allowance below avoids.
    run_started: float | None = None
    # The platform's allowance to produce a runner, bounded by what the
    # caller can spare: we cannot hand it 15s of patience we do not have.
    start_allowance = min(trigger_timeout, wait_budget)
    while True:
        now = time.monotonic()
        elapsed = now - started_at
        # Observe, then judge on what THIS pass saw. A result that landed
        # only now is still returned and a runner visible only now still
        # counts as seen -- judging on a staler look would report a job
        # that finished (or started) right on an allowance as one that
        # never did.
        rec = _fresh_record()
        if rec is not None:
            return rec
        pid = _live_runner_pid()
        if pid is not None:
            if saw_runner is None:
                run_started = now
            saw_runner = pid
        elif saw_runner is not None:
            # The runner we were watching is gone. It writes its record
            # and only then releases the lock, so having just seen the
            # lock free, a run that completed at all has its record on
            # disk by now: one more read settles "finished" against
            # "killed before recording" with no window between them, and
            # no reason to keep polling either way. That read accepts the
            # record by the pid that wrote it, not by when it ended: a run
            # already in flight when we fired is the run we attached to
            # and waited out, so its result is ours to return even though
            # it ended before we asked for it.
            rec = _fresh_record() or _record_written_by(saw_runner)
            if rec is not None:
                return rec
            raise crony.errors.RunnerCrashed(
                f"runner for {full_name!r} exited without recording "
                f"a result (killed before it could write one)"
            )
        if pid is not None:
            assert run_started is not None
            # A live runner, running against two allowances that start at
            # different moments: its own cap from when IT started, the
            # caller's budget from when we asked for it. Whichever is
            # exhausted first ends the wait, and checking them before
            # arming the pid-exit wait keeps a wedged job from holding the
            # loop past either.
            run_left = job_timeout - (now - run_started)
            budget_left = wait_budget - elapsed
            if run_left <= 0:
                raise crony.errors.JobTimeoutError(
                    f"{full_name!r} did not complete within {job_timeout:g}s"
                )
            if budget_left <= 0:
                raise crony.errors.JobTimeoutError(
                    f"{full_name!r} did not complete within {wait_budget:g}s"
                )
            left = min(run_left, budget_left)
            pid_wait = None if math.isinf(left) else max(1.0, left)
            _wait_for_pid_exit(pid, timeout=pid_wait)
            continue
        # No runner has ever appeared for this dispatch: it is starting,
        # or run.pid is a stale leftover (a dead or pre-trigger pid). When
        # the allowance for that runs out, nothing ran -- which is the
        # verdict whether the platform used up its own allowance or the
        # caller could not spare that much. Either way we never saw a
        # runner, and that, not the clock that stopped us, is what says
        # the job never started.
        if elapsed > start_allowance:
            raise crony.errors.TriggerStartTimeout(
                f"trigger of {full_name!r} never produced a fresh "
                f"run within {start_allowance:g}s"
            )
        time.sleep(1.0)


def trigger_exit_code(rec: dict[str, Any]) -> int:
    """Map a last-run.json record to a process exit code.

    A run classified "ok" maps to 0 even when its recorded `exit_code`
    is non-zero -- a `success_exit_codes` match preserves the real code
    but classifies the run a success, so `crony trigger --wait` exits 0
    just like the 0 `crony _run` surfaces to the scheduler.

    A record without an `exit_code` is one of something that never
    reached an exit of its own to report: a job killed on its timeout or
    by a signal, or any group record at all (a group runs no process --
    its outcome lives entirely in `exit_class`). `... or 0` would mask
    every one of those as success, so the code is derived from the
    exit_class instead, and `crony trigger --wait` exits non-zero against
    a job that timed out or a group that could not run a child. Any other
    class reaching this fallback (a corrupt or unrecognized record) is
    not a failure we can name, so it maps to 0.
    """
    exit_class = crony.model.ExitClass.parse(rec.get("exit_class"))
    if exit_class is crony.model.ExitClass.OK:
        return 0
    rc = rec.get("exit_code")
    if rc is not None:
        return int(rc)
    if exit_class is crony.model.ExitClass.TIMEOUT:
        return int(crony.errors.ExitCode.TIMEOUT)
    if exit_class is crony.model.ExitClass.SIGNAL:
        sig = rec.get("signal")
        return 128 + int(sig) if sig else int(crony.errors.ExitCode.ERROR)
    if exit_class is crony.model.ExitClass.FAIL:
        # A record classified `fail` with no `exit_code` of its own: a
        # group that could not run one of its children (a group carries
        # no process exit, its outcome lives in exit_class). Without this
        # a `crony trigger --wait <group>` against a group that failed to
        # dispatch would exit 0 and read as success.
        return int(crony.errors.ExitCode.ERROR)
    return 0


# Grace between the guard's SIGTERM and its escalation to SIGKILL,
# matching `_exec_with_timeout`'s own command-kill grace.
_GUARD_KILL_GRACE_SEC = 10


def do_run_guard(cap: int, argv: list[str]) -> None:
    """Hard wallclock backstop wrapping the runner. Platform schedulers
    invoke this; not user-facing.

    Runs `argv` (a `crony _run <ref>` invocation) under a `cap`-second
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


def do_run(ref: str) -> None:
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
    ("crony _run never got to the user's command"). Without this
    record the scheduled fire fails, leaving the prior outcome in
    place: a snapshot-schema bump looks like ordinary
    "edited config, not yet applied" drift, and a per-run precondition
    surfaces only as a `crashed` launch with no explanation.
    """
    parsed = crony.unit.EntityRef.from_str(ref)
    if parsed is None:
        raise crony.errors.UsageError(
            f"crony _run takes <bundle>:<uuid>, got {ref!r}; "
            f"this entry point is for platform-unit invocation. "
            f"Use `crony trigger <name>` to fire by name."
        )
    try:
        snap = crony.runtime.load_snapshot(parsed)
        if isinstance(snap, crony.model.Job):
            rc = _run_job(snap)
        else:
            rc = _run_group(snap)
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
        # launch -- a mismatch would read as a launch that recorded no
        # result (a crash).
        "process_exit": int(crony.errors.ExitCode.PRECONDITION),
        "reason": str(exc),
    }
    crony.runtime.write_last_run(state_dir / "last-run.json", payload)
    log_path = state_dir / crony.model.RUN_LOG_NAME
    line = f"\n=== {now} CANCELED {ref} ===\n   {exc}\n"
    with open(log_path, "ab") as f:
        f.write(line.encode("utf-8"))


def do_jitter(ref: str, name: str) -> None:
    """Platform start-time jitter offset companion. On platforms that
    don't have a native jitter offset implementation for interval-based
    services (e.g. launchd), this subcommand can be invoked once via an
    additional service unit to implement a jitter offset for the primary
    service by triggering it before its scheduled interval time. Not
    user-facing.

    `ref` is the service's `<bundle>:<uuid>` (it locates the state dir);
    `name` addresses the service and companion units through the platform
    scheduler, so this common layer runs no scheduler-specific commands.

    The service's own `run.lock` decides whether to act, and serializes
    the trigger and its log line against a concurrent run:
    - HELD -> a run is already in flight -> nothing to phase, so the
      companion just unloads itself.
    - FREE -> no run this epoch -> WHILE HOLDING the lock it triggers the
      service through the scheduler and appends a line to the service's
      run.log recording whether the trigger succeeded (so a failing
      trigger is visible in the log), then releases. On success it unloads
      itself -- its last act, since on launchd the unload boots out its
      own label, which is synchronous and kills this process; on a failed
      trigger it re-raises (so the failure surfaces a non-zero exit the
      scheduler records) and stays loaded to retry at the next offset.
    """
    parsed = crony.unit.EntityRef.from_str(ref)
    if parsed is None:
        raise crony.errors.UsageError(
            f"crony _jitter takes <bundle>:<uuid>, got {ref!r}; this "
            f"entry point is for the platform jitter companion."
        )
    sched = crony.runtime.scheduler()
    state_dir = crony.model.Job.state_dir_from_ref(parsed)
    # A companion leaked past its service's removal (state dir gone) has
    # nothing to phase; unload it rather than crash in acquire_lock's
    # open() of a lock under a missing directory.
    if not state_dir.is_dir():
        sched.deactivate_jitter(name)
        return
    try:
        with crony.runtime.acquire_lock(state_dir / "run.lock"):
            # Trigger the service while holding the lock, then log the
            # outcome under the same lock (so the line can't splice into a
            # concurrent run's output, and so a failing trigger is
            # recorded). The trigger is async -- it starts the service,
            # which acquires this lock only after we release it here.
            try:
                sched.trigger(name)
            except crony.errors.SubprocessError:
                # Record the failure, then re-raise: surface a non-zero
                # exit and stay loaded to retry next offset (no unload).
                _append_jitter_log(state_dir, name, succeeded=False)
                raise
            _append_jitter_log(state_dir, name, succeeded=True)
    except crony.errors.LockBusyError:
        # A run is already in flight; it phases the cadence itself.
        sched.deactivate_jitter(name)
        return
    # Triggered successfully; unload so it does not fire again this epoch.
    sched.deactivate_jitter(name)


def _append_jitter_log(state_dir: Path, name: str, *, succeeded: bool) -> None:
    """Append the jitter companion's one-line trigger marker to the
    service's run.log, recording whether the trigger succeeded. Called
    only while run.lock is held, so it cannot interleave with a concurrent
    run's output block."""
    now = crony.runtime.now_iso()
    outcome = "triggered" if succeeded else "trigger FAILED"
    line = f"\n=== {now} JITTER {name} {outcome} ===\n"
    with open(state_dir / crony.model.RUN_LOG_NAME, "ab") as f:
        f.write(line.encode("utf-8"))
