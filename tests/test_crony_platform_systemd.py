#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for crony.platform.systemd (the Linux backend)."""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.platform import (  # noqa: E402
    SchedulerWarning,
    UnitLastExit,
    get_scheduler,
    systemd,
)
from crony.unit import (  # noqa: E402
    EntityName,
    EntityRef,
    Interval,
    PriorityClass,
    Schedule,
    UnitSpec,
)

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "src" / "crony" / "platform" / "systemd.py"

_REF = EntityRef("default", "u-test")
# Absolute paths are normally resolved live at apply time and baked into
# the run argv by the runtime layer; the tests pin deterministic values.
_UV = Path("/abs/uv")
_CRONY = Path("/abs/crony")
# The run argv the runtime layer hands the backend as `spec.cmd`.
_CMD = (str(_UV), "run", "--script", str(_CRONY), "_run", str(_REF))
# render() / dispatch don't read the unit dir; pin a placeholder.
_DIR = Path("/unused")


class TestSystemdRendering:
    def test_service_unit(self) -> None:
        svc = systemd.render_service("brew", _CMD)
        assert "[Unit]" in svc
        assert "[Service]" in svc
        assert "Type=oneshot" in svc
        assert "ExecStart=" in svc
        assert " _run default:u-test" in svc
        assert "WorkingDirectory=%h" in svc

    def test_timer_oncalendar(self) -> None:
        timer = systemd.render_timer("j", Schedule.from_str("*-*-* 03:00"))
        assert "OnCalendar=*-*-* 03:00" in timer
        assert "Persistent=true" in timer
        assert "WantedBy=timers.target" in timer

    def test_timer_interval(self) -> None:
        timer = systemd.render_timer("j", Interval.from_str("1h"))
        assert "OnUnitActiveSec=1h" in timer
        # OnActiveSec anchors the first firing to timer activation;
        # without it OnUnitActiveSec has no service run to measure from
        # and the timer never elapses.
        assert "OnActiveSec=1h" in timer

    def test_service_invokes_uv_with_absolute_path(self) -> None:
        # systemd user services run with a minimal default PATH; render
        # uv's absolute path so the unit doesn't depend on PATH.
        svc = systemd.render_service("j", _CMD)
        assert (
            "ExecStart=/abs/uv run --script /abs/crony _run default:u-test"
            in svc
        )


class TestSystemdPriority:
    def test_high_records_intent(self) -> None:
        svc = systemd.render_service("j", _CMD, PriorityClass.HIGH)
        assert "# crony priority=high" in svc
        # high leaves CPU/IO at the Linux defaults.
        assert "Nice=" not in svc
        assert "IOSchedulingClass" not in svc

    def test_low_sets_scheduling(self) -> None:
        svc = systemd.render_service("j", _CMD, PriorityClass.LOW)
        assert "Nice=10" in svc
        assert "IOSchedulingClass=idle" in svc

    def test_normal_emits_nothing(self) -> None:
        svc = systemd.render_service("j", _CMD, PriorityClass.NORMAL)
        assert "Nice=" not in svc
        assert "IOSchedulingClass" not in svc


class TestSystemdScheduler:
    """render() returns the right unit files for each schedule shape."""

    def _spec(self, timing: Schedule | Interval | None) -> UnitSpec:
        return UnitSpec(
            name=EntityName.from_str("default.brew"),
            cmd=_CMD,
            timing=timing,
            priority=PriorityClass.NORMAL,
        )

    def test_service_and_timer_when_scheduled(self) -> None:
        units = get_scheduler("linux", _DIR).render(
            self._spec(Interval.from_str("1h")),
        )
        assert set(units) == {
            "crony-default.brew.service",
            "crony-default.brew.timer",
        }

    def test_service_only_when_scheduleless(self) -> None:
        units = get_scheduler("linux", _DIR).render(self._spec(None))
        assert list(units) == ["crony-default.brew.service"]

    def test_remove_files_unlinks_service_and_timer(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # deactivate would shell out to systemctl; stub it so the test
        # exercises only the unlinks and runs on any platform.
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: None)
        service = tmp_path / "crony-default.brew.service"
        timer = tmp_path / "crony-default.brew.timer"
        service.write_text("x")
        timer.write_text("x")
        get_scheduler("linux", tmp_path).remove_files("default.brew")
        assert not service.exists()
        assert not timer.exists()

    def test_remove_files_tolerates_absent(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: None)
        # A schedule-less entry has no .timer; remove_files tolerates the
        # missing file rather than failing the destroy.
        get_scheduler("linux", tmp_path).remove_files("default.absent")


class TestSystemdActivate:
    """activate() reloads units then, for a scheduled entry, enables the
    timer (boot symlink) and restarts it. The restart -- not a plain
    `enable --now` -- is what re-arms an already-active interval timer:
    `enable --now` no-ops the start on a running unit, so the schedule
    would stay on its stale activation."""

    def _record(self, monkeypatch: Any) -> list[list[str]]:
        calls: list[list[str]] = []

        def fake_run(*a: Any, **k: Any) -> subprocess.CompletedProcess[str]:
            calls.append(list(a[0] if a else k.get("args", [])))
            return subprocess.CompletedProcess([], 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        return calls

    def test_scheduled_enables_then_restarts_timer(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        calls = self._record(monkeypatch)
        get_scheduler("linux", tmp_path).activate(
            "default.brew", scheduled=True
        )
        timer = "crony-default.brew.timer"
        reload_c = ["systemctl", "--user", "--quiet", "daemon-reload"]
        enable_c = ["systemctl", "--user", "--quiet", "enable", timer]
        restart_c = ["systemctl", "--user", "--quiet", "restart", timer]
        assert reload_c in calls
        assert enable_c in calls
        assert restart_c in calls
        # A running timer only re-arms if restart follows the reload that
        # loaded the fresh unit and the enable that linked it.
        assert calls.index(reload_c) < calls.index(enable_c)
        assert calls.index(enable_c) < calls.index(restart_c)

    def test_scheduleless_only_reloads(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        calls = self._record(monkeypatch)
        get_scheduler("linux", tmp_path).activate(
            "default.grp", scheduled=False
        )
        assert calls == [["systemctl", "--user", "--quiet", "daemon-reload"]]


class TestSystemdScheduleArmed:
    """schedule_armed reads `systemctl show`'s next-elapse to tell an
    armed timer from a loaded-but-dead one (the failure mode where an
    interval timer with no valid anchor reports NextElapse=infinity and
    never fires). An inactive / unqueryable timer is not confirmed armed
    (False); an entry with no timer falls back to whether it is loaded."""

    NAME = "default.brew"

    def _sched(self, tmp_path: Path, *, timer: bool) -> Any:
        if timer:
            (tmp_path / f"crony-{self.NAME}.timer").write_text("x")
        return get_scheduler("linux", tmp_path)

    def _stub_show(
        self, monkeypatch: Any, props: dict[str, str] | None
    ) -> None:
        def fake_run(*_a: Any, **_k: Any) -> subprocess.CompletedProcess[str]:
            if props is None:
                raise FileNotFoundError("systemctl")
            body = "".join(f"{k}={v}\n" for k, v in props.items())
            return subprocess.CompletedProcess([], 0, stdout=body)

        monkeypatch.setattr(subprocess, "run", fake_run)

    def test_dead_interval_timer_is_false(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # An active interval timer with no anchor: infinity monotonic
        # elapse, no realtime elapse -> will never fire.
        self._stub_show(
            monkeypatch,
            {
                "ActiveState": "active",
                "NextElapseUSecMonotonic": "infinity",
                "NextElapseUSecRealtime": "",
            },
        )
        sched = self._sched(tmp_path, timer=True)
        assert sched.schedule_armed(self.NAME) is False

    def test_healthy_calendar_timer_is_true(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A calendar timer arms on the realtime clock (a date string);
        # its monotonic elapse is unused (0).
        self._stub_show(
            monkeypatch,
            {
                "ActiveState": "active",
                "NextElapseUSecMonotonic": "0",
                "NextElapseUSecRealtime": "Tue 2026-07-07 03:30:00 PDT",
            },
        )
        sched = self._sched(tmp_path, timer=True)
        assert sched.schedule_armed(self.NAME) is True

    def test_healthy_interval_timer_is_true(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A healthy interval timer arms on the monotonic clock (a finite
        # span), with no realtime elapse.
        self._stub_show(
            monkeypatch,
            {
                "ActiveState": "active",
                "NextElapseUSecMonotonic": "1h 2min 3s",
                "NextElapseUSecRealtime": "",
            },
        )
        sched = self._sched(tmp_path, timer=True)
        assert sched.schedule_armed(self.NAME) is True

    def test_inactive_timer_is_false(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # An inactive timer is not confirmed armed -> False.
        self._stub_show(
            monkeypatch,
            {
                "ActiveState": "inactive",
                "NextElapseUSecMonotonic": "infinity",
                "NextElapseUSecRealtime": "",
            },
        )
        sched = self._sched(tmp_path, timer=True)
        assert sched.schedule_armed(self.NAME) is False

    def test_no_timer_falls_back_to_loaded(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A grouped / disabled entry installs no .timer -- its schedule
        # rides on the loaded `.service`, so armed tracks loaded.
        monkeypatch.setattr(systemd, "_is_enabled", lambda _u: "enabled")
        sched = self._sched(tmp_path, timer=False)
        assert sched.schedule_armed(self.NAME) is True
        monkeypatch.setattr(systemd, "_is_enabled", lambda _u: "")
        assert sched.schedule_armed(self.NAME) is False

    def test_systemctl_absent_is_false(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._stub_show(monkeypatch, None)
        sched = self._sched(tmp_path, timer=True)
        assert sched.schedule_armed(self.NAME) is False


class TestSystemdVerify:
    """verify() reports linger health: silent when enabled, a
    SchedulerWarning (with the enable-linger fix) when disabled, and a
    SchedulerWarning when it can't be determined. The linger probe and
    user resolution are stubbed."""

    def test_enabled_is_silent(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(systemd, "_current_user", lambda: "bob")
        monkeypatch.setattr(systemd, "_linger_enabled", lambda _u: True)
        assert get_scheduler("linux", _DIR).verify() is None

    def test_disabled_warns_with_fix(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(systemd, "_current_user", lambda: "bob")
        monkeypatch.setattr(systemd, "_linger_enabled", lambda _u: False)
        with pytest.raises(SchedulerWarning) as exc:
            get_scheduler("linux", _DIR).verify()
        assert "sudo loginctl enable-linger bob" in str(exc.value)

    def test_unknown_warns(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(systemd, "_current_user", lambda: "bob")
        monkeypatch.setattr(systemd, "_linger_enabled", lambda _u: None)
        with pytest.raises(SchedulerWarning, match="could not determine"):
            get_scheduler("linux", _DIR).verify()

    def test_linger_probe_reads_sentinel(self, monkeypatch: Any) -> None:
        # The probe checks the world-readable sentinel first.
        monkeypatch.setattr(
            Path,
            "exists",
            lambda self: str(self) == "/var/lib/systemd/linger/bob",
        )
        assert systemd._linger_enabled("bob") is True

    def test_linger_probe_loginctl_no(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(Path, "exists", lambda _self: False)

        def fake_run(*_a: object, **_k: object) -> Any:
            return subprocess.CompletedProcess([], 0, stdout="Linger=no\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert systemd._linger_enabled("bob") is False

    def test_linger_probe_loginctl_missing(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(Path, "exists", lambda _self: False)

        def fake_run(*_a: object, **_k: object) -> Any:
            raise FileNotFoundError("loginctl not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert systemd._linger_enabled("bob") is None


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("systemd-analyze") is None,
    reason="systemd-analyze verify is Linux + systemd only",
)
class TestSystemdAnalyzeVerify:
    """Validate every generated systemd unit shape against
    `systemd-analyze verify`, so a malformed template is caught rather
    than only surfacing on a real `systemctl` install. Linux-only; runs
    in CI on the Linux matrix leg and is skipped elsewhere.
    """

    def test_every_unit_shape_verifies(self, tmp_path: Path) -> None:
        # ExecStart's executable must resolve for verify to pass;
        # sys.executable is a real absolute path. The argv after it is
        # irrelevant to verify (the unit is never run).
        real = Path(sys.executable)
        sched = get_scheduler("linux", tmp_path)
        shapes: list[tuple[str, Schedule | Interval | None, PriorityClass]] = [
            (
                "default.cal-normal",
                Schedule.from_str("*-*-* 03:00"),
                PriorityClass.NORMAL,
            ),
            (
                "default.cal-high",
                Schedule.from_str("Mon *-*-* 09:00"),
                PriorityClass.HIGH,
            ),
            ("default.cal-low", Schedule.from_str("daily"), PriorityClass.LOW),
            (
                "default.interval",
                Interval.from_str("30min"),
                PriorityClass.HIGH,
            ),
            ("default.scheduleless", None, PriorityClass.LOW),
        ]
        written: list[Path] = []
        for nm, timing, prio in shapes:
            cmd = (str(real), "run", "--script", str(real), "_run", str(_REF))
            spec = UnitSpec(
                name=EntityName.from_str(nm),
                cmd=cmd,
                timing=timing,
                priority=prio,
            )
            units = sched.render(spec)
            for fname, content in units.items():
                path = tmp_path / fname
                path.write_text(content, encoding="utf-8")
                written.append(path)
        assert written
        # `systemd-analyze verify` exits non-zero on structural errors
        # but only WARNS (rc 0, "... ignoring") on an unknown directive
        # or unparseable value. Gate on both the exit code and those
        # warning markers so a bogus [Service] key is caught too.
        markers = ("ignoring", "failed to parse")
        for path in written:
            proc = subprocess.run(
                ["systemd-analyze", "verify", str(path)],
                capture_output=True,
                text=True,
            )
            output = proc.stdout + proc.stderr
            complaints = [
                line
                for line in output.splitlines()
                if any(m in line.lower() for m in markers)
            ]
            assert proc.returncode == 0 and not complaints, (
                f"{path.name} failed systemd-analyze verify "
                f"(rc={proc.returncode}):\n{output}"
            )


class TestSystemdUnitLastExits:
    """`systemctl show` reports a service's last-launch outcome. The
    backend normalizes ExecMainStatus to the launchctl convention:
    exit codes positive, signal kills (`Result=signal`/`core-dump`)
    negated. A unit whose `ActiveState` is active/activating has a run
    in flight and is left out."""

    def _setup(self, tmp_path: Path) -> None:
        for short in ("ok", "failed", "killed", "running"):
            (tmp_path / f"crony-default.{short}.service").write_text("x")

    def test_parses_show_blocks(self, tmp_path: Path, monkeypatch: Any) -> None:
        self._setup(tmp_path)
        blocks = [
            {
                "Id": "crony-default.ok.service",
                "ActiveState": "inactive",
                "Result": "success",
                "ExecMainStatus": "0",
            },
            {
                "Id": "crony-default.failed.service",
                "ActiveState": "failed",
                "Result": "exit-code",
                "ExecMainStatus": "42",
            },
            {
                "Id": "crony-default.killed.service",
                "ActiveState": "failed",
                "Result": "signal",
                "ExecMainStatus": "9",
            },
            {
                "Id": "crony-default.running.service",
                "ActiveState": "activating",
                "Result": "success",
                "ExecMainStatus": "0",
            },
        ]
        monkeypatch.setattr(systemd, "_show_services", lambda _u: blocks)
        got = get_scheduler("linux", tmp_path).unit_last_exits()
        # default.running (ActiveState activating) is left out.
        assert got == {
            "default.ok": UnitLastExit(exit_status=0),
            "default.failed": UnitLastExit(exit_status=42),
            "default.killed": UnitLastExit(exit_status=-9),
        }

    def test_empty_when_systemctl_absent(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._setup(tmp_path)
        monkeypatch.setattr(systemd, "_show_services", lambda _u: [])
        assert get_scheduler("linux", tmp_path).unit_last_exits() == {}


class TestSystemdUnitName:
    """The UNIT NAME identifier: the `.timer` for a scheduled entry, the
    `.service` for a grouped one, and "" when scheduled-ness is
    unknown."""

    def test_scheduled_is_timer(self) -> None:
        sched = get_scheduler("linux", _DIR)
        assert sched.unit_name("default.j", True) == "crony-default.j.timer"

    def test_unscheduled_is_service(self) -> None:
        sched = get_scheduler("linux", _DIR)
        assert sched.unit_name("default.j", False) == "crony-default.j.service"

    def test_unknown_is_empty(self) -> None:
        assert get_scheduler("linux", _DIR).unit_name("default.j", None) == ""


class TestSystemdUnitPaths:
    """unit_config_path is the `.service` (defines / runs the job);
    unit_timer_path is the separate `.timer` (the schedule arm)."""

    def test_scheduled_splits_service_and_timer(self, tmp_path: Path) -> None:
        (tmp_path / "crony-default.j.service").write_text("x")
        (tmp_path / "crony-default.j.timer").write_text("x")
        sched = get_scheduler("linux", tmp_path)
        assert sched.unit_config_path("default.j") == (
            tmp_path / "crony-default.j.service"
        )
        assert sched.unit_timer_path("default.j") == (
            tmp_path / "crony-default.j.timer"
        )

    def test_grouped_has_service_but_no_timer(self, tmp_path: Path) -> None:
        (tmp_path / "crony-default.g.service").write_text("x")
        sched = get_scheduler("linux", tmp_path)
        assert sched.unit_config_path("default.g") == (
            tmp_path / "crony-default.g.service"
        )
        assert sched.unit_timer_path("default.g") is None

    def test_absent_paths_are_none(self, tmp_path: Path) -> None:
        sched = get_scheduler("linux", tmp_path)
        assert sched.unit_config_path("default.x") is None
        assert sched.unit_timer_path("default.x") is None


class TestSystemdDefaultUnitDir:
    """get_scheduler with no dir resolves the backend default under the
    user's home; an explicit dir overrides it."""

    def test_default_under_home(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(Path, "home", lambda: Path("/tmp/x/home"))
        assert get_scheduler("linux").unit_dir == Path(
            "/tmp/x/home/.config/systemd/user"
        )

    def test_explicit_dir_overrides(self) -> None:
        assert get_scheduler("linux", Path("/unit/dir")).unit_dir == Path(
            "/unit/dir"
        )


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
