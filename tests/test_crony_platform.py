#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for crony.platform's package-level surface (the
get_scheduler factory). Backend-specific tests live in
test_crony_platform_launchd.py / test_crony_platform_systemd.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.platform import get_scheduler  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "src" / "crony" / "platform" / "__init__.py"


class TestGetScheduler:
    def test_unsupported_platform_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported platform"):
            get_scheduler("plan9")


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
