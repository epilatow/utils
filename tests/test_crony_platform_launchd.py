#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for crony.platform.launchd (the macOS backend)."""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.platform import get_scheduler, launchd  # noqa: E402
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
# Absolute paths are normally resolved live at apply time; the renderers
# take them as explicit args, so the tests pin deterministic values.
_UV = Path("/abs/uv")
_CRONY = Path("/abs/crony")
# render() / dispatch don't read the unit dir; pin a placeholder.
_DIR = Path("/unused")


class TestPlistRendering:
    """launchd.render_plist produces well-formed launchd plists."""

    def test_keyword_daily(self) -> None:
        plist = launchd.render_plist(
            "brew",
            _REF,
            Schedule.from_str("daily"),
            uv_path=_UV,
            crony_path=_CRONY,
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
            _REF,
            Schedule.from_str("*-*-* 03:15"),
            uv_path=_UV,
            crony_path=_CRONY,
        )
        assert "<key>Hour</key>" in plist
        assert "<integer>3</integer>" in plist
        assert "<integer>15</integer>" in plist

    def test_oncalendar_dow_with_time(self) -> None:
        plist = launchd.render_plist(
            "j",
            _REF,
            Schedule.from_str("Mon *-*-* 09:00"),
            uv_path=_UV,
            crony_path=_CRONY,
        )
        assert "<key>Weekday</key>" in plist
        assert "<integer>1</integer>" in plist  # Mon=1

    def test_oncalendar_first_of_month(self) -> None:
        plist = launchd.render_plist(
            "j",
            _REF,
            Schedule.from_str("*-*-01 03:00"),
            uv_path=_UV,
            crony_path=_CRONY,
        )
        assert "<key>Day</key>" in plist
        assert "<integer>1</integer>" in plist

    def test_interval(self) -> None:
        plist = launchd.render_plist(
            "j",
            _REF,
            Interval.from_str("30min"),
            uv_path=_UV,
            crony_path=_CRONY,
        )
        assert "<key>StartInterval</key>" in plist
        assert "<integer>1800</integer>" in plist

    def test_program_args_invoke_uv_with_absolute_path(self) -> None:
        # launchd's per-agent PATH omits uv, so the shebang's `env`
        # lookup fails (exit 127). Render uv's absolute path into
        # ProgramArguments so the unit doesn't depend on PATH.
        plist = launchd.render_plist(
            "j",
            _REF,
            Schedule.from_str("daily"),
            uv_path=_UV,
            crony_path=_CRONY,
        )
        assert "<string>/abs/uv</string>" in plist
        assert "<string>run</string>" in plist
        assert "<string>--script</string>" in plist
        assert "<string>/abs/crony</string>" in plist
        # The runner argv carries the bundle:uuid ref, not the name.
        assert "<string>default:u-test</string>" in plist

    def test_every_shape_is_a_valid_plist(self) -> None:
        # Each rendered plist must parse back as a well-formed plist
        # (plutil only runs at apply on macOS; this catches structural
        # breakage in CI on any platform).
        shapes: list[
            tuple[Schedule | Interval | None, PriorityClass | None]
        ] = [
            (Schedule.from_str("daily"), None),
            (Interval.from_str("30min"), None),
            (Schedule.from_str("Mon *-*-* 09:00"), PriorityClass.HIGH),
            (Schedule.from_str("*-*-* 03:00"), PriorityClass.LOW),
            (None, None),  # on-demand, normal priority
        ]
        for timing, priority in shapes:
            plist = launchd.render_plist(
                "j", _REF, timing, priority, uv_path=_UV, crony_path=_CRONY
            )
            d = plistlib.loads(plist.encode("utf-8"))
            assert d["Label"] == "org.crony.j"
            assert d["ProgramArguments"][1] == "run"


class TestLaunchdPriority:
    def test_high(self) -> None:
        plist = launchd.render_plist(
            "j",
            _REF,
            Schedule.from_str("daily"),
            PriorityClass.HIGH,
            uv_path=_UV,
            crony_path=_CRONY,
        )
        d = plistlib.loads(plist.encode("utf-8"))
        assert d["ProcessType"] == "Interactive"
        assert d["LowPriorityIO"] is False
        assert d["Nice"] == 0

    def test_low(self) -> None:
        plist = launchd.render_plist(
            "j",
            _REF,
            Schedule.from_str("daily"),
            PriorityClass.LOW,
            uv_path=_UV,
            crony_path=_CRONY,
        )
        d = plistlib.loads(plist.encode("utf-8"))
        assert d["ProcessType"] == "Background"
        assert d["LowPriorityIO"] is True
        assert d["Nice"] == 10

    def test_normal_and_none_emit_nothing(self) -> None:
        for p in (PriorityClass.NORMAL, None):
            plist = launchd.render_plist(
                "j",
                _REF,
                Schedule.from_str("daily"),
                p,
                uv_path=_UV,
                crony_path=_CRONY,
            )
            d = plistlib.loads(plist.encode("utf-8"))
            assert "ProcessType" not in d
            assert "LowPriorityIO" not in d
            assert "Nice" not in d
        assert launchd._priority_keys(PriorityClass.NORMAL) == {}
        assert launchd._priority_keys(None) == {}


class TestLaunchdScheduler:
    def test_render_one_plist(self) -> None:
        spec = UnitSpec(
            name=EntityName.from_str("default.brew"),
            ref=_REF,
            timing=Schedule.from_str("daily"),
            priority=None,
        )
        units = get_scheduler("darwin", _DIR).render(
            spec, uv_path=_UV, crony_path=_CRONY
        )
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


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
