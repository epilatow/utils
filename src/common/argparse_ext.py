"""Shared argparse helpers for the repo's CLI utilities.

``StrictArgumentParser`` routes a subparser's argument errors through
that subparser (not the root). Build a command tree with
``add_command_subparsers`` at each level and dispatch with
``parse_command``, which collapses the tree into a single space-joined
``command`` path (e.g. ``"config init"``) and prints the deepest
entered subparser's own help when a required level is omitted, instead
of argparse's terse "the following arguments are required" error.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any, cast

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

    # Command-tree depth assigned to parsers this action creates.
    # ``add_command_subparsers`` sets it to its own level + 1; the
    # class default covers a raw ``add_subparsers`` group that does not
    # take part in ``parse_command`` dispatch.
    _child_command_level: int = 1

    def __call__(
        self,
        _parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        _option_string: str | None = None,
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

    def add_parser(self, name: str, **kwargs: Any) -> StrictArgumentParser:
        # add_subparsers builds children from the owning parser's
        # class (StrictArgumentParser), so stamp the child's
        # command-tree depth here -- that is what lets a child know its
        # own level when it later calls add_command_subparsers.
        parser = cast(
            "StrictArgumentParser", super().add_parser(name, **kwargs)
        )
        parser._command_level = self._child_command_level
        return parser


class StrictArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that installs the strict subparsers action.

    add_subparsers() defaults child parser_class to type(self), so
    nested subparsers inherit the strict action without per-call
    wiring.
    """

    # Depth of this parser in the command tree; the root is level 1.
    # add_command_subparsers numbers its dest from this and stamps
    # children one deeper.
    _command_level: int = 1

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.register("action", "parsers", StrictSubParsersAction)

    def add_command_subparsers(self, **kwargs: Any) -> StrictSubParsersAction:
        """Add a command-tree subparsers group to this parser.

        The group is non-required, so omitting its subcommand leaves
        its numbered dest at ``None`` and records this parser's
        ``print_help`` as the ``_action_help`` default; ``parse_command``
        prints that help and exits 2 rather than raising argparse's
        terse required-argument error. Levels are numbered automatically
        (``cmd1``, ``cmd2``, ...), so nesting to any depth needs no
        per-level dest name.
        """
        self.set_defaults(_action_help=self.print_help)
        action = cast(
            StrictSubParsersAction,
            self.add_subparsers(dest=f"cmd{self._command_level}", **kwargs),
        )
        action._child_command_level = self._command_level + 1
        return action

    def parse_command(
        self, argv: list[str] | None = None
    ) -> argparse.Namespace:
        """Parse argv and collapse the numbered ``cmd<level>`` dests
        into a single space-joined ``command`` path, leaving only the
        leaf's own options on the namespace.

        A path that stops at a command-tree group without choosing its
        subcommand prints the deepest entered parser's help and exits 2
        -- the same exit code argparse uses for ``-h`` or a usage
        error, but with that subcommand's full help rather than a terse
        usage line. Invalid arguments / subcommands are still
        ``parse_args``'s usual usage error.
        """
        data = vars(self.parse_args(argv))
        action_help = data.pop("_action_help", None)
        path: list[str] = []
        level = 1
        while f"cmd{level}" in data:
            chosen = data.pop(f"cmd{level}")
            if chosen is None:
                help_fn = action_help or self.print_help
                help_fn()
                raise SystemExit(2)
            path.append(chosen)
            level += 1
        return argparse.Namespace(command=" ".join(path), **data)
