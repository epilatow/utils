#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for common.exitcodes (shared by every bin/ utility)."""

from __future__ import annotations

import enum
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.exitcodes import CommonExitCode, ExitCodeBase  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "src" / "common" / "exitcodes.py"


class _SampleExit(ExitCodeBase):
    OK = 0, "All good"
    WARN = 1, "A warning"
    BOOM = 4, "Boom"


class TestExitCodeBase:
    def test_base_has_no_members(self) -> None:
        # The base must stay members-less so subclasses can add their own
        # (a populated enum cannot be subclassed).
        assert list(ExitCodeBase) == []

    def test_subclass_unpacks_value_and_description(self) -> None:
        assert int(_SampleExit.OK) == 0
        assert int(_SampleExit.BOOM) == 4
        assert _SampleExit.OK.description == "All good"  # type: ignore[attr-defined]
        assert isinstance(_SampleExit.OK, enum.IntEnum)

    def test_entries_are_value_description_pairs(self) -> None:
        assert _SampleExit.entries() == [
            (0, "All good"),
            (1, "A warning"),
            (4, "Boom"),
        ]

    def test_epilog_lists_every_member(self) -> None:
        epilog = _SampleExit.epilog()
        assert epilog.startswith("Exit Status:")
        assert "0  All good" in epilog
        assert "1  A warning" in epilog
        assert "4  Boom" in epilog

    def test_epilog_excludes_given_codes(self) -> None:
        # A utility hides its internal codes from the user-facing block.
        epilog = _SampleExit.epilog(exclude={4})
        assert "0  All good" in epilog
        assert "1  A warning" in epilog
        assert "Boom" not in epilog


class _RefExit(ExitCodeBase):
    SUCCESS = CommonExitCode.SUCCESS
    TIMEOUT = CommonExitCode.TIMEOUT
    SPECIFIC = 10, "Specific"


class TestCommonExitCode:
    def test_canonical_values(self) -> None:
        assert CommonExitCode.SUCCESS == (0, "Success")
        assert CommonExitCode.USAGE == (2, "Usage/argument error")
        assert CommonExitCode.TIMEOUT == (6, "Operation timed out")
        assert CommonExitCode.CRASHED == (7, "Crashed (unhandled exception)")

    def test_codes_distinct_and_within_reserved_range(self) -> None:
        values = [
            getattr(CommonExitCode, name)[0]
            for name in vars(CommonExitCode)
            if not name.startswith("_")
        ]
        assert len(values) == len(set(values))
        assert all(0 <= v <= 9 for v in values)

    def test_referenced_codes_resolve(self) -> None:
        # A subclass that pulls members from CommonExitCode gets the
        # canonical value + description; its own codes start at 10.
        assert int(_RefExit.SUCCESS) == 0
        assert int(_RefExit.TIMEOUT) == 6
        assert _RefExit.TIMEOUT.description == "Operation timed out"  # type: ignore[attr-defined]
        assert int(_RefExit.SPECIFIC) == 10


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
