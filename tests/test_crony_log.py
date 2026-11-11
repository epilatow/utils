#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15,<3.16"
# dependencies = ["pytest", "pytest-cov"]
# ///
# This is AI generated code

"""Message prefixes and separation from command output in combined logs."""

import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from crony.log import write_line  # noqa: E402

_script_path = REPO_ROOT / "src" / "crony" / "log.py"


def test_multiline_messages_prefix_every_line() -> None:
    log = io.BytesIO()
    write_line(log, "first\n\nsecond\n")
    assert log.getvalue() == b"> crony: first\n> crony: \n> crony: second\n"


@pytest.mark.parametrize("output", [b"", b"progress", b"done\n"])
def test_messages_start_after_command_output(
    tmp_path: Path, output: bytes
) -> None:
    path = tmp_path / "run.log"
    path.write_bytes(output)
    with path.open("ab", buffering=0) as log:
        write_line(log, "next", log_path=path)
    lead = b"\n" if output and not output.endswith(b"\n") else b""
    assert path.read_bytes() == output + lead + b"> crony: next\n"


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
