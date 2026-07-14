# This is AI generated code

"""crony's exit codes and exception hierarchy.

Every layer raises these, so they live in the lowest module -- with no
first-party dependency beyond `common.exitcodes`. Each exception carries
the `ExitCode` the CLI maps it to when it surfaces.
"""

import subprocess

from common.exitcodes import CommonExitCode, ExitCodeBase


class ExitCode(ExitCodeBase):
    SUCCESS = CommonExitCode.SUCCESS
    WARNING = CommonExitCode.WARNING
    USAGE = CommonExitCode.USAGE
    CONFIG = CommonExitCode.CONFIG
    ERROR = CommonExitCode.ERROR
    SUBPROCESS = CommonExitCode.SUBPROCESS
    TIMEOUT = CommonExitCode.TIMEOUT
    CRASHED = CommonExitCode.CRASHED
    LOCK_BUSY = 10, "run.lock held by another instance"
    PRECONDITION = 11, "run precondition failed before exec"


# Exit codes returned only by the internal `crony _run` path, never by a
# user-facing subcommand. Omitted from user documentation (the man page),
# the same way `_run` itself is.
INTERNAL_EXIT_CODES = frozenset({ExitCode.LOCK_BUSY, ExitCode.PRECONDITION})


class CronyError(RuntimeError):
    """Base exception for crony errors."""

    exit_code: ExitCode = ExitCode.ERROR


class UsageError(CronyError):
    """Bad argument or unknown subcommand."""

    exit_code = ExitCode.USAGE


class ConfigError(CronyError):
    """Unparseable or invalid config."""

    exit_code = ExitCode.CONFIG


class SubprocessError(subprocess.CalledProcessError, CronyError):
    """Subprocess (a scheduler / host command) failed."""

    exit_code = ExitCode.SUBPROCESS


class LockBusyError(CronyError):
    """`crony _run` could not acquire the per-job lock."""

    exit_code = ExitCode.LOCK_BUSY


class PreconditionError(CronyError):
    """`crony _run` precondition failed before exec."""

    exit_code = ExitCode.PRECONDITION


class JobTimeoutError(CronyError):
    """`crony _run` killed the wrapped command on timeout."""

    exit_code = ExitCode.TIMEOUT


class UnitNotInstalledError(PreconditionError):
    """A dispatcher was asked to fire a unit that isn't installed
    on this host.

    Surfaces from `trigger_unit` when the platform unit file the
    dispatcher would reach for doesn't exist. The typical cause
    is a parent group whose snapshot still references a child
    that was destroyed (or never applied here): `crony apply`
    re-pins the parent's snapshot to drop the stale reference.
    Caught and soft-failed by `_run_group` so a single missing
    child doesn't take down a whole group run. Inherits
    `PreconditionError`'s `PRECONDITION` exit code so the
    "precondition failed before exec" framing is consistent at
    the exit-code level even though the specialization carries
    the more specific message.
    """


class TriggerStartTimeout(JobTimeoutError):
    """The platform never produced a runner within
    `trigger_timeout_sec` after we asked it to fire the unit.
    Suggests a broken plist / unit, a stalled scheduler queue, or
    the unit not being loaded (run `crony apply` first).

    Raised only when no runner was ever observed. A runner that
    started and then died without recording raises `RunnerCrashed`
    instead -- the two look alike from the outside (no result ever
    lands) but mean opposite things: nothing ran vs. the job ran and
    crashed.

    Subclass of `JobTimeoutError` so it shares the TIMEOUT exit
    code; the specialization conveys 'the trigger never started'
    rather than 'the runner exceeded its timeout'.
    """


class RunnerCrashed(CronyError):
    """A runner started but exited without recording a result.

    The live-dispatch detection of the same event `crony status` shows
    as `crashed`: the waiter saw a live, lock-holding runner for the
    unit and then watched its pid go away with no fresh `last-run.json`
    behind it -- the signature of a job killed hard enough that it never
    wrote its own record (OOM kill, SIGKILL, the unit being unloaded
    mid-run). `crony status` reaches that same `crashed` verdict
    independently, by reconciling the stale pid.

    Distinct from `TriggerStartTimeout` (nothing ever ran) because the
    job DID run: a parent group that gets this has done its job of
    running the child, so the crash is the child's outcome, not the
    group's fault.

    Carries the general `ERROR` code rather than `CRASHED`: `CRASHED` is
    crony's exit code for crony ITSELF dying on an unhandled exception,
    so reusing it here would leave a `crony trigger --wait` caller unable
    to tell a crashed job from a crashed crony.
    """
