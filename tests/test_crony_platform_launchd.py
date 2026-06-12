#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for crony.platform.launchd (the macOS backend)."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.platform import (  # noqa: E402
    UnitLastExit,
    get_scheduler,
    launchd,
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
_script_path = REPO_ROOT / "src" / "crony" / "platform" / "launchd.py"

_REF = EntityRef("default", "u-test")
# Absolute paths are normally resolved live at apply time and baked into
# the run argv by the runtime layer; the tests pin deterministic values.
_UV = Path("/abs/uv")
_CRONY = Path("/abs/crony")
# The run argv the runtime layer hands the backend as `spec.cmd`.
_CMD = (str(_UV), "run", "--script", str(_CRONY), "_run", str(_REF))
# render() / dispatch don't read the unit dir; pin a placeholder.
_DIR = Path("/unused")


class TestPlistRendering:
    """launchd.render_plist produces well-formed launchd plists."""

    def test_keyword_daily(self) -> None:
        plist = launchd.render_plist(
            "brew",
            _CMD,
            Schedule.from_str("daily"),
        )
        assert "<key>Label</key>" in plist
        assert "<string>org.crony.brew</string>" in plist
        assert "<key>StartCalendarInterval</key>" in plist
        # daily -> 00:00
        assert "<key>Hour</key>" in plist
        assert "<integer>0</integer>" in plist

    def test_oncalendar_simple_time(self) -> None:
        plist = launchd.render_plist(
            "j",
            _CMD,
            Schedule.from_str("*-*-* 03:15"),
        )
        assert "<key>Hour</key>" in plist
        assert "<integer>3</integer>" in plist
        assert "<integer>15</integer>" in plist

    def test_oncalendar_dow_with_time(self) -> None:
        plist = launchd.render_plist(
            "j",
            _CMD,
            Schedule.from_str("Mon *-*-* 09:00"),
        )
        assert "<key>Weekday</key>" in plist
        assert "<integer>1</integer>" in plist  # Mon=1

    def test_oncalendar_first_of_month(self) -> None:
        plist = launchd.render_plist(
            "j",
            _CMD,
            Schedule.from_str("*-*-01 03:00"),
        )
        assert "<key>Day</key>" in plist
        assert "<integer>1</integer>" in plist

    def test_interval(self) -> None:
        plist = launchd.render_plist(
            "j",
            _CMD,
            Interval.from_str("30min"),
        )
        assert "<key>StartInterval</key>" in plist
        assert "<integer>1800</integer>" in plist

    def test_program_args_wrap_uv_in_sh_with_absolute_path(self) -> None:
        plist = launchd.render_plist(
            "j",
            _CMD,
            Schedule.from_str("daily"),
        )
        d = plistlib.loads(plist.encode("utf-8"))
        assert d["ProgramArguments"][:2] == ["/bin/sh", "-c"]
        # uv's absolute path because launchd's per-agent PATH omits
        # it, exec so sh is replaced by uv, and the bundle:uuid ref
        # (not the name) so the runner locates the state dir.
        assert d["ProgramArguments"][2] == (
            "exec /abs/uv run --script /abs/crony _run default:u-test"
        )

    def test_every_shape_is_a_valid_plist(self) -> None:
        # Each rendered plist must parse back as a well-formed plist
        # (plutil only runs at apply on macOS; this catches structural
        # breakage in CI on any platform).
        shapes: list[tuple[Schedule | Interval | None, PriorityClass]] = [
            (Schedule.from_str("daily"), PriorityClass.NORMAL),
            (Interval.from_str("30min"), PriorityClass.NORMAL),
            (Schedule.from_str("Mon *-*-* 09:00"), PriorityClass.HIGH),
            (Schedule.from_str("*-*-* 03:00"), PriorityClass.LOW),
            (None, PriorityClass.NORMAL),  # on-demand, normal priority
        ]
        for timing, priority in shapes:
            plist = launchd.render_plist("j", _CMD, timing, priority)
            d = plistlib.loads(plist.encode("utf-8"))
            assert d["Label"] == "org.crony.j"
            assert d["ProgramArguments"][:2] == ["/bin/sh", "-c"]


class TestLaunchdPriority:
    def test_high(self) -> None:
        plist = launchd.render_plist(
            "j",
            _CMD,
            Schedule.from_str("daily"),
            PriorityClass.HIGH,
        )
        d = plistlib.loads(plist.encode("utf-8"))
        assert d["ProcessType"] == "Interactive"
        assert d["LowPriorityIO"] is False
        assert d["Nice"] == 0

    def test_low(self) -> None:
        plist = launchd.render_plist(
            "j",
            _CMD,
            Schedule.from_str("daily"),
            PriorityClass.LOW,
        )
        d = plistlib.loads(plist.encode("utf-8"))
        assert d["ProcessType"] == "Background"
        assert d["LowPriorityIO"] is True
        assert d["Nice"] == 10

    def test_normal_emits_nothing(self) -> None:
        plist = launchd.render_plist(
            "j",
            _CMD,
            Schedule.from_str("daily"),
            PriorityClass.NORMAL,
        )
        d = plistlib.loads(plist.encode("utf-8"))
        assert "ProcessType" not in d
        assert "LowPriorityIO" not in d
        assert "Nice" not in d
        assert launchd._priority_keys(PriorityClass.NORMAL) == {}


class TestLaunchdScheduler:
    def test_render_one_plist(self) -> None:
        spec = UnitSpec(
            name=EntityName.from_str("default.brew"),
            cmd=_CMD,
            timing=Schedule.from_str("daily"),
            priority=PriorityClass.NORMAL,
        )
        units = get_scheduler("darwin", _DIR).render(spec)
        assert list(units) == ["org.crony.default.brew.plist"]

    def test_installed_names_includes_non_namespaced(
        self, tmp_path: Path
    ) -> None:
        # installed_names keys on the unit name, not entity identity:
        # a stray whose name isn't <bundle>.<short> is still returned
        # so destroy can reach a hand-created / legacy unit.
        (tmp_path / "org.crony.default.real.plist").write_text("x")
        (tmp_path / "org.crony.bogus.plist").write_text("x")  # no dot
        sched = get_scheduler("darwin", tmp_path)
        assert sched.installed_names() == {"default.real", "bogus"}

    def test_remove_files_unlinks_plist(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # deactivate would shell out to launchctl; stub it so the test
        # exercises only the unlink and runs on any platform.
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: None)
        plist = tmp_path / "org.crony.default.brew.plist"
        plist.write_text("x")
        get_scheduler("darwin", tmp_path).remove_files("default.brew")
        assert not plist.exists()

    def test_remove_files_tolerates_absent(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: None)
        # No file on disk: a partial / never-installed entity must not
        # raise when destroyed.
        get_scheduler("darwin", tmp_path).remove_files("default.absent")

    def test_verify_is_noop(self) -> None:
        # launchd auto-loads a logged-in user's agents; there is no
        # logout-survival toggle to warn about, so verify never raises.
        assert get_scheduler("darwin", _DIR).verify() is None


class TestLaunchdUnitLastExits:
    """`launchctl list` rows are `<pid>\\t<status>\\t<label>`; status is
    the last completed run's wait status (0 / positive exit code /
    negative signal). A numeric pid means a launch is in flight, so its
    stale status is skipped and the unit is left out."""

    _LIST = (
        "PID\tStatus\tLabel\n"
        "-\t0\torg.crony.default.ok\n"
        "-\t42\torg.crony.default.failed\n"
        "-\t-9\torg.crony.default.killed\n"
        "1234\t0\torg.crony.default.running\n"
        "-\t0\tcom.apple.somethingelse\n"
    )

    def test_parses_status_column(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(launchd, "_launchctl_list", lambda: self._LIST)
        got = get_scheduler("darwin", _DIR).unit_last_exits()
        # default.running (numeric pid) and the non-crony label are out.
        assert got == {
            "default.ok": UnitLastExit(exit_status=0),
            "default.failed": UnitLastExit(exit_status=42),
            "default.killed": UnitLastExit(exit_status=-9),
        }

    def test_empty_when_launchctl_absent(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(launchd, "_launchctl_list", lambda: "")
        assert get_scheduler("darwin", _DIR).unit_last_exits() == {}


class TestLaunchdUnitName:
    """The UNIT NAME identifier is the launchd label, independent of
    whether the entry is scheduled (one plist per entity)."""

    def test_label_regardless_of_schedule(self) -> None:
        sched = get_scheduler("darwin", _DIR)
        for scheduled in (True, False, None):
            assert sched.unit_name("default.j", scheduled) == (
                "org.crony.default.j"
            )


class TestLaunchdUnitPaths:
    """unit_config_path is the plist; launchd has no separate timer
    unit, so unit_timer_path is always None."""

    def test_config_is_plist_timer_is_none(self, tmp_path: Path) -> None:
        (tmp_path / "org.crony.default.j.plist").write_text("x")
        sched = get_scheduler("darwin", tmp_path)
        assert sched.unit_config_path("default.j") == (
            tmp_path / "org.crony.default.j.plist"
        )
        assert sched.unit_timer_path("default.j") is None

    def test_absent_config_is_none(self, tmp_path: Path) -> None:
        sched = get_scheduler("darwin", tmp_path)
        assert sched.unit_config_path("default.x") is None
        assert sched.unit_timer_path("default.x") is None


class TestLaunchdDefaultUnitDir:
    """get_scheduler with no dir resolves the backend default under the
    user's home; an explicit dir overrides it."""

    def test_default_under_home(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(Path, "home", lambda: Path("/tmp/x/home"))
        assert get_scheduler("darwin").unit_dir == Path(
            "/tmp/x/home/Library/LaunchAgents"
        )

    def test_explicit_dir_overrides(self) -> None:
        assert get_scheduler("darwin", Path("/unit/dir")).unit_dir == Path(
            "/unit/dir"
        )


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
