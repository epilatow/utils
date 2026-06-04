#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for crony.platform.darwin (the DarwinHost backend).

Tests that mock the host commands run on any platform; tests that
exercise a real darwin-only syscall (the kqueue pid-exit wait) are
guarded with a darwin skipif.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.platform import DarwinHost, PidWait  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "src" / "crony" / "platform" / "darwin.py"


@pytest.mark.skipif(
    sys.platform != "darwin", reason="kqueue pid-exit wait is darwin-only"
)
class TestDarwinWaitForPidExit:
    """The kqueue-based pid-exit wait, exercised against real processes.
    Must be reliable without polling."""

    def test_live_pid_exits_during_wait(self) -> None:
        proc = subprocess.Popen(["sleep", "0.3"])
        try:
            t0 = time.monotonic()
            result = DarwinHost().wait_for_pid_exit(proc.pid, timeout=5.0)
            dt = time.monotonic() - t0
            assert result is PidWait.EXITED
            assert 0.2 < dt < 2.0, f"unexpected wait duration: {dt}"
        finally:
            proc.wait()

    def test_already_dead_pid_returns_exited(self) -> None:
        proc = subprocess.Popen(["true"])
        proc.wait()
        # Either the kernel still has zombie info (kqueue returns
        # immediately) or the pid has been recycled (we wait for a new
        # process to exit, possibly hitting timeout). Both are
        # acceptable; the call must not hang past the timeout.
        result = DarwinHost().wait_for_pid_exit(proc.pid, timeout=2.0)
        assert result in {PidWait.EXITED, PidWait.TIMED_OUT}

    def test_long_running_pid_times_out(self) -> None:
        proc = subprocess.Popen(["sleep", "5"])
        try:
            t0 = time.monotonic()
            result = DarwinHost().wait_for_pid_exit(proc.pid, timeout=0.2)
            dt = time.monotonic() - t0
            assert result is PidWait.TIMED_OUT
            assert 0.15 < dt < 0.6, f"unexpected wait duration: {dt}"
        finally:
            proc.terminate()
            proc.wait()


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
