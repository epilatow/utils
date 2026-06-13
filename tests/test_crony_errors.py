#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for crony.errors: the ExitCode enum and the exception
hierarchy every crony layer raises."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conftest import ExceptionHierarchyBase  # noqa: E402

from crony.errors import (  # noqa: E402
    ConfigError,
    CronyError,
    ExitCode,
    JobTimeoutError,
    LockBusyError,
    PreconditionError,
    SubprocessError,
    TriggerStartTimeout,
    UnitNotInstalledError,
    UsageError,
)

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "src" / "crony" / "errors.py"


class TestExceptionHierarchy(ExceptionHierarchyBase):
    """Every non-excluded ExitCode has a matching exception, codes are
    unique, and the common codes match the canonical subset."""

    BASE_ERROR = CronyError
    EXIT_CODE = ExitCode
    EXCLUDED_CODES = {
        ExitCode.SUCCESS,
        ExitCode.WARNING,
        ExitCode.CRASHED,
    }


class TestExceptionRelationships:
    """The specialization relationships and the exit code each error
    surfaces with."""

    def test_each_error_is_a_crony_error(self) -> None:
        for exc in (
            UsageError,
            ConfigError,
            SubprocessError,
            LockBusyError,
            PreconditionError,
            JobTimeoutError,
            UnitNotInstalledError,
            TriggerStartTimeout,
        ):
            assert issubclass(exc, CronyError)

    def test_specializations(self) -> None:
        # The specializations inherit a parent's exit code rather than
        # declaring their own.
        assert issubclass(UnitNotInstalledError, PreconditionError)
        assert "exit_code" not in UnitNotInstalledError.__dict__
        assert UnitNotInstalledError.exit_code is ExitCode.PRECONDITION
        assert issubclass(TriggerStartTimeout, JobTimeoutError)
        assert "exit_code" not in TriggerStartTimeout.__dict__
        assert TriggerStartTimeout.exit_code is ExitCode.TIMEOUT

    def test_subprocess_error_is_also_called_process_error(self) -> None:
        # SubprocessError doubles as a subprocess.CalledProcessError so
        # an `except subprocess.CalledProcessError` site still catches it.
        assert issubclass(SubprocessError, subprocess.CalledProcessError)
        assert issubclass(SubprocessError, CronyError)

    def test_exit_code_mapping(self) -> None:
        assert CronyError.exit_code is ExitCode.ERROR
        assert UsageError.exit_code is ExitCode.USAGE
        assert ConfigError.exit_code is ExitCode.CONFIG
        assert SubprocessError.exit_code is ExitCode.SUBPROCESS
        assert LockBusyError.exit_code is ExitCode.LOCK_BUSY
        assert PreconditionError.exit_code is ExitCode.PRECONDITION
        assert JobTimeoutError.exit_code is ExitCode.TIMEOUT


class TestExitCodeValues:
    """The crony-specific numeric codes (the common ones are pinned by
    TestExceptionHierarchy against the canonical subset)."""

    def test_utility_codes(self) -> None:
        assert int(ExitCode.LOCK_BUSY) == 10
        assert int(ExitCode.PRECONDITION) == 11


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
