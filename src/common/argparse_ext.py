"""Shared argparse helpers for the repo's CLI utilities.

``StrictArgumentParser`` routes a subparser's argument errors through
that subparser (not the root), and ``action_subparsers`` makes an
omitted required action print the subcommand's own help instead of
argparse's terse "the following arguments are required" error.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    SubParsersActionBase = argparse._SubParsersAction[argparse.ArgumentParser]
else:
    SubParsersActionBase = argparse._SubParsersAction


class StrictSubParsersAction(SubParsersActionBase):
    """Subparsers action that errors on the subparser, not the root.

    Stdlib's _SubParsersAction.__call__ calls parse_known_args on the
    chosen subparser and stashes any leftover tokens on the top-level
    namespace, so an unknown-arg error is reported by the root parser
    -- with its program name and usage line, obscuring which
    subcommand the user actually typed. Calling parse_args (strict)
    on the subparser routes the error through the subparser's own
    error(), giving "<prog> <subcommand>: error: ..." with the
    subcommand's usage line.

    Stdlib's deprecation-warning branch
    (add_parser(deprecated=True), 3.13+) is intentionally omitted:
    no consumer uses deprecated=True, and the stdlib hook
    (parser._warning) is private and version-gated.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: Optional[str] = None,
    ) -> None:
        parser_name = values[0]
        arg_strings = values[1:]
        if self.dest is not argparse.SUPPRESS:
            setattr(namespace, self.dest, parser_name)
        try:
            subparser = self._name_parser_map[parser_name]
        except KeyError as exc:
            choices = ", ".join(self._name_parser_map)
            raise argparse.ArgumentError(
                self,
                f"unknown parser {parser_name!r} (choices: {choices})",
            ) from exc
        subnamespace = subparser.parse_args(arg_strings)
        for key, value in vars(subnamespace).items():
            setattr(namespace, key, value)


class StrictArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that installs the strict subparsers action.

    add_subparsers() defaults child parser_class to type(self), so
    nested subparsers inherit the strict action without per-call
    wiring.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.register("action", "parsers", StrictSubParsersAction)


def action_subparsers(
    parent: argparse.ArgumentParser, **kwargs: Any
) -> SubParsersActionBase:
    """Add an action-group subparsers to ``parent``, left non-required
    so that omitting the action prints the subcommand's own help (via
    the ``_action_help`` default registered here, which the consumer's
    ``cli`` invokes) rather than argparse's terse "the following
    arguments are required" error.
    """
    parent.set_defaults(_action_help=parent.print_help)
    return parent.add_subparsers(dest="action", **kwargs)
