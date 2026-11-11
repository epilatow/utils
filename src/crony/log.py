# This is AI generated code

"""Crony's own messages in a job's combined output log."""

import os
import re
from pathlib import Path
from typing import IO

RUN_HEADER_RE = re.compile(r"^(?:> crony: )?=== (\S+) ", re.MULTILINE)


def write_line(
    log_file: IO[bytes], text: str, *, log_path: Path | None = None
) -> None:
    """Prefix every message line, separating it from partial command output.

    Supply log_path when the command may have written through the append-only
    handle. Reading its last byte keeps a note off the command's final line.
    """
    lead = b""
    if log_path is not None:
        with open(log_path, "rb") as reader:
            if reader.seek(0, os.SEEK_END) > 0:
                reader.seek(-1, os.SEEK_END)
                lead = b"" if reader.read(1) == b"\n" else b"\n"
    lines = text.splitlines() or [""]
    log_file.write(
        lead + "".join(f"> crony: {line}\n" for line in lines).encode()
    )
