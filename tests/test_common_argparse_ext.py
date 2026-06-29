#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for common.argparse_ext (shared by every bin/ utility)."""

import argparse
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.argparse_ext import (  # noqa: E402
    DefaultsHelpFormatter,
    RawDescriptionDefaultsHelpFormatter,
    StrictArgumentParser,
    StrictSubParsersAction,
    add_argument_ext,
    default_help_suffix,
    get_extended_help,
    help_with_default,
    is_common,
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


class TestAddArgumentExt:
    """`add_argument_ext` stashes doc-renderer metadata on the action;
    plain `add_argument` leaves the defaults."""

    def test_extended_help_and_common_round_trip(self) -> None:
        action = add_argument_ext(
            argparse.ArgumentParser(),
            "--foo",
            action="store_true",
            common=True,
            help="terse",
            extended_help="The longer description.",
        )
        assert get_extended_help(action) == "The longer description."
        assert is_common(action) is True
        # argparse still sees only the terse help.
        assert action.help == "terse"

    def test_defaults_when_unset(self) -> None:
        action = add_argument_ext(argparse.ArgumentParser(), "--foo")
        assert get_extended_help(action) is None
        assert is_common(action) is False

    def test_plain_add_argument_has_no_metadata(self) -> None:
        action = argparse.ArgumentParser().add_argument("--foo")
        assert get_extended_help(action) is None
        assert is_common(action) is False


class TestDefaultDisplay:
    """default_help_suffix / help_with_default and the formatters."""

    def test_suffix_shows_meaningful_default(self) -> None:
        action = argparse.ArgumentParser().add_argument(
            "--fmt", default="netscape"
        )
        assert default_help_suffix(action) == " (default: netscape)"

    def test_suffix_shows_falsy_but_real_default(self) -> None:
        # 0 and "" are real defaults; only None/False/SUPPRESS are noise.
        action = argparse.ArgumentParser().add_argument("--n", default=0)
        assert default_help_suffix(action) == " (default: 0)"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"action": "store_true"},  # default False
            {},  # default None
            {"default": argparse.SUPPRESS},
        ],
    )
    def test_suffix_suppresses_noise_defaults(
        self, kwargs: dict[str, Any]
    ) -> None:
        action = argparse.ArgumentParser().add_argument("--x", **kwargs)
        assert default_help_suffix(action) == ""

    def test_suffix_skips_plain_required_positional(self) -> None:
        action = argparse.ArgumentParser().add_argument("name")
        assert default_help_suffix(action) == ""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"nargs": "*", "default": []},  # variadic positional
            {"default": ""},  # empty string
        ],
    )
    def test_suffix_suppresses_empty_collection_default(
        self, kwargs: dict[str, Any]
    ) -> None:
        name = "items" if "nargs" in kwargs else "--s"
        action = argparse.ArgumentParser().add_argument(name, **kwargs)
        assert default_help_suffix(action) == ""

    def test_suffix_contracts_home_to_tilde(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A home-derived default must not bake the absolute home into docs.
        monkeypatch.setenv("HOME", "/home/fake")
        action = argparse.ArgumentParser().add_argument(
            "--config", default="/home/fake/.borgadm"
        )
        assert default_help_suffix(action) == " (default: ~/.borgadm)"

    def test_suffix_leaves_non_home_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", "/home/fake")
        action = argparse.ArgumentParser().add_argument(
            "--config", default="/etc/borgadm"
        )
        assert default_help_suffix(action) == " (default: /etc/borgadm)"

    def test_help_with_default_appends(self) -> None:
        action = argparse.ArgumentParser().add_argument(
            "--fmt", default="json", help="Output format."
        )
        assert help_with_default(action) == "Output format. (default: json)"

    def test_help_with_default_left_alone_when_already_present(self) -> None:
        action = argparse.ArgumentParser().add_argument(
            "--p", default="x", help="Path (default: auto)."
        )
        assert help_with_default(action) == "Path (default: auto)."

    def test_formatter_escapes_percent_in_default(self) -> None:
        # argparse %-formats the help string; a literal % in the default
        # (e.g. a strftime pattern) must not be mis-parsed or crash --help.
        parser = argparse.ArgumentParser(
            prog="t", formatter_class=DefaultsHelpFormatter
        )
        parser.add_argument("--fmt", default="%Y-%m-%d", help="Date format.")
        assert "Date format. (default: %Y-%m-%d)" in parser.format_help()

    def test_formatter_shows_and_suppresses(self) -> None:
        parser = argparse.ArgumentParser(
            prog="t", formatter_class=DefaultsHelpFormatter
        )
        parser.add_argument("--fmt", default="netscape", help="Format.")
        parser.add_argument("--quiet", action="store_true", help="Quiet.")
        text = parser.format_help()
        assert "Format. (default: netscape)" in text
        assert "(default: None)" not in text
        assert "(default: False)" not in text

    def test_subparser_defaults_to_defaults_formatter(self) -> None:
        parser = StrictArgumentParser(prog="t")
        sub = parser.add_command_subparsers()
        cmd = sub.add_parser("cmd")
        cmd.add_argument("--fmt", default="netscape", help="Format.")
        assert isinstance(cmd.formatter_class(prog="t"), DefaultsHelpFormatter)
        assert "Format. (default: netscape)" in cmd.format_help()

    def test_raw_defaults_formatter_keeps_epilog_verbatim(self) -> None:
        parser = argparse.ArgumentParser(
            prog="t",
            epilog="line1\n  indented line2",
            formatter_class=RawDescriptionDefaultsHelpFormatter,
        )
        parser.add_argument("--fmt", default="json", help="Format.")
        text = parser.format_help()
        assert "  indented line2" in text
        assert "Format. (default: json)" in text


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
