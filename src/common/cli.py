"""Shared decorator for utility CLI entry points.

Each utility's ``cli()`` (or ``main()``) handles its own domain errors
inline and returns an exit code. ``cli_entrypoint`` wraps that entry
point to convert the two cases every utility treats identically -- a
Ctrl-C and an otherwise-unhandled exception -- into stable exit codes,
so neither escapes to a raw traceback a scheduler can't interpret.
"""

from __future__ import annotations

import functools
import sys
import traceback
from collections.abc import Callable
from typing import ParamSpec

from common.exitcodes import SIGINT_EXIT_CODE, CommonExitCode

_P = ParamSpec("_P")


def cli_entrypoint(fn: Callable[_P, int]) -> Callable[_P, int]:
    """Wrap a CLI entry point so an uncaught Ctrl-C or unexpected
    exception becomes a stable exit code instead of a traceback.

    A ``KeyboardInterrupt`` returns the shell's SIGINT convention
    (128 + SIGINT); any other exception that escapes the entry point's
    own handlers prints a traceback and returns the dedicated crash
    code. Domain errors are caught inside each entry point and never
    reach here.
    """

    @functools.wraps(fn)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> int:
        try:
            return fn(*args, **kwargs)
        except KeyboardInterrupt:
            # Ctrl-C: exit on the SIGINT convention without a traceback.
            # The newline lands the shell prompt below the echoed `^C`.
            print(file=sys.stderr)
            return SIGINT_EXIT_CODE
        except Exception:
            # Reaching here is a bug -- an exception the entry point
            # should have caught. Surface the traceback and return the
            # crash code, distinct from a deliberate ERROR, rather than
            # leaving a scheduler with no usable exit status. The
            # decorator is utility-agnostic, so it returns the raw int
            # rather than any one utility's `ExitCode.CRASHED`.
            traceback.print_exc()
            return CommonExitCode.CRASHED[0]

    return wrapper
