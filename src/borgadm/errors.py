"""Exit statuses and exceptions shared by borgadm modules."""

import subprocess

from common.exitcodes import CommonExitCode, ExitCodeBase


class ExitCode(ExitCodeBase):
    SUCCESS = CommonExitCode.SUCCESS
    WARNING = CommonExitCode.WARNING
    USAGE = CommonExitCode.USAGE
    CONFIG = CommonExitCode.CONFIG
    ERROR = CommonExitCode.ERROR
    SUBPROCESS = CommonExitCode.SUBPROCESS
    CRASHED = CommonExitCode.CRASHED
    CHECK_NO_BACKUPS = 10, "Check failed: no full backups"
    CHECK_AGE = 11, "Check failed: backup too old"
    CHECK_REPO = 12, "Check failed: repo metadata"
    CHECK_ARCHIVES = 13, "Check failed: archive metadata"
    CHECK_PRUNE = 14, "Check failed: unpruned archives"
    CHECK_FULL = 15, "Check failed: full repo + archives"


class BorgadmError(RuntimeError):
    """Base exception for borgadm errors."""

    exit_code: ExitCode = ExitCode.ERROR


class ConfigError(BorgadmError):
    """Config file or permissions error."""

    exit_code = ExitCode.CONFIG


class SubprocessError(subprocess.CalledProcessError, BorgadmError):
    """Subprocess command failed."""

    exit_code = ExitCode.SUBPROCESS


class CheckNoBackupsError(BorgadmError):
    """No full backups found."""

    exit_code = ExitCode.CHECK_NO_BACKUPS


class CheckAgeError(BorgadmError):
    """Backup too old."""

    exit_code = ExitCode.CHECK_AGE


class CheckRepoError(BorgadmError):
    """Repo metadata check failed."""

    exit_code = ExitCode.CHECK_REPO


class CheckArchivesError(BorgadmError):
    """Archive metadata check failed."""

    exit_code = ExitCode.CHECK_ARCHIVES


class CheckFullError(BorgadmError):
    """Full repo + archives check failed."""

    exit_code = ExitCode.CHECK_FULL


class CheckPruneError(BorgadmError):
    """Unpruned archives found."""

    exit_code = ExitCode.CHECK_PRUNE
