"""Shared base + canonical common codes for the utilities' ExitCode enums.

Each utility defines its own ``ExitCode(ExitCodeBase)``. The base
supplies the ``(value, description)`` unpacking and ``epilog()`` (which
renders an argparse exit-codes block from whatever members the subclass
declares), while ``CommonExitCode`` is the single source of truth for
the codes shared by every utility: a subclass references those for its
common members and adds its own codes starting at 10. Values 0-9 are
reserved -- 0-6 in use, 7-9 held for future common codes -- so utility
specifics never collide with a later shared code. SUCCESS..SUBPROCESS
are carried by every utility; the rest (e.g. TIMEOUT) are used only
where they apply.
"""

from __future__ import annotations

import enum
from typing import Self


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
    def epilog(cls) -> str:
        lines = ["exit codes:"]
        for member in cls:
            lines.append(
                f"  {member.value}  {member.description}"  # type: ignore[attr-defined]
            )
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
