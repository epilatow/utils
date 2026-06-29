#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for crony.platform.linux (the LinuxHost backend).

Tests that mock the host commands run on any platform; tests that
exercise a real Linux-only syscall (the pidfd pid-exit wait) are guarded
with a Linux skipif.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.platform import LinuxHost, PidWait  # noqa: E402
from crony.platform.fda import FDAWrapper  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "src" / "crony" / "platform" / "linux.py"


@pytest.mark.skipif(
    sys.platform != "linux", reason="/proc pid-exit wait is Linux-only"
)
class TestLinuxWaitForPidExit:
    """The /proc-polling pid-exit wait, exercised against real
    processes."""

    def test_live_pid_exits_during_wait(self) -> None:
        proc = subprocess.Popen(["sleep", "0.3"])
        try:
            t0 = time.monotonic()
            result = LinuxHost().wait_for_pid_exit(proc.pid, timeout=5.0)
            dt = time.monotonic() - t0
            assert result is PidWait.EXITED
            assert 0.2 < dt < 2.0, f"unexpected wait duration: {dt}"
        finally:
            proc.wait()

    def test_already_dead_pid_returns_exited(self) -> None:
        proc = subprocess.Popen(["true"])
        proc.wait()
        # The pid is either gone (no /proc entry) or, if reaped late, a
        # zombie -- both read as exited. If the number raced into reuse,
        # the wait runs to timeout instead. Both are acceptable; the call
        # must not hang past the timeout.
        result = LinuxHost().wait_for_pid_exit(proc.pid, timeout=2.0)
        assert result in {PidWait.EXITED, PidWait.TIMED_OUT}

    def test_long_running_pid_times_out(self) -> None:
        proc = subprocess.Popen(["sleep", "5"])
        try:
            t0 = time.monotonic()
            result = LinuxHost().wait_for_pid_exit(proc.pid, timeout=0.2)
            dt = time.monotonic() - t0
            assert result is PidWait.TIMED_OUT
            assert 0.15 < dt < 0.6, f"unexpected wait duration: {dt}"
        finally:
            proc.terminate()
            proc.wait()


class TestLinuxKeychain:
    def test_no_keychain_returns_none(self) -> None:
        # Linux has no keychain integration; the resolver falls through
        # to its env / file path.
        assert LinuxHost().keychain_secret("svc", "acct") is None


class TestLinuxKeepAwake:
    """LinuxHost.keep_awake_argv wraps the command in `systemd-inhibit`;
    shutil.which (which the backend uses) is stubbed."""

    def test_wraps_with_systemd_inhibit(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            shutil,
            "which",
            lambda n: "/x/systemd-inhibit" if n == "systemd-inhibit" else None,
        )
        argv, note = LinuxHost().keep_awake_argv(
            ["/bin/sh", "-c", "true"], "default.a"
        )
        assert argv[0] == "/x/systemd-inhibit"
        assert "--what=sleep:idle" in argv
        assert "--why=job default.a" in argv
        assert "--" in argv
        assert argv[-3:] == ["/bin/sh", "-c", "true"]
        assert note is None

    def test_missing_systemd_inhibit_runs_unwrapped(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _n: None)
        argv, note = LinuxHost().keep_awake_argv(["true"], "default.a")
        assert argv == ["true"]
        assert note is not None and "systemd-inhibit not found" in note


class TestLinuxFullDiskAccess:
    """Full Disk Access is a macOS TCC concept; on Linux every FDA
    operation is an inert no-op."""

    def test_argv_unchanged(self) -> None:
        argv = ["/bin/sh", "-c", "true"]
        assert LinuxHost().full_disk_access_argv(argv) == argv

    def test_prepare_is_noop(self) -> None:
        assert LinuxHost().prepare_full_disk_access() is None

    def test_state_is_ok(self) -> None:
        assert LinuxHost().full_disk_access_state() is FDAWrapper.OK


class TestLinuxNoDesktop:
    """Desktop interaction is unsupported on Linux: supports_interactive
    is False and the idle / lock / dialog ops raise rather than silently
    no-op."""

    def test_does_not_support_interactive(self) -> None:
        assert LinuxHost().supports_interactive is False

    def test_hid_idle_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            LinuxHost().hid_idle_seconds()

    def test_screen_locked_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            LinuxHost().screen_locked()

    def test_show_dialog_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            LinuxHost().show_dialog("t", "b", ["No", "Yes"])

    def test_show_failure_dialog_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            LinuxHost().show_failure_dialog("t", "b")


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
