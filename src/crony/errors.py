# This is AI generated code

"""crony's exit codes and exception hierarchy.

Every layer raises these, so they live in the lowest module -- with no
first-party dependency beyond `common.exitcodes`. Each exception carries
the `ExitCode` the CLI maps it to when it surfaces.
"""

from __future__ import annotations

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
    LOCK_BUSY = 10, "run.lock held by another instance"
    PRECONDITION = 11, "run precondition failed before exec"


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
    """`crony run` could not acquire the per-job lock."""

    exit_code = ExitCode.LOCK_BUSY


class PreconditionError(CronyError):
    """`crony run` precondition failed before exec."""

    exit_code = ExitCode.PRECONDITION


class JobTimeoutError(CronyError):
    """`crony run` killed the wrapped command on timeout."""

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

    Subclass of `JobTimeoutError` so it shares the TIMEOUT exit
    code; the specialization conveys 'the trigger never started'
    rather than 'the runner exceeded its timeout'.
    """
