#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for common.argparse_ext (shared by every bin/ utility)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.argparse_ext import (  # noqa: E402
    StrictArgumentParser,
    StrictSubParsersAction,
    action_subparsers,
)

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "src" / "common" / "argparse_ext.py"


def _parser_with_command() -> StrictArgumentParser:
    p = StrictArgumentParser(prog="tool")
    sub = p.add_subparsers(dest="command")
    cmd = sub.add_parser("cmd")
    cmd.add_argument("--flag", action="store_true")
    return p


class TestStrictArgumentParser:
    def test_installs_strict_subparsers_action(self) -> None:
        p = StrictArgumentParser(prog="tool")
        sub = p.add_subparsers(dest="command")
        assert isinstance(sub, StrictSubParsersAction)

    def test_subparser_error_routed_to_subcommand(self, capsys: Any) -> None:
        # A bad arg under `cmd` is reported as "tool cmd: error: ..."
        # (the subcommand), not by the root parser.
        p = _parser_with_command()
        with pytest.raises(SystemExit) as exc:
            p.parse_args(["cmd", "--bogus"])
        assert exc.value.code == 2
        assert "tool cmd:" in capsys.readouterr().err

    def test_valid_args_parse(self) -> None:
        p = _parser_with_command()
        args = p.parse_args(["cmd", "--flag"])
        assert args.command == "cmd"
        assert args.flag is True

    def test_strict_action_inherited_by_nested_subparsers(
        self, capsys: Any
    ) -> None:
        # add_subparsers defaults child parser_class to type(self), so a
        # nested group routes errors to the deepest subcommand.
        p = StrictArgumentParser(prog="tool")
        sub = p.add_subparsers(dest="command")
        grp = sub.add_parser("group")
        nested = grp.add_subparsers(dest="action")
        nested.add_parser("do")
        with pytest.raises(SystemExit):
            p.parse_args(["group", "do", "--bogus"])
        assert "tool group do:" in capsys.readouterr().err


class TestActionSubparsers:
    @staticmethod
    def _parser() -> StrictArgumentParser:
        p = StrictArgumentParser(prog="tool")
        sub = p.add_subparsers(dest="command")
        grp = sub.add_parser("group")
        nested = action_subparsers(grp)
        nested.add_parser("do")
        return p

    def test_missing_action_registers_help(self) -> None:
        # Omitting the action leaves action=None and a callable
        # _action_help (the subcommand's print_help) for cli to invoke.
        p = self._parser()
        args = p.parse_args(["group"])
        assert getattr(args, "action", None) is None
        assert callable(getattr(args, "_action_help", None))

    def test_present_action_dispatches(self) -> None:
        p = self._parser()
        args = p.parse_args(["group", "do"])
        assert args.action == "do"

    def test_unknown_action_errors(self, capsys: Any) -> None:
        p = self._parser()
        with pytest.raises(SystemExit):
            p.parse_args(["group", "bogus"])
        assert "bogus" in capsys.readouterr().err


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
