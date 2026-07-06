#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""End-to-end tests: drive the real `crony` CLI as a subprocess against
the host's real scheduler -- launchd on darwin, `systemd --user` on
linux -- so behavior our mocked unit tests cannot see (does a timer
actually arm? does apply re-arm a dead one?) is exercised for real.

These are the tests that catch the class of bug a mocked suite cannot:
a systemd interval timer that is loaded and enabled yet will never fire.
The unit tests stub `systemctl`, so they can only assert which command
crony runs, never whether the resulting timer is live.

Isolation and cleanup
----------------------
Every job an e2e run installs lives in the reserved `crony-e2e` bundle,
and config / state / (on darwin) the unit dir are redirected to a
throwaway tmp tree via the CRONY_* env overrides. crony's whole view of
"installed units" is scoped to its unit dir, so a run cannot see -- let
alone modify -- the operator's real jobs. Each test tears its bundle
down at fixture teardown, and the fixture also destroys the bundle
before it runs, sweeping any leftovers from a previously killed run.

If a run is hard-killed (SIGKILL / power loss) before teardown, remove
leftovers by hand:

  linux:   systemctl --user list-timers --all | grep crony-crony-e2e
           rm ~/.config/systemd/user/crony-crony-e2e.* \\
             && systemctl --user daemon-reload
  darwin:  launchctl print "gui/$(id -u)" | grep crony-e2e
           launchctl bootout "gui/$(id -u)/org.crony.crony-e2e.<job>"

Linux requires a running user service manager (a booted-systemd host
with lingering enabled and XDG_RUNTIME_DIR pointing at the user runtime
dir), and darwin a usable launchd GUI session; `tests/linux-docker-test.sh`
and CI provide one. A requested run that cannot reach the running
platform's scheduler fails (it does not skip) -- an explicitly requested
suite that silently passed would hide the missing coverage.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.platform import current_platform  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "bin" / "crony"
CRONY_BIN = _script_path

# The reserved bundle every e2e job lives under. Namespaces all unit
# files / launchd labels away from real jobs and is the single cleanup
# handle. Never name a real bundle this.
E2E_BUNDLE = "crony-e2e"

_PLATFORM = current_platform()
_IS_LINUX = _PLATFORM == "linux"
_IS_DARWIN = _PLATFORM == "darwin"


def _user_systemd_up() -> bool:
    """Whether a systemd user manager is reachable (a booted-systemd host
    with lingering + XDG_RUNTIME_DIR). `show-environment` succeeds only
    when the user bus answers. `_require_scheduler` uses this to fail a
    requested run that cannot reach one, rather than skip it."""
    if not _IS_LINUX:
        return False
    try:
        return (
            subprocess.run(
                ["systemctl", "--user", "show-environment"],
                capture_output=True,
                timeout=10,
            ).returncode
            == 0
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return False


def _launchd_usable() -> bool:
    """Whether the per-user launchd domain is reachable. `launchctl print
    gui/<uid>` succeeds only when the GUI (Aqua) session bootstrap exists.
    `_require_scheduler` uses this to fail a requested run that cannot
    reach one, rather than skip it."""
    if not _IS_DARWIN:
        return False
    try:
        return (
            subprocess.run(
                ["launchctl", "print", f"gui/{os.getuid()}"],
                capture_output=True,
                timeout=10,
            ).returncode
            == 0
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return False


_SYSTEMD_USER = _user_systemd_up()
_LAUNCHD_USABLE = _launchd_usable()


def _require_scheduler() -> None:
    """Fail -- do not skip -- when a requested e2e run cannot drive the
    running platform's scheduler. An explicitly requested suite that
    silently skips is a false green: the coverage the caller asked for
    did not happen. A genuine platform mismatch (a systemd test on
    darwin) is a plain skipif on the class, handled before this runs."""
    if _IS_LINUX and not _SYSTEMD_USER:
        pytest.fail(
            "crony e2e needs a systemd user manager, but none is "
            "reachable -- boot systemd, `loginctl enable-linger`, and set "
            "XDG_RUNTIME_DIR (see tests/linux-docker-test.sh)."
        )
    if _IS_DARWIN and not _LAUNCHD_USABLE:
        pytest.fail(
            "crony e2e needs a usable launchd GUI (Aqua) session, but "
            "none is reachable (a headless host)."
        )
    if not _IS_LINUX and not _IS_DARWIN:
        pytest.fail(f"crony e2e has no scheduler backend for {_PLATFORM!r}.")


pytestmark = pytest.mark.e2e


class _CronyE2E:
    """A subprocess `crony` driver over an isolated config / state /
    unit-dir namespace scoped to the reserved e2e bundle."""

    def __init__(self, tmp_path: Path) -> None:
        self.config_dir = tmp_path / "config"
        self.config_dir.mkdir()
        self.dropin_dir = tmp_path / "dropin"
        self.dropin_dir.mkdir()
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir()
        self.config_file = self.config_dir / "config.toml"
        self.config_file.write_text("# crony e2e (empty base bundle)\n")
        self.env = os.environ.copy()
        self.env["CRONY_CONFIG_DIR"] = str(self.config_dir)
        self.env["CRONY_CONFIG_FILE"] = str(self.config_file)
        self.env["CRONY_CONFIG_DROPIN_DIR"] = str(self.dropin_dir)
        self.env["CRONY_STATE_DIR"] = str(self.state_dir)
        if _IS_DARWIN:
            # launchd loads a plist by explicit path, so a throwaway unit
            # dir is fully isolated (only the gui-domain label is shared,
            # namespaced by the reserved bundle).
            self.unit_dir = tmp_path / "units"
            self.unit_dir.mkdir()
            self.env["CRONY_UNIT_DIR"] = str(self.unit_dir)
        else:
            # The systemd user manager only scans its own search path, so
            # units must live in the default dir for `systemctl --user` to
            # see them; the reserved-bundle prefix keeps them isolated.
            self.unit_dir = Path.home() / ".config" / "systemd" / "user"

    def full(self, short: str) -> str:
        return f"{E2E_BUNDLE}.{short}"

    def write_bundle(self, jobs_toml: str, select: list[str]) -> None:
        """Write the reserved bundle: the given `[job.*]` TOML plus a
        `[target.<platform>]` selecting `select` on this host, then stamp
        UUIDs (crony rejects an unstamped job)."""
        array = "[" + ", ".join(f'"{s}"' for s in select) + "]"
        body = f"{jobs_toml}\n[target.{_PLATFORM}]\njobs = {array}\n"
        (self.dropin_dir / f"{E2E_BUNDLE}.toml").write_text(body)
        self.crony("config", "update", "-b", E2E_BUNDLE)

    def crony(
        self, *args: str, check: bool = True, timeout: int = 120
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(CRONY_BIN), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )

    def systemctl_user(
        self, *args: str, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["systemctl", "--user", *args],
            env=self.env,
            capture_output=True,
            text=True,
            check=check,
        )

    def status_config(self, full_name: str) -> str | None:
        """The CONFIG cell `crony status` shows for `full_name`, or None
        when the entry has no row."""
        out = self.crony("status", "-b", E2E_BUNDLE, check=False).stdout
        for line in out.splitlines():
            toks = line.split()
            if toks and toks[0] == full_name:
                return toks[1]
        return None

    def destroy_bundle(self) -> None:
        """Best-effort teardown of every unit in the reserved bundle."""
        self.crony("destroy", "--all", "-b", E2E_BUNDLE, check=False)
        if _IS_LINUX:
            self.systemctl_user("daemon-reload")


@pytest.fixture
def e2e(tmp_path: Path) -> Iterator[_CronyE2E]:
    # A requested e2e that cannot drive this platform's scheduler fails
    # here rather than skipping -- the caller asked for the coverage.
    _require_scheduler()
    h = _CronyE2E(tmp_path)
    # Sweep any leftovers a previously killed run left in this namespace
    # before starting, then guarantee teardown even on test failure.
    h.destroy_bundle()
    try:
        yield h
    finally:
        h.destroy_bundle()


def _sabotage_timer_dead(h: _CronyE2E, short: str) -> None:
    """Put an applied interval timer into the loaded-but-dead runtime
    state a reboot leaves behind: rewrite it to the pre-anchor shape
    (OnUnitActiveSec with no OnActiveSec) and re-activate it. With no
    service run to measure from, its next elapse is infinity -- active,
    enabled, and unable to ever fire."""
    timer = h.unit_dir / f"crony-{h.full(short)}.timer"
    timer.write_text(
        "[Unit]\n"
        "Description=e2e dead timer\n"
        "\n"
        "[Timer]\n"
        "OnUnitActiveSec=8h\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    h.systemctl_user("daemon-reload", check=True)
    h.systemctl_user("restart", timer.name, check=True)


class TestApplyLifecycle:
    """Cross-platform: apply / status / destroy through the real
    scheduler on whichever backend the host runs.

    These exercise crony's control plane, which runs in the test process
    and so sees the isolated CRONY_* dirs. Job *execution* is not covered
    here: the scheduler re-invokes `crony _run` in a fresh process that
    does not inherit the test's env overrides, so a triggered job
    resolves the real dirs, not the isolated ones -- there is no way to
    observe an isolated run without baking the overrides into the unit
    (which production does not do). The bug these suites exist to catch
    is a schedule that never fires, asserted below off the live timer
    state, not off a job actually running."""

    def test_apply_reports_synced(self, e2e: _CronyE2E) -> None:
        e2e.write_bundle(
            '[job.probe]\ncommand = "true"\nschedule = "*-*-* 03:00"\n',
            ["probe"],
        )
        e2e.crony("apply", e2e.full("probe"))
        assert e2e.status_config(e2e.full("probe")) == "synced"

    def test_destroy_removes_deployment(self, e2e: _CronyE2E) -> None:
        e2e.write_bundle(
            '[job.probe]\ncommand = "true"\nschedule = "*-*-* 03:00"\n',
            ["probe"],
        )
        e2e.crony("apply", e2e.full("probe"))
        assert e2e.status_config(e2e.full("probe")) == "synced"
        e2e.crony("destroy", e2e.full("probe"))
        # Still in config, no longer deployed -> missing (not synced).
        assert e2e.status_config(e2e.full("probe")) == "missing"


@pytest.mark.skipif(
    not _IS_LINUX, reason="systemd interval-timer arming is Linux-only"
)
class TestSystemdTimerArming:
    """The bug class mocked tests cannot reach: a systemd interval timer
    that is loaded yet will never fire. Asserted through `crony status`,
    which reads the live next-elapse."""

    def test_fresh_interval_apply_is_synced(self, e2e: _CronyE2E) -> None:
        # A first apply starts the timer fresh, anchoring OnActiveSec, so
        # a healthy interval job reads synced (guards a render that drops
        # the anchor -- it would read broken).
        e2e.write_bundle(
            '[job.probe]\ncommand = "true"\ninterval = "8h"\n', ["probe"]
        )
        e2e.crony("apply", e2e.full("probe"))
        assert e2e.status_config(e2e.full("probe")) == "synced"

    def test_dead_timer_reports_broken(self, e2e: _CronyE2E) -> None:
        # Detection: an active timer that will never fire (next elapse
        # infinity) reads broken, not a benign synced/never.
        e2e.write_bundle(
            '[job.probe]\ncommand = "true"\ninterval = "8h"\n', ["probe"]
        )
        e2e.crony("apply", e2e.full("probe"))
        _sabotage_timer_dead(e2e, "probe")
        assert e2e.status_config(e2e.full("probe")) == "broken"

    def test_apply_re_arms_dead_timer(self, e2e: _CronyE2E) -> None:
        # The repair: re-applying a dead-but-active interval timer must
        # leave it armed. An apply that only reloads the unit without
        # restarting the already-active timer leaves it dead -- this is
        # the regression guard for that failure.
        e2e.write_bundle(
            '[job.probe]\ncommand = "true"\ninterval = "8h"\n', ["probe"]
        )
        e2e.crony("apply", e2e.full("probe"))
        _sabotage_timer_dead(e2e, "probe")
        assert e2e.status_config(e2e.full("probe")) == "broken"
        e2e.crony("apply", e2e.full("probe"))
        assert e2e.status_config(e2e.full("probe")) == "synced"


@pytest.mark.skipif(not _IS_DARWIN, reason="launchd backend is darwin-only")
class TestLaunchdInterval:
    """launchd carries the schedule in the plist and has no loaded-but-
    dead state; a freshly applied interval job is simply armed."""

    def test_interval_apply_is_synced(self, e2e: _CronyE2E) -> None:
        e2e.write_bundle(
            '[job.probe]\ncommand = "true"\ninterval = "8h"\n', ["probe"]
        )
        e2e.crony("apply", e2e.full("probe"))
        assert e2e.status_config(e2e.full("probe")) == "synced"


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
