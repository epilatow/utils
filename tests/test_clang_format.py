#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Every Applications/*.app wrapper's C source stays clang-format clean.

The Mach-O wrappers under `Applications/` (BorgAdm.c, Crony.c) are kept
formatted to the repo-root `.clang-format`, so a checked-in source always
matches what `xcrun clang-format -i` would produce. clang-format ships in
the Xcode toolchain and is reached through `xcrun`; where it is
unavailable (no Xcode), the check skips.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
_script_path = Path(__file__)

_WRAPPER_SOURCES = sorted(REPO_ROOT.glob("Applications/*/Contents/MacOS/*.c"))


@pytest.mark.skipif(
    shutil.which("xcrun") is None,
    reason="clang-format is reached through xcrun (Xcode toolchain)",
)
@pytest.mark.parametrize("source", _WRAPPER_SOURCES, ids=lambda p: p.name)
def test_wrapper_c_is_clang_format_clean(source: Path) -> None:
    result = subprocess.run(
        ["xcrun", "clang-format", "--style=file", str(source)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    rel = source.relative_to(REPO_ROOT)
    assert result.stdout == source.read_text(), (
        f"{rel} is not clang-format clean; run `xcrun clang-format -i {rel}`"
    )


def test_wrapper_sources_discovered() -> None:
    # Guard the glob: a layout change that hides every wrapper source
    # would make the format gate silently pass by covering nothing.
    assert _WRAPPER_SOURCES, "no Applications/*.app wrapper C sources found"


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
