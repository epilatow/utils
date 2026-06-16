"""Shared base + canonical common codes for the utilities' ExitCode enums.

Each utility defines its own ``ExitCode(ExitCodeBase)``. The base
supplies the ``(value, description)`` unpacking and ``epilog()`` (which
renders an argparse exit-codes block from whatever members the subclass
declares), while ``CommonExitCode`` is the single source of truth for
the codes shared by every utility: a subclass references those for its
common members and adds its own codes starting at 10. Values 0-9 are
reserved -- 0-7 in use, 8-9 held for future common codes -- so utility
specifics never collide with a later shared code. SUCCESS..SUBPROCESS
and CRASHED are carried by every utility; the rest (e.g. TIMEOUT) are
used only where they apply.
"""

from __future__ import annotations

import enum
import signal
from collections.abc import Container
from typing import Self

# Exit status a CLI returns when interrupted by Ctrl-C: the shell
# convention of 128 + the signal number, so `$?` reads as a SIGINT
# death. The shared cli_entrypoint decorator catches KeyboardInterrupt
# and returns this rather than letting it escape to a traceback. Not an
# ExitCode member -- it is the signal convention, not an application
# status, so it stays out of the per-tool exit-code listing and the
# exception map.
SIGINT_EXIT_CODE: int = 128 + int(signal.SIGINT)


class ExitCodeBase(enum.IntEnum):
    """IntEnum base whose members are ``(value, description)`` pairs.

    Subclasses assign members as tuples (e.g. ``SUCCESS = 0,
    "Success"``). The base itself has no members, which is what lets it
    be subclassed -- an enum that already has members cannot be.
    """

    def __new__(cls, value: int, description: str = "") -> Self:
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.description = description  # type: ignore[attr-defined]
        return obj

    @classmethod
    def entries(cls) -> list[tuple[int, str]]:
        """The members as `(value, description)` pairs, in definition
        order. The typed view of the codes, for callers that render them
        themselves (e.g. a man-page generator) rather than via
        `epilog()`."""
        return [
            (member.value, member.description)  # type: ignore[attr-defined]
            for member in cls
        ]

    @classmethod
    def epilog(cls, exclude: Container[int] = ()) -> str:
        """The argparse exit-status block. `exclude` drops codes a
        utility treats as internal (never surfaced to users), mirroring
        whatever filtering its user-facing docs apply."""
        lines = ["Exit Status:"]
        for value, description in cls.entries():
            if value in exclude:
                continue
            lines.append(f"  {value}  {description}")
        return "\n".join(lines)


class CommonExitCode:
    """Canonical ``(value, description)`` pairs shared by every utility.

    A utility's ``ExitCode`` references these for its common members
    (e.g. ``SUCCESS = CommonExitCode.SUCCESS``) rather than re-spelling
    the value and text, so the shared codes can't drift. Not an enum
    itself -- just the data each ExitCode subclass pulls from.
    """

    SUCCESS = (0, "Success")
    WARNING = (1, "Warning")
    USAGE = (2, "Usage/argument error")
    CONFIG = (3, "Configuration error")
    ERROR = (4, "General error")
    SUBPROCESS = (5, "Subprocess error")
    TIMEOUT = (6, "Operation timed out")
    CRASHED = (7, "Crashed (unhandled exception)")
