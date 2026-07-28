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

A daemon job is restarted by the supervisor whenever it stops, so
teardown sweeps the scheduler directly after `crony destroy` rather than
trusting that call alone.

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
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.platform import current_platform

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
                check=False,
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
                check=False,
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


# Every unit this suite installs is prefixed with the reserved bundle
# name, so a teardown sweep keyed on it cannot reach a real job.
_UNIT_GLOB = f"crony-{E2E_BUNDLE}."


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
        `[target.platform.<platform>]` selecting `select` on this host,
        then stamp UUIDs (crony rejects an unstamped job)."""
        array = "[" + ", ".join(f'"{s}"' for s in select) + "]"
        body = f"{jobs_toml}\n[target.platform.{_PLATFORM}]\njobs = {array}\n"
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

    def darwin_label(self, short: str, *, jitter: bool = False) -> str:
        """The launchd Label for `short`'s service (or its jitter
        companion) in the reserved bundle."""
        suffix = ".jitter" if jitter else ""
        return f"org.crony.{self.full(short)}{suffix}"

    def darwin_plist(self, short: str, *, jitter: bool = False) -> Path:
        """The on-disk plist path for `short`'s service (or companion)."""
        return (
            self.unit_dir / f"{self.darwin_label(short, jitter=jitter)}.plist"
        )

    def launchctl_loaded(self, label: str) -> bool:
        """Whether `label` is registered in the user's GUI domain."""
        return (
            subprocess.run(
                ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                capture_output=True,
                text=True,
                check=False,
            ).returncode
            == 0
        )

    def _status_row(self, full_name: str) -> list[str] | None:
        """`crony status`'s row for `full_name` split into cells, or None
        when the entry has no row. The default columns are job-or-uuid,
        config, schedule, status, last-ran."""
        out = self.crony("status", "-b", E2E_BUNDLE, check=False).stdout
        for line in out.splitlines():
            toks = line.split()
            if toks and toks[0] == full_name:
                return toks
        return None

    def status_config(self, full_name: str) -> str | None:
        """The CONFIG cell `crony status` shows for `full_name`, or None
        when the entry has no row."""
        row = self._status_row(full_name)
        return row[1] if row else None

    def status_status(self, full_name: str) -> str | None:
        """The STATUS cell `crony status` shows for `full_name`, or None
        when the entry has no row."""
        row = self._status_row(full_name)
        return row[3] if row and len(row) > 3 else None

    def destroy_bundle(self) -> None:
        """Best-effort teardown of every unit in the reserved bundle.

        Falls back to the platform directly afterwards: a daemon is
        restarted whenever it stops, so anything `crony destroy` failed
        to reach would keep coming back rather than simply lingering."""
        self.crony("destroy", "--all", "-b", E2E_BUNDLE, check=False)
        if _IS_LINUX:
            self.systemctl_user("daemon-reload")
        self._force_stop_leftovers()

    def _force_stop_leftovers(self) -> None:
        """Stop any reserved-bundle unit still registered with the
        scheduler. Every target is name-scoped to the reserved bundle, so
        this can only reach units this suite installed."""
        if _IS_LINUX:
            listed = self.systemctl_user(
                "list-units", "--all", "--no-legend", f"{_UNIT_GLOB}*"
            ).stdout
            for line in listed.splitlines():
                unit = line.split()[0] if line.split() else ""
                if unit.startswith(_UNIT_GLOB):
                    self.systemctl_user("disable", "--now", unit)
            self.systemctl_user("daemon-reload")
            return
        listed = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        for token in listed.split():
            if token.startswith(f"org.crony.{E2E_BUNDLE}."):
                subprocess.run(
                    ["launchctl", "bootout", f"gui/{os.getuid()}/{token}"],
                    capture_output=True,
                    check=False,
                )

    def wait_until(
        self, predicate: Callable[[], bool], *, timeout: float, what: str
    ) -> None:
        """Poll `predicate` until it holds, failing with `what` on
        timeout. The supervisors act asynchronously, so every assertion
        about a daemon being up (or gone) has to wait for it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.5)
        raise AssertionError(f"timed out waiting for {what}")


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

    def test_eligible_interval_timer_seeds_onactive_with_offset(
        self, e2e: _CronyE2E
    ) -> None:
        # An interval at or above the 10m jitter floor delays its first
        # fire by the model's per-job offset (OnActiveSec = a bare-seconds
        # value, not the interval) while the cadence stays the full
        # interval. No RandomizedDelaySec -- the offset is ours.
        e2e.write_bundle(
            '[job.probe]\ncommand = "true"\ninterval = "8h"\n', ["probe"]
        )
        e2e.crony("apply", e2e.full("probe"))
        body = (e2e.unit_dir / f"crony-{e2e.full('probe')}.timer").read_text()
        assert "OnUnitActiveSec=8h" in body
        assert re.search(r"OnActiveSec=\d+s", body)
        assert "OnActiveSec=8h" not in body
        assert "RandomizedDelaySec" not in body

    def test_apply_re_arms_dead_timer_matching_content(
        self, e2e: _CronyE2E
    ) -> None:
        # The production case: the on-disk timer already matches crony's
        # render (a reboot / reload left it un-restarted), so it is dead
        # yet content-synced. apply must re-arm it even though there is no
        # content drift -- otherwise it no-ops the entry it calls broken.
        e2e.write_bundle(
            '[job.probe]\ncommand = "true"\ninterval = "8h"\n', ["probe"]
        )
        e2e.crony("apply", e2e.full("probe"))
        timer = e2e.unit_dir / f"crony-{e2e.full('probe')}.timer"
        rendered = timer.read_text()
        # Drive it dead without diverging the content from crony's render:
        # activate the pre-anchor shape (dead), then swap the rendered
        # content back via a reload only -- the active timer keeps its
        # stale anchor, so it stays dead with the correct file on disk.
        timer.write_text(
            "[Unit]\nDescription=e2e\n\n[Timer]\nOnUnitActiveSec=8h\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
        e2e.systemctl_user("daemon-reload", check=True)
        e2e.systemctl_user("restart", timer.name, check=True)
        timer.write_text(rendered)
        e2e.systemctl_user("daemon-reload", check=True)
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


@pytest.mark.skipif(
    not _IS_DARWIN, reason="launchd jitter companion is darwin-only"
)
class TestLaunchdJitter:
    """launchd has no native start-time randomization, so a jittered
    interval job (>= the 10m floor) gets a second LaunchAgent -- a jitter
    companion -- installed and loaded beside its service, sharing the
    entity's apply / disable / destroy lifecycle. An 8h interval sits above
    the production floor, so it is jittered without any test seam; the
    fires-and-self-unloads case drives a short interval through the live
    env floors."""

    _JITTERED = '[job.probe]\ncommand = "true"\ninterval = "8h"\n'

    def test_companion_installed_and_loaded(self, e2e: _CronyE2E) -> None:
        e2e.write_bundle(self._JITTERED, ["probe"])
        e2e.crony("apply", e2e.full("probe"))
        assert e2e.status_config(e2e.full("probe")) == "synced"
        assert e2e.darwin_plist("probe").exists()
        assert e2e.darwin_plist("probe", jitter=True).exists()
        assert e2e.launchctl_loaded(e2e.darwin_label("probe"))
        assert e2e.launchctl_loaded(e2e.darwin_label("probe", jitter=True))

    def test_calendar_job_has_no_companion(self, e2e: _CronyE2E) -> None:
        e2e.write_bundle(
            '[job.probe]\ncommand = "true"\nschedule = "*-*-* 03:00"\n',
            ["probe"],
        )
        e2e.crony("apply", e2e.full("probe"))
        assert e2e.darwin_plist("probe").exists()
        assert not e2e.darwin_plist("probe", jitter=True).exists()

    def test_status_surfaces_companion_path(self, e2e: _CronyE2E) -> None:
        e2e.write_bundle(self._JITTERED, ["probe"])
        e2e.crony("apply", e2e.full("probe"))
        out = e2e.crony(
            "status",
            "-b",
            E2E_BUNDLE,
            "--cols",
            "job-or-uuid,unit-config-2",
        ).stdout
        assert str(e2e.darwin_plist("probe", jitter=True)) in out

    def test_disable_removes_companion_enable_restores(
        self, e2e: _CronyE2E
    ) -> None:
        e2e.write_bundle(self._JITTERED, ["probe"])
        e2e.crony("apply", e2e.full("probe"))
        e2e.crony("disable", e2e.full("probe"))
        assert e2e.darwin_plist("probe").exists()
        assert not e2e.darwin_plist("probe", jitter=True).exists()
        assert not e2e.launchctl_loaded(e2e.darwin_label("probe", jitter=True))
        e2e.crony("enable", e2e.full("probe"))
        assert e2e.darwin_plist("probe", jitter=True).exists()
        assert e2e.launchctl_loaded(e2e.darwin_label("probe", jitter=True))

    def test_reapply_keeps_companion_loaded(self, e2e: _CronyE2E) -> None:
        e2e.write_bundle(self._JITTERED, ["probe"])
        e2e.crony("apply", e2e.full("probe"))
        e2e.crony("apply", e2e.full("probe"))
        assert e2e.launchctl_loaded(e2e.darwin_label("probe"))
        assert e2e.launchctl_loaded(e2e.darwin_label("probe", jitter=True))

    def test_companion_fires_and_self_boots_out(self, e2e: _CronyE2E) -> None:
        # Drive a short interval so the companion fires within seconds --
        # both floors are read live from the env. When it fires it unloads
        # its own label (via the scheduler), so the label disappears while
        # the service stays loaded.
        e2e.env["CRONY_MIN_INTERVAL_SECONDS"] = "5"
        e2e.env["CRONY_JITTER_FLOOR_SECONDS"] = "5"
        e2e.write_bundle(
            '[job.probe]\ncommand = "true"\ninterval = "15s"\n', ["probe"]
        )
        e2e.crony("apply", e2e.full("probe"))
        assert e2e.launchctl_loaded(e2e.darwin_label("probe", jitter=True))
        deadline = time.monotonic() + 60
        while (
            e2e.launchctl_loaded(e2e.darwin_label("probe", jitter=True))
            and time.monotonic() < deadline
        ):
            time.sleep(2)
        assert not e2e.launchctl_loaded(
            e2e.darwin_label("probe", jitter=True)
        ), "jitter companion did not self-unload after firing"
        # The service is untouched: still loaded, firing on its interval.
        assert e2e.launchctl_loaded(e2e.darwin_label("probe"))


class TestDaemonSupervision:
    """A daemon under the real supervisor: the rendered unit starts
    itself and is respawned when it exits, and disable / destroy stop it.

    These assert on scheduler-observable state (the respawn counter, unit
    registration) rather than on the job's command running. crony bakes
    no CRONY_* overrides into a rendered unit, so a runner the scheduler
    spawns reads the real state dir and exits before it reaches the
    command -- which is itself a non-zero exit, so it exercises the
    respawn path. The runner's own exit-code contract (which code means
    restart, which means stay down) is unit-tested.
    """

    def _apply_daemon(self, e2e: _CronyE2E) -> None:
        # Short backoff so a respawn lands inside the poll window.
        e2e.env["CRONY_DAEMON_RESTART_SECONDS"] = "1"
        e2e.write_bundle('[job.d]\ncommand = "true"\ndaemon = true\n', ["d"])
        e2e.crony("apply", e2e.full("d"))

    def _start_marker(self, e2e: _CronyE2E) -> str:
        """A value that changes every time the supervisor starts the
        unit, or `""` when it has never started.

        Deliberately not a restart counter. systemd's `NRestarts` moves
        only for an *automatic* restart, so a manual `crony trigger`
        leaves it flat; the main process's start timestamp moves however
        the start was initiated. launchd's `runs` counts every launch,
        including a kickstart, so it already has that property.
        """
        if _IS_LINUX:
            out = e2e.systemctl_user(
                "show",
                "-p",
                "ExecMainStartTimestampMonotonic",
                f"crony-{e2e.full('d')}.service",
            ).stdout
            _, _, value = out.strip().partition("=")
            return "" if value in ("", "0") else value
        out = subprocess.run(
            [
                "launchctl",
                "print",
                f"gui/{os.getuid()}/{e2e.darwin_label('d')}",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        m = re.search(r"^\s*runs = (\d+)", out, re.MULTILINE)
        return "" if m is None or m.group(1) == "0" else m.group(1)

    def _loaded(self, e2e: _CronyE2E) -> bool:
        if _IS_LINUX:
            state = e2e.systemctl_user(
                "is-active", f"crony-{e2e.full('d')}.service"
            ).stdout.strip()
            return state in ("active", "activating")
        return e2e.launchctl_loaded(e2e.darwin_label("d"))

    def _wait_started(self, e2e: _CronyE2E, what: str) -> str:
        """Block until the unit has been started, and return the marker
        for that start."""
        e2e.wait_until(
            lambda: bool(self._start_marker(e2e)), timeout=60, what=what
        )
        return self._start_marker(e2e)

    def test_starts_itself_without_a_trigger(self, e2e: _CronyE2E) -> None:
        # No timer and nothing kickstarts it: applying a daemon is what
        # starts it, which is the whole point of the mode.
        self._apply_daemon(e2e)
        self._wait_started(e2e, "the daemon to be started by the supervisor")

    def test_supervisor_respawns_it_after_it_exits(
        self, e2e: _CronyE2E
    ) -> None:
        # The unit must be rendered so the supervisor brings it back --
        # KeepAlive on launchd, Restart= on systemd. The marker moving
        # again is that policy working against a command that keeps
        # exiting.
        self._apply_daemon(e2e)
        first = self._wait_started(e2e, "the daemon to start")
        e2e.wait_until(
            lambda: self._start_marker(e2e) not in ("", first),
            timeout=120,
            what="the supervisor to respawn the daemon",
        )

    def test_disable_stops_it(self, e2e: _CronyE2E) -> None:
        # Rewriting the unit file neither stops a running daemon nor
        # un-arms it, so disable has to reach the scheduler.
        self._apply_daemon(e2e)
        self._wait_started(e2e, "the daemon to start")
        e2e.crony("disable", e2e.full("d"))
        # A disabled entry stays registered with the scheduler on
        # purpose -- dormant but triggerable -- so "stopped" is that it
        # is no longer being restarted, not that its unit is gone.
        time.sleep(3)
        settled = self._start_marker(e2e)
        time.sleep(6)
        assert self._start_marker(e2e) == settled, (
            "disabled daemon was still being respawned"
        )

    def test_triggering_a_disabled_daemon_runs_it_once(
        self, e2e: _CronyE2E
    ) -> None:
        # A disabled daemon stays registered and triggerable, so it can
        # be started by hand -- but disabling stripped the supervision
        # (launchd `KeepAlive`, systemd `Restart=`), so what starts is a
        # one-shot: the scheduler does not bring it back when it stops,
        # and it is still disabled for the next boot.
        self._apply_daemon(e2e)
        self._wait_started(e2e, "the daemon to start")
        e2e.crony("disable", e2e.full("d"))
        time.sleep(3)
        settled = self._start_marker(e2e)

        e2e.crony("trigger", e2e.full("d"))
        e2e.wait_until(
            lambda: self._start_marker(e2e) not in ("", settled),
            timeout=60,
            what="the triggered daemon to start",
        )
        started = self._start_marker(e2e)
        # Several restart intervals: it must not start again.
        time.sleep(8)
        assert self._start_marker(e2e) == started, (
            "a triggered disabled daemon was respawned after it stopped"
        )
        # Triggering does not clear the operator-disable, so the entry
        # reports disabled again once the run it started has ended.
        assert e2e.status_status(e2e.full("d")) == "disabled"

    def test_destroy_removes_a_running_daemon(self, e2e: _CronyE2E) -> None:
        # A daemon holds its run lock for its whole life, so destroy has
        # to stop the unit rather than wait for the lock to clear.
        self._apply_daemon(e2e)
        self._wait_started(e2e, "the daemon to start")
        e2e.crony("destroy", e2e.full("d"))
        assert e2e.status_config(e2e.full("d")) in (None, "missing")
        e2e.wait_until(
            lambda: not self._loaded(e2e),
            timeout=60,
            what="the destroyed daemon to stop",
        )


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
