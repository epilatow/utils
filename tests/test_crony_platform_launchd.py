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
import time
from pathlib import Path
from typing import Any

import pytest

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


class TestLaunchdReload:
    """activate / enable / disable reload via bootout + bootstrap. The
    bootstrap settles the asynchronous teardown (poll until the label is
    gone) and retries the spurious errno-5 race; a disabled unit is never
    bootstrapped, since a disabled label's bootstrap fails with that same
    errno 5 and the retry could not clear it."""

    def _setup(
        self,
        monkeypatch: Any,
        tmp_path: Path,
        *,
        bootstrap_rcs: tuple[int, ...] = (0,),
        loaded_seq: list[bool] | None = None,
    ) -> tuple[Any, list[list[str]]]:
        sched = get_scheduler("darwin", tmp_path)
        (tmp_path / launchd.plist_filename("default.j")).write_text("x")
        calls: list[list[str]] = []
        rcs = list(bootstrap_rcs)

        def fake_run(
            cmd: Any, *, check: bool = False, **_kw: Any
        ) -> subprocess.CompletedProcess[str]:
            argv = list(cmd)
            calls.append(argv)
            rc = 0
            if argv[:2] == ["launchctl", "bootstrap"]:
                rc = rcs.pop(0) if rcs else 0
            if check and rc != 0:
                raise subprocess.CalledProcessError(rc, argv)
            return subprocess.CompletedProcess(
                argv, rc, stdout="", stderr=("boom" if rc else "")
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
        seq = iter([] if loaded_seq is None else loaded_seq)
        monkeypatch.setattr(
            launchd, "_is_loaded", lambda _lbl: next(seq, False)
        )
        return sched, calls

    @staticmethod
    def _subs(calls: list[list[str]]) -> list[str]:
        return [c[1] for c in calls if c and c[0] == "launchctl"]

    def test_active_reload_boots_out_then_bootstraps(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        sched, calls = self._setup(monkeypatch, tmp_path)
        sched.activate("default.j", prior_disabled=False, scheduled=True)
        assert self._subs(calls) == ["bootout", "bootstrap"]
        # No deprecated load/unload anywhere.
        assert "load" not in self._subs(calls)
        assert "unload" not in self._subs(calls)

    def test_disabled_reload_never_bootstraps(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        sched, calls = self._setup(monkeypatch, tmp_path)
        sched.activate("default.j", prior_disabled=True, scheduled=True)
        subs = self._subs(calls)
        assert "bootstrap" not in subs
        assert subs == ["bootout", "disable"]

    def test_enable_enables_then_bootstraps(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        sched, calls = self._setup(monkeypatch, tmp_path)
        sched.enable("default.j")
        assert self._subs(calls) == ["enable", "bootout", "bootstrap"]

    def test_disable_boots_out_then_disables(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        sched, calls = self._setup(monkeypatch, tmp_path)
        sched.disable("default.j")
        assert self._subs(calls) == ["bootout", "disable"]

    def test_bootstrap_retries_spurious_eio(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        # First bootstrap returns errno 5, second succeeds: the reload
        # re-settles and retries rather than surfacing the race.
        sched, calls = self._setup(monkeypatch, tmp_path, bootstrap_rcs=(5, 0))
        sched.activate("default.j", prior_disabled=False, scheduled=True)
        assert self._subs(calls).count("bootstrap") == 2
        assert self._subs(calls).count("bootout") == 2

    def test_bootstrap_raises_after_exhausting_retries(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        sched, calls = self._setup(
            monkeypatch, tmp_path, bootstrap_rcs=(5, 5, 5)
        )
        with pytest.raises(subprocess.CalledProcessError):
            sched.activate("default.j", prior_disabled=False, scheduled=True)
        assert (
            self._subs(calls).count("bootstrap") == launchd._BOOTSTRAP_ATTEMPTS
        )

    def test_genuine_failure_raises_without_retry(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        # A non-errno-5 bootstrap failure is genuine, not the race, so it
        # surfaces at once rather than burning the retry budget.
        sched, calls = self._setup(monkeypatch, tmp_path, bootstrap_rcs=(1,))
        with pytest.raises(subprocess.CalledProcessError):
            sched.activate("default.j", prior_disabled=False, scheduled=True)
        assert self._subs(calls).count("bootstrap") == 1

    def test_settle_waits_for_label_to_clear(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        # _is_loaded reports the label still present twice, then gone:
        # the bootstrap waits for it rather than racing the teardown.
        polled: list[int] = []
        seq = [True, True, False]

        def is_loaded(_lbl: str) -> bool:
            polled.append(1)
            return seq[len(polled) - 1] if len(polled) <= len(seq) else False

        sched = get_scheduler("darwin", tmp_path)
        (tmp_path / launchd.plist_filename("default.j")).write_text("x")
        calls: list[list[str]] = []

        def fake_run(cmd: Any, **_k: Any) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            return subprocess.CompletedProcess(list(cmd), 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
        monkeypatch.setattr(launchd, "_is_loaded", is_loaded)
        sched.activate("default.j", prior_disabled=False, scheduled=True)
        # Polled until the label cleared, then bootstrapped.
        assert len(polled) >= 3
        assert self._subs(calls) == ["bootout", "bootstrap"]

    def test_settle_is_bounded_when_label_never_clears(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        # A label that never deregisters must not hang the reload: the
        # settle is time-bounded and bootstrap still runs.
        sched, calls = self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(launchd, "_is_loaded", lambda _lbl: True)
        # Each monotonic() jumps well past the settle timeout, so the
        # bounded wait gives up immediately and bootstrap still runs.
        elapsed = [0.0]

        def fake_monotonic() -> float:
            elapsed[0] += 100.0
            return elapsed[0]

        monkeypatch.setattr(time, "monotonic", fake_monotonic)
        sched.activate("default.j", prior_disabled=False, scheduled=True)
        assert "bootstrap" in self._subs(calls)


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
