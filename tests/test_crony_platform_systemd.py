#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for crony.platform.systemd (the Linux backend)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.platform import get_scheduler, systemd  # noqa: E402
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
# Absolute paths are normally resolved live at apply time; the renderers
# take them as explicit args, so the tests pin deterministic values.
_UV = Path("/abs/uv")
_CRONY = Path("/abs/crony")
# render() / dispatch don't read the unit dir; pin a placeholder.
_DIR = Path("/unused")


class TestSystemdRendering:
    def test_service_unit(self) -> None:
        svc = systemd.render_service(
            "brew", _REF, uv_path=_UV, crony_path=_CRONY
        )
        assert "[Unit]" in svc
        assert "[Service]" in svc
        assert "Type=oneshot" in svc
        assert "ExecStart=" in svc
        assert " run default:u-test" in svc
        assert "WorkingDirectory=%h" in svc

    def test_timer_oncalendar(self) -> None:
        timer = systemd.render_timer("j", Schedule.from_str("*-*-* 03:00"))
        assert "OnCalendar=*-*-* 03:00" in timer
        assert "Persistent=true" in timer
        assert "WantedBy=timers.target" in timer

    def test_timer_interval(self) -> None:
        timer = systemd.render_timer("j", Interval.from_str("1h"))
        assert "OnUnitActiveSec=1h" in timer

    def test_service_invokes_uv_with_absolute_path(self) -> None:
        # systemd user services run with a minimal default PATH; render
        # uv's absolute path so the unit doesn't depend on PATH.
        svc = systemd.render_service("j", _REF, uv_path=_UV, crony_path=_CRONY)
        assert (
            "ExecStart=/abs/uv run --script /abs/crony run default:u-test"
            in svc
        )


class TestSystemdPriority:
    def test_high_records_intent(self) -> None:
        svc = systemd.render_service(
            "j", _REF, PriorityClass.HIGH, uv_path=_UV, crony_path=_CRONY
        )
        assert "# crony priority=high" in svc
        # high leaves CPU/IO at the Linux defaults.
        assert "Nice=" not in svc
        assert "IOSchedulingClass" not in svc

    def test_low_sets_scheduling(self) -> None:
        svc = systemd.render_service(
            "j", _REF, PriorityClass.LOW, uv_path=_UV, crony_path=_CRONY
        )
        assert "Nice=10" in svc
        assert "IOSchedulingClass=idle" in svc

    def test_normal_emits_nothing(self) -> None:
        svc = systemd.render_service(
            "j", _REF, PriorityClass.NORMAL, uv_path=_UV, crony_path=_CRONY
        )
        assert "Nice=" not in svc
        assert "IOSchedulingClass" not in svc


class TestSystemdScheduler:
    """render() returns the right unit files for each schedule shape."""

    def _spec(self, timing: Schedule | Interval | None) -> UnitSpec:
        return UnitSpec(
            name=EntityName.from_str("default.brew"),
            ref=_REF,
            timing=timing,
            priority=None,
        )

    def test_service_and_timer_when_scheduled(self) -> None:
        units = get_scheduler("linux", _DIR).render(
            self._spec(Interval.from_str("1h")),
            uv_path=_UV,
            crony_path=_CRONY,
        )
        assert set(units) == {
            "crony-default.brew.service",
            "crony-default.brew.timer",
        }

    def test_service_only_when_scheduleless(self) -> None:
        units = get_scheduler("linux", _DIR).render(
            self._spec(None), uv_path=_UV, crony_path=_CRONY
        )
        assert list(units) == ["crony-default.brew.service"]


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
        shapes: list[
            tuple[str, Schedule | Interval | None, PriorityClass | None]
        ] = [
            ("default.cal-normal", Schedule.from_str("*-*-* 03:00"), None),
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
            spec = UnitSpec(
                name=EntityName.from_str(nm),
                ref=_REF,
                timing=timing,
                priority=prio,
            )
            units = sched.render(spec, uv_path=real, crony_path=real)
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


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
