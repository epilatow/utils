#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest"]
# ///
"""Unit tests for borgadm.errors."""

import sys
from pathlib import Path
from typing import ClassVar

from conftest import ExceptionHierarchyBase

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from borgadm.errors import BorgadmError, ExitCode  # noqa: E402

_script_path = REPO_ROOT / "src" / "borgadm" / "errors.py"


class TestExceptionHierarchy(ExceptionHierarchyBase):
    """Test BorgadmError exception hierarchy."""

    BASE_ERROR = BorgadmError
    EXIT_CODE = ExitCode
    EXCLUDED_CODES: ClassVar = {
        ExitCode.SUCCESS,
        ExitCode.WARNING,
        ExitCode.USAGE,
        ExitCode.CRASHED,
    }


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
