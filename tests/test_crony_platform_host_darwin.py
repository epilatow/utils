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

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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


class TestDarwinKeychain:
    """DarwinHost.keychain_secret shells out to `security`; the stdlib
    subprocess (which the backend imports) is stubbed so these run on
    any platform."""

    def test_returns_secret_on_success(self, monkeypatch: Any) -> None:
        captured: dict[str, Any] = {}

        def fake_run(argv: list[str], **_k: object) -> Any:
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, stdout="sekret\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert DarwinHost().keychain_secret("svc", "acct") == "sekret"
        # `-s svc` precedes `-a acct`, and `-w` is the trailing flag.
        argv = captured["argv"]
        assert argv[argv.index("-s") + 1] == "svc"
        assert argv[argv.index("-a") + 1] == "acct"
        assert argv[-1] == "-w"

    def test_omits_account_when_none(self, monkeypatch: Any) -> None:
        captured: dict[str, Any] = {}

        def fake_run(argv: list[str], **_k: object) -> Any:
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, stdout="x\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        DarwinHost().keychain_secret("svc", None)
        assert "-a" not in captured["argv"]

    def test_missing_item_returns_none(self, monkeypatch: Any) -> None:
        def fake_run(argv: list[str], **_k: object) -> Any:
            return subprocess.CompletedProcess(argv, 44, stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert DarwinHost().keychain_secret("svc", None) is None

    def test_security_missing_returns_none(self, monkeypatch: Any) -> None:
        def fake_run(*_a: object, **_k: object) -> Any:
            raise FileNotFoundError("security not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert DarwinHost().keychain_secret("svc", None) is None


class TestDarwinKeepAwake:
    """DarwinHost.keep_awake_argv wraps the command in `caffeinate`;
    shutil.which (which the backend uses) is stubbed."""

    def test_wraps_with_caffeinate(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            shutil,
            "which",
            lambda n: "/x/caffeinate" if n == "caffeinate" else None,
        )
        argv, note = DarwinHost().keep_awake_argv(
            ["/bin/sh", "-c", "true"], "default.a"
        )
        assert argv == ["/x/caffeinate", "-i", "-s", "/bin/sh", "-c", "true"]
        assert note is None

    def test_missing_caffeinate_runs_unwrapped(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(shutil, "which", lambda _n: None)
        argv, note = DarwinHost().keep_awake_argv(["true"], "default.a")
        assert argv == ["true"]
        assert note is not None and "caffeinate not found" in note


class TestDarwinInteractive:
    """The desktop-interaction primitives. supports_interactive is True;
    the idle / lock / dialog ops build ioreg / osascript invocations,
    with the stdlib subprocess stubbed so these run on any platform."""

    def test_supports_interactive(self) -> None:
        assert DarwinHost().supports_interactive is True

    def test_hid_idle_parses_nanoseconds(self, monkeypatch: Any) -> None:
        sample = (
            '  | |   "HIDIdleTime" = 7500000000\n'
            '  | |   "HIDKeyboardCapsLockOn" = No\n'
        )

        def fake_run(*a: Any, **_k: object) -> Any:
            return subprocess.CompletedProcess(a, 0, stdout=sample, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert DarwinHost().hid_idle_seconds() == 7.5

    def test_hid_idle_missing_field_returns_zero(
        self, monkeypatch: Any
    ) -> None:
        def fake_run(*a: Any, **_k: object) -> Any:
            return subprocess.CompletedProcess(a, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert DarwinHost().hid_idle_seconds() == 0.0

    def test_hid_idle_subprocess_failure_returns_zero(
        self, monkeypatch: Any
    ) -> None:
        def fake_run(*_a: object, **_k: object) -> Any:
            raise FileNotFoundError("ioreg not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert DarwinHost().hid_idle_seconds() == 0.0

    def test_screen_locked_yes(self, monkeypatch: Any) -> None:
        out = (
            "IOConsoleUsers = "
            '({"CGSSessionScreenIsLocked"=Yes,"kCGSSessionUserNameKey"="me"})'
        )

        def fake_run(*a: Any, **_k: object) -> Any:
            return subprocess.CompletedProcess(a, 0, stdout=out, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert DarwinHost().screen_locked() is True

    def test_screen_locked_no(self, monkeypatch: Any) -> None:
        out = 'IOConsoleUsers = ({"kCGSSessionUserNameKey"="me"})'

        def fake_run(*a: Any, **_k: object) -> Any:
            return subprocess.CompletedProcess(a, 0, stdout=out, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert DarwinHost().screen_locked() is False

    def test_show_dialog_returns_clicked_button(self, monkeypatch: Any) -> None:
        captured: dict[str, Any] = {}

        def fake_run(cmd: list[str], **_k: object) -> Any:
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(
                cmd, 0, stdout="button returned:Run Job\n", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        clicked = DarwinHost().show_dialog(
            "crony: j", "go?", ["Cancel Job", "Delay Job", "Run Job"]
        )
        assert clicked == "Run Job"
        script = captured["cmd"][2]
        # The last button is the AppleScript default, the first the
        # cancel button.
        assert 'default button "Run Job"' in script
        assert 'cancel button "Cancel Job"' in script

    def test_show_dialog_exact_match_not_substring(
        self, monkeypatch: Any
    ) -> None:
        # A button label that is a substring of another must not shadow
        # it: clicking "Run" returns "Run", not "Run Job".
        def fake_run(cmd: list[str], **_k: object) -> Any:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="button returned:Run\n", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert DarwinHost().show_dialog("t", "b", ["Run Job", "Run"]) == "Run"

    def test_show_dialog_nonzero_returns_empty(self, monkeypatch: Any) -> None:
        # osascript exits non-zero when the cancel button is clicked.
        def fake_run(cmd: list[str], **_k: object) -> Any:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert DarwinHost().show_dialog("t", "b", ["No", "Yes"]) == ""

    def test_show_dialog_osascript_missing_returns_empty(
        self, monkeypatch: Any
    ) -> None:
        def fake_run(*_a: object, **_k: object) -> Any:
            raise FileNotFoundError("osascript not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert DarwinHost().show_dialog("t", "b", ["No", "Yes"]) == ""

    def test_failure_dialog_escapes_and_detaches(
        self, monkeypatch: Any
    ) -> None:
        captured: dict[str, Any] = {}

        def fake_popen(cmd: list[str], **kwargs: object) -> Any:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return None

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        DarwinHost().show_failure_dialog("crony: boom", 'a "q" \\ b')
        assert captured["cmd"][0:2] == ["osascript", "-e"]
        script = captured["cmd"][2]
        assert "display dialog" in script
        # Raw quotes / backslashes from the body arrive escaped.
        assert '\\"q\\"' in script
        assert "\\\\ b" in script
        # Detached so the modal can't stall the runner.
        assert captured["kwargs"].get("start_new_session") is True


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
