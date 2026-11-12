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

from crony.log import write_header, write_line  # noqa: E402

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


@pytest.mark.parametrize("output", [b"", b"progress", b"done\n"])
def test_delimiters_follow_a_blank_line_without_a_prefix(
    tmp_path: Path, output: bytes
) -> None:
    path = tmp_path / "run.log"
    path.write_bytes(output)
    header = (
        "=== 2026-11-12T15:10:52-08:00 default.job timeout=1800s pid=42 ==="
    )
    with path.open("ab", buffering=0) as log:
        write_header(log, header, log_path=path)
        write_line(log, "exit status: 0; runtime: 00:00:05", log_path=path)
    lead = b"\n" if output and not output.endswith(b"\n") else b""
    assert path.read_bytes() == (
        output
        + lead
        + f"\n{header}\n".encode()
        + b"> crony: exit status: 0; runtime: 00:00:05\n"
    )


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
