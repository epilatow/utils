#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov"]
# ///
# This is AI generated code

"""Tests for bin/darwin-tz-watchdog."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent

_script_path = REPO_ROOT / "bin" / "darwin-tz-watchdog"
_loader = importlib.machinery.SourceFileLoader(
    "darwin_tz_watchdog", str(_script_path)
)
_spec = importlib.util.spec_from_loader("darwin_tz_watchdog", _loader)
assert _spec and _spec.loader
dtw = importlib.util.module_from_spec(_spec)
sys.modules["darwin_tz_watchdog"] = dtw
_spec.loader.exec_module(dtw)


# =============================================================================
# Helpers
# =============================================================================


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess for mocking subprocess.run results."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# Representative `launchctl print` body. Real output is much
# longer; tests only depend on the `pid = N` line so we omit the
# rest, but include enough surrounding noise that the parser has
# to actually find the right line.
_PRINT_BODY = """\
gui/501/com.apple.UserEventAgent-Aqua = {
\ttype = login
\thandle = 100023
\tactive count = 497
\tprogram = /usr/libexec/UserEventAgent
\tpid = 1030
\truns = 1
\tlast exit code = 0
}
"""


# =============================================================================
# _tz_mtime
# =============================================================================


def test_tz_mtime_uses_lstat_not_stat(tmp_path: Path) -> None:
    # Two real files with distinct mtimes -- a "target" that's old
    # and a "symlink" that's recent. _tz_mtime must report the
    # symlink's own mtime (the recent one) so the watchdog notices
    # tz switches that update the link without touching the
    # underlying zone-data file.
    target = tmp_path / "target"
    target.write_bytes(b"data")
    import os as _os

    old_mtime = 1_700_000_000
    new_mtime = 1_800_000_000
    _os.utime(target, (old_mtime, old_mtime))

    link = tmp_path / "link"
    link.symlink_to(target)
    _os.utime(link, (new_mtime, new_mtime), follow_symlinks=False)

    assert dtw._tz_mtime(link) == new_mtime
    # And that stat-following would have returned the old value,
    # confirming we'd miss the switch if we used stat.
    assert link.stat().st_mtime == old_mtime


# =============================================================================
# _uea_aqua_pid
# =============================================================================


def test_uea_aqua_pid_parses_launchctl_print() -> None:
    with patch.object(dtw.subprocess, "run", autospec=True) as run_mock:
        run_mock.return_value = _completed(stdout=_PRINT_BODY)
        assert dtw._uea_aqua_pid() == 1030
    args, _ = run_mock.call_args
    assert args[0][0] == "launchctl"
    assert args[0][1] == "print"
    assert args[0][2].endswith(dtw.UEA_AQUA_LABEL)


def test_uea_aqua_pid_returns_none_when_service_missing() -> None:
    with patch.object(dtw.subprocess, "run", autospec=True) as run_mock:
        run_mock.return_value = _completed(
            stderr="Could not find service\n", returncode=113
        )
        assert dtw._uea_aqua_pid() is None


def test_uea_aqua_pid_returns_none_when_no_pid_line() -> None:
    body = "gui/501/com.apple.UserEventAgent-Aqua = {\n\ttype = login\n}\n"
    with patch.object(dtw.subprocess, "run", autospec=True) as run_mock:
        run_mock.return_value = _completed(stdout=body)
        assert dtw._uea_aqua_pid() is None


def test_uea_aqua_pid_returns_none_on_non_integer_pid() -> None:
    body = "\tpid = not-a-number\n"
    with patch.object(dtw.subprocess, "run", autospec=True) as run_mock:
        run_mock.return_value = _completed(stdout=body)
        assert dtw._uea_aqua_pid() is None


# =============================================================================
# _process_start_epoch
# =============================================================================


def test_process_start_epoch_parses_ps_lstart() -> None:
    # ps -o lstart= example output (note the leading whitespace
    # that ps emits; the parser must strip it).
    with patch.object(dtw.subprocess, "run", autospec=True) as run_mock:
        run_mock.return_value = _completed(stdout="Fri Apr 10 08:07:23 2026\n")
        epoch = dtw._process_start_epoch(1030)
    # Don't assert an absolute epoch (tz-dependent on the host).
    # Round-trip through the same format to confirm we got back
    # the same wall-clock instant ps reported.
    import datetime as dt

    rebuilt = dt.datetime.fromtimestamp(epoch).strftime(dtw.PS_LSTART_FORMAT)
    assert rebuilt == "Fri Apr 10 08:07:23 2026"


def test_process_start_epoch_raises_on_ps_failure() -> None:
    with patch.object(dtw.subprocess, "run", autospec=True) as run_mock:
        run_mock.return_value = _completed(
            stderr="ps: no such process\n", returncode=1
        )
        with pytest.raises(dtw.WatchdogError, match="ps failed for pid 99999"):
            dtw._process_start_epoch(99999)


def test_process_start_epoch_raises_on_unparseable_lstart() -> None:
    with patch.object(dtw.subprocess, "run", autospec=True) as run_mock:
        run_mock.return_value = _completed(stdout="not a date\n")
        with pytest.raises(dtw.WatchdogError, match="unrecognised lstart"):
            dtw._process_start_epoch(1030)


def test_process_start_epoch_raises_on_empty_output() -> None:
    with patch.object(dtw.subprocess, "run", autospec=True) as run_mock:
        run_mock.return_value = _completed(stdout="   \n")
        with pytest.raises(dtw.WatchdogError, match="returned no lstart"):
            dtw._process_start_epoch(1030)


# =============================================================================
# _restart_uea_aqua
# =============================================================================


def test_restart_sends_sigterm_to_pid() -> None:
    with patch.object(dtw.os, "kill", autospec=True) as kill_mock:
        dtw._restart_uea_aqua(1030)
    kill_mock.assert_called_once_with(1030, dtw.signal.SIGTERM)


def test_restart_swallows_process_lookup_error() -> None:
    # A race between detection and signal is benign -- the agent
    # was already gone (and launchd will respawn a fresh, current-
    # TZ instance), so the watchdog has nothing more to do.
    with patch.object(dtw.os, "kill", autospec=True) as kill_mock:
        kill_mock.side_effect = ProcessLookupError(3, "No such process")
        dtw._restart_uea_aqua(1030)  # should not raise


def test_restart_wraps_permission_error() -> None:
    with patch.object(dtw.os, "kill", autospec=True) as kill_mock:
        kill_mock.side_effect = PermissionError(1, "Operation not permitted")
        with pytest.raises(
            dtw.WatchdogError, match="kill\\(1030, SIGTERM\\) refused"
        ):
            dtw._restart_uea_aqua(1030)


# =============================================================================
# check_and_restart
# =============================================================================


def test_no_pid_means_nothing_to_restart(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(dtw, "_uea_aqua_pid", lambda: None)
    # If we ever try to read tz / start / restart in this
    # state, the test will explode -- the not-loaded branch must
    # short-circuit cleanly.
    monkeypatch.setattr(dtw, "_tz_mtime", lambda *_: pytest.fail("unexpected"))
    monkeypatch.setattr(
        dtw,
        "_process_start_epoch",
        lambda _pid: pytest.fail("unexpected"),
    )
    monkeypatch.setattr(
        dtw,
        "_restart_uea_aqua",
        lambda _pid: pytest.fail("unexpected"),
    )

    rc = dtw.check_and_restart(dry_run=False, verbose=False)
    assert rc == dtw.EXIT_OK
    assert capsys.readouterr().out == ""


def test_fresh_agent_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # tz mtime older than process start -> not stale -> no
    # restart.
    monkeypatch.setattr(dtw, "_uea_aqua_pid", lambda: 1030)
    monkeypatch.setattr(dtw, "_tz_mtime", lambda *_: 1_000.0)
    monkeypatch.setattr(dtw, "_process_start_epoch", lambda _pid: 2_000.0)
    kick = create_autospec(dtw._restart_uea_aqua)
    monkeypatch.setattr(dtw, "_restart_uea_aqua", kick)

    rc = dtw.check_and_restart(dry_run=False, verbose=False)
    assert rc == dtw.EXIT_OK
    kick.assert_not_called()


def test_stale_agent_triggers_restart(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(dtw, "_uea_aqua_pid", lambda: 1030)
    monkeypatch.setattr(dtw, "_tz_mtime", lambda *_: 2_000.0)
    monkeypatch.setattr(dtw, "_process_start_epoch", lambda _pid: 1_000.0)
    kick = create_autospec(dtw._restart_uea_aqua)
    monkeypatch.setattr(dtw, "_restart_uea_aqua", kick)

    rc = dtw.check_and_restart(dry_run=False, verbose=False)
    assert rc == dtw.EXIT_OK
    kick.assert_called_once_with(1030)
    out = capsys.readouterr().out
    assert "restarted" in out
    assert dtw.UEA_AQUA_LABEL in out
    assert "pid=1030" in out


def test_dry_run_does_not_restart(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(dtw, "_uea_aqua_pid", lambda: 1030)
    monkeypatch.setattr(dtw, "_tz_mtime", lambda *_: 2_000.0)
    monkeypatch.setattr(dtw, "_process_start_epoch", lambda _pid: 1_000.0)
    kick = create_autospec(dtw._restart_uea_aqua)
    monkeypatch.setattr(dtw, "_restart_uea_aqua", kick)

    rc = dtw.check_and_restart(dry_run=True, verbose=False)
    assert rc == dtw.EXIT_OK
    kick.assert_not_called()
    assert "would restart" in capsys.readouterr().out


def test_verbose_prints_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(dtw, "_uea_aqua_pid", lambda: 1030)
    monkeypatch.setattr(dtw, "_tz_mtime", lambda *_: 1_000.0)
    monkeypatch.setattr(dtw, "_process_start_epoch", lambda _pid: 2_000.0)

    rc = dtw.check_and_restart(dry_run=True, verbose=True)
    out = capsys.readouterr().out
    assert rc == dtw.EXIT_OK
    assert "pid:" in out
    assert "agent start:" in out
    assert "tz mtime:" in out
    assert "stale:       False" in out


# =============================================================================
# main()
# =============================================================================


@pytest.fixture
def fake_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin platform.system() to 'Darwin' so main() doesn't early-exit."""
    monkeypatch.setattr(dtw.platform, "system", lambda: "Darwin")


def test_main_exits_zero_on_non_darwin_without_parsing_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dtw.platform, "system", lambda: "Linux")
    # If main() reaches argparse, an unknown flag would exit 2.
    # The early-return guard must run before that.
    sentinel = create_autospec(dtw.check_and_restart)
    monkeypatch.setattr(dtw, "check_and_restart", sentinel)

    assert dtw.main(["--no-such-flag"]) == dtw.EXIT_OK
    sentinel.assert_not_called()


@pytest.mark.usefixtures("fake_darwin")
def test_main_propagates_watchdog_error_to_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(**_kw: Any) -> int:
        raise dtw.WatchdogError("kaboom")

    monkeypatch.setattr(dtw, "check_and_restart", _raise)
    assert dtw.main([]) == dtw.EXIT_ERROR


@pytest.mark.usefixtures("fake_darwin")
def test_main_propagates_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(**_kw: Any) -> int:
        raise RuntimeError("surprise")

    monkeypatch.setattr(dtw, "check_and_restart", _raise)
    assert dtw.main([]) == dtw.EXIT_ERROR


@pytest.mark.usefixtures("fake_darwin")
def test_main_dispatches_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def _capture(**kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(dtw, "check_and_restart", _capture)
    assert dtw.main(["--dry-run", "--verbose"]) == dtw.EXIT_OK
    assert seen == {"dry_run": True, "verbose": True}


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
