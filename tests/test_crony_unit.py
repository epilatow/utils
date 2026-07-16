#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for crony.unit (the platform-neutral value objects)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.unit import (  # noqa: E402
    ON_DEMAND_SPEC,
    EntityName,
    EntityRef,
    Interval,
    JitterSpec,
    OnDemand,
    PriorityClass,
    Schedule,
    UnitSpec,
    name_is_dotted_prefix,
)

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "src" / "crony" / "unit.py"

_UUID = "12345678-9abc-def0-1234-56789abcdef0"


class TestNameIsDottedPrefix:
    @pytest.mark.parametrize(
        ("prefix", "name"),
        [
            ("foo", "foo.bar"),
            ("foo", "foo.bar.baz"),
            ("foo.bar", "foo.bar.baz"),
        ],
    )
    def test_proper_dotted_prefix(self, prefix: str, name: str) -> None:
        assert name_is_dotted_prefix(prefix, name)

    @pytest.mark.parametrize(
        ("prefix", "name"),
        [
            ("foo", "foo"),  # equal, not a proper prefix
            ("foo", "foobar"),  # string-prefix, no dot boundary
            ("foo.a", "foo.ab"),  # dotted string-prefix, no dot boundary
            ("foo.bar", "foo.baz"),  # unrelated
            ("foo.bar", "foo"),  # reversed
        ],
    )
    def test_not_a_dotted_prefix(self, prefix: str, name: str) -> None:
        assert not name_is_dotted_prefix(prefix, name)


class TestEntityRef:
    def test_round_trips(self) -> None:
        ref = EntityRef.from_str(f"borgadm:{_UUID}")
        assert ref == EntityRef("borgadm", _UUID)
        assert str(ref) == f"borgadm:{_UUID}"

    def test_is_hashable(self) -> None:
        ref = EntityRef("default", _UUID)
        assert {ref: 1}[EntityRef("default", _UUID)] == 1

    @pytest.mark.parametrize(
        "arg",
        [
            "no-colon",
            ":only-uuid",
            "bundle:",
            "bad.bundle:" + _UUID,
            "ok:not-a-uuid",
            f"ok:{_UUID.upper()}",  # non-canonical (uppercase)
        ],
    )
    def test_rejects(self, arg: str) -> None:
        assert EntityRef.from_str(arg) is None


class TestEntityName:
    def test_round_trips(self) -> None:
        name = EntityName.from_str("borgadm.prune")
        assert name == EntityName("borgadm", "prune")
        assert str(name) == "borgadm.prune"

    def test_short_keeps_dots(self) -> None:
        # Split on the first dot only.
        assert EntityName.from_str("a.b.c") == EntityName("a", "b.c")

    def test_is_hashable(self) -> None:
        assert {EntityName("d", "x"): 1}[EntityName("d", "x")] == 1

    @pytest.mark.parametrize(
        "arg",
        ["bare", ".leading", "trailing.", "bad bundle.x", "ok.-bad short"],
    )
    def test_rejects(self, arg: str) -> None:
        with pytest.raises(ValueError):
            EntityName.from_str(arg)


class TestPriorityClass:
    def test_from_str_and_str(self) -> None:
        assert PriorityClass.from_str("high") is PriorityClass.HIGH
        assert str(PriorityClass.LOW) == "low"

    def test_rejects_unknown(self) -> None:
        with pytest.raises(ValueError):
            PriorityClass.from_str("urgent")


class TestInterval:
    @pytest.mark.parametrize(
        ("spec", "seconds"),
        [
            ("30min", 1800),
            ("1h30min", 5400),
            ("90 seconds", 90),
            ("2h", 7200),
            ("2h 15m", 8100),
            ("1d", 86400),
            ("60s", 60),
            ("1month", 2592000),
            ("1year", 31536000),
        ],
    )
    def test_parses_to_seconds_and_keeps_source(
        self, spec: str, seconds: int
    ) -> None:
        iv = Interval.from_str(spec)
        assert iv.total_seconds == seconds
        # str() round-trips the spec as written, not a canonical form.
        assert str(iv) == spec

    def test_minutes_vs_months(self) -> None:
        # lowercase m is minutes, capital M is months.
        assert Interval.from_str("5m").total_seconds == 300
        assert Interval.from_str("5M").total_seconds == 5 * 2592000

    @pytest.mark.parametrize("spec", ["", "   ", "abc", "0s", "5x"])
    def test_rejects(self, spec: str) -> None:
        with pytest.raises(ValueError):
            Interval.from_str(spec)


class TestSchedule:
    @pytest.mark.parametrize(
        ("spec", "calendar"),
        [
            ("hourly", {"Minute": 0}),
            ("daily", {"Minute": 0, "Hour": 0}),
            ("weekly", {"Minute": 0, "Hour": 0, "Weekday": 1}),
            ("monthly", {"Minute": 0, "Hour": 0, "Day": 1}),
            ("yearly", {"Minute": 0, "Hour": 0, "Day": 1, "Month": 1}),
            ("03:15", {"Minute": 15, "Hour": 3}),
            ("Mon *-*-* 09:00", {"Minute": 0, "Hour": 9, "Weekday": 1}),
            (
                "*-12-25 06:30",
                {"Minute": 30, "Hour": 6, "Day": 25, "Month": 12},
            ),
        ],
    )
    def test_to_plist_calendar(
        self, spec: str, calendar: dict[str, int]
    ) -> None:
        assert Schedule.from_str(spec).to_plist_calendar() == calendar

    @pytest.mark.parametrize(
        "spec",
        ["hourly", "daily", "weekly", "03:15", "Mon *-*-* 09:00", "Sun 23:59"],
    )
    def test_str_preserves_source(self, spec: str) -> None:
        # The spec is kept as written, not canonicalized.
        assert str(Schedule.from_str(spec)) == spec

    def test_year_and_second_kept_in_str_dropped_in_plist(self) -> None:
        sched = Schedule.from_str("2030-01-02 05:06:07")
        assert str(sched) == "2030-01-02 05:06:07"
        # launchd has no Year or Second field.
        assert sched.to_plist_calendar() == {
            "Minute": 6,
            "Hour": 5,
            "Day": 2,
            "Month": 1,
        }

    @pytest.mark.parametrize(
        "spec",
        [
            "",
            "  ",
            "two\nlines",
            "*:0/15",  # step
            "Mon..Fri 09:00",  # range
            "1,2 09:00",  # list
            "foo",  # no time component
            "*",  # typo: no time component
            "1234",  # typo: no time component
            "*-*-*",  # date with no time
            "25:00",  # hour out of range
        ],
    )
    def test_rejects(self, spec: str) -> None:
        with pytest.raises(ValueError):
            Schedule.from_str(spec)


class TestOnDemand:
    def test_str_is_the_spec_token(self) -> None:
        assert str(OnDemand()) == ON_DEMAND_SPEC == "on-demand"

    def test_instances_are_equal(self) -> None:
        # No fields, so every instance compares equal -- it behaves as a
        # singleton firing-mode marker.
        assert OnDemand() == OnDemand()

    def test_not_confused_with_a_schedule(self) -> None:
        assert not isinstance(OnDemand(), (Schedule, Interval))


class TestUnitSpec:
    def test_construction(self) -> None:
        cmd = ("/abs/uv", "run", "--script", "/abs/crony", "run", "x:y")
        spec = UnitSpec(
            name=EntityName("default", "job"),
            cmd=cmd,
            timing=Schedule.from_str("daily"),
            priority=PriorityClass.NORMAL,
        )
        assert str(spec.name) == "default.job"
        assert spec.cmd == cmd
        assert isinstance(spec.timing, Schedule)

    def test_jitter_defaults_none(self) -> None:
        # A unit is unjittered unless the model stamps a JitterSpec.
        spec = UnitSpec(
            name=EntityName("default", "job"),
            cmd=(),
            timing=None,
            priority=PriorityClass.NORMAL,
        )
        assert spec.jitter is None

    def test_carries_jitter_spec(self) -> None:
        jitter = JitterSpec(
            offset=Interval("90s", 90), cmd=("uv", "crony", "_jitter")
        )
        spec = UnitSpec(
            name=EntityName("default", "job"),
            cmd=(),
            timing=Interval.from_str("1h"),
            priority=PriorityClass.NORMAL,
            jitter=jitter,
        )
        assert spec.jitter is jitter
        assert spec.jitter.offset.total_seconds == 90


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
