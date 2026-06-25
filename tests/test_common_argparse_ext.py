#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for common.argparse_ext (shared by every bin/ utility)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.argparse_ext import (  # noqa: E402
    StrictArgumentParser,
    StrictSubParsersAction,
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


class TestCommandSubparsers:
    @staticmethod
    def _three_level() -> StrictArgumentParser:
        # `tool single` and `tool group do [--flag]`.
        p = StrictArgumentParser(prog="tool")
        top = p.add_command_subparsers()
        top.add_parser("single")
        grp = top.add_parser("group")
        mid = grp.add_command_subparsers()
        leaf = mid.add_parser("do")
        leaf.add_argument("--flag", action="store_true")
        return p

    def test_numbered_dests_auto_increment(self) -> None:
        # Each add_command_subparsers level lands under its own
        # cmd<level> dest without the consumer numbering anything.
        p = self._three_level()
        raw = p.parse_args(["group", "do"])
        assert raw.cmd1 == "group"
        assert raw.cmd2 == "do"

    def test_parse_command_collapses_single_level(self) -> None:
        p = self._three_level()
        args = p.parse_command(["single"])
        assert args.command == "single"
        assert not hasattr(args, "cmd1")

    def test_parse_command_collapses_three_levels(self) -> None:
        p = self._three_level()
        args = p.parse_command(["group", "do", "--flag"])
        assert args.command == "group do"
        assert args.flag is True
        assert not hasattr(args, "_action_help")

    def test_missing_command_prints_root_help(self, capsys: Any) -> None:
        p = self._three_level()
        with pytest.raises(SystemExit) as exc:
            p.parse_command([])
        assert exc.value.code == 2
        assert "usage: tool" in capsys.readouterr().out

    def test_missing_action_prints_subcommand_help(self, capsys: Any) -> None:
        # Entering `group` without its action prints group's own help,
        # not the root's.
        p = self._three_level()
        with pytest.raises(SystemExit) as exc:
            p.parse_command(["group"])
        assert exc.value.code == 2
        assert "usage: tool group" in capsys.readouterr().out

    def test_unknown_subcommand_errors(self, capsys: Any) -> None:
        p = self._three_level()
        with pytest.raises(SystemExit):
            p.parse_command(["bogus"])
        assert "bogus" in capsys.readouterr().err


class TestValidateHook:
    @staticmethod
    def _parser() -> StrictArgumentParser:
        # `tool cmd [--bad]`; the cmd registers a _validate that rejects
        # --bad as an illegal combination.
        p = StrictArgumentParser(prog="tool")
        top = p.add_command_subparsers()
        cmd = top.add_parser("cmd")
        cmd.add_argument("--bad", action="store_true")

        def _validate(
            parser: argparse.ArgumentParser, args: argparse.Namespace
        ) -> None:
            if args.bad:
                parser.error("--bad is not allowed")

        cmd.add_validate_callback(_validate)
        return p

    def test_bad_combo_routed_to_subcommand(self, capsys: Any) -> None:
        p = self._parser()
        with pytest.raises(SystemExit) as exc:
            p.parse_command(["cmd", "--bad"])
        assert exc.value.code == 2
        assert "tool cmd:" in capsys.readouterr().err

    def test_good_combo_passes(self) -> None:
        p = self._parser()
        args = p.parse_command(["cmd"])
        assert args.command == "cmd"

    def test_validate_stripped_from_namespace(self) -> None:
        p = self._parser()
        args = p.parse_command(["cmd"])
        assert not hasattr(args, "_validate")


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
