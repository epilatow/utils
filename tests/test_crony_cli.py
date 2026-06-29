#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest", "pytest-cov", "tomlkit", "pydantic>=2"]
# ///
# This is AI generated code

"""Unit tests for the crony CLI entry point."""

import logging
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from conftest import (
    CmdCallbacksBase,
    HelpWidthBase,
    SentinelHomeBase,
    UnknownArgRoutedToSubparserBase,
)

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from conftest_crony import _isolate_home  # noqa: E402, F401

from crony import cli as crony_cli  # noqa: E402
from crony import model as crony_model  # noqa: E402
from crony import paths as crony_paths  # noqa: E402
from crony import runner as crony_runner  # noqa: E402
from crony.errors import (  # noqa: E402
    ConfigError,
    ExitCode,
    JobTimeoutError,
    LockBusyError,
    PreconditionError,
    SubprocessError,
    UsageError,
)
from crony.platform import (  # noqa: E402
    get_scheduler,
)

_script_path = REPO_ROOT / "bin" / "crony"


class TestIsolateCronyHomeFixture(SentinelHomeBase):
    """Pin the autouse `_isolate_home` fixture so a future refactor
    can't quietly remove the safety net. Inherits the generic
    Path.home() + sentinel-non-existence checks and adds the
    crony-specific attribute/env-var matrix assertions.
    """

    def test_all_attributes_under_sentinel(self) -> None:
        # All four path constants are read through crony.paths. (The
        # platform unit dirs resolve under Path.home(), checked below.)
        sentinel = Path.home()
        for attr in (
            "CONFIG_DIR",
            "CONFIG_FILE",
            "CONFIG_DROPIN_DIR",
            "STATE_DIR",
        ):
            value = getattr(crony_paths, attr)
            assert str(value).startswith(str(sentinel)), (
                f"crony.paths.{attr}={value!r} escaped the sentinel"
            )

    def test_all_env_vars_under_sentinel(self) -> None:
        sentinel = Path.home()
        for attr in (
            "CONFIG_DIR",
            "CONFIG_FILE",
            "CONFIG_DROPIN_DIR",
            "STATE_DIR",
        ):
            value = os.environ[f"CRONY_{attr}"]
            assert value.startswith(str(sentinel)), (
                f"CRONY_{attr}={value!r} escaped the sentinel"
            )

    def test_scheduler_unit_dirs_under_sentinel(self) -> None:
        # The scheduler backends resolve their default unit dir under
        # Path.home(), so the autouse Path.home patch sandboxes them
        # with no separate redirect.
        sentinel = Path.home()
        for plat in ("darwin", "linux"):
            unit_dir = get_scheduler(plat).unit_dir
            assert str(unit_dir).startswith(str(sentinel)), (
                f"{plat} unit dir {unit_dir!r} escaped the sentinel"
            )


class TestHelpOutput:
    """`crony --help` surfaces the overview appended to the epilog."""

    def test_help_includes_overview(self) -> None:
        parser = crony_cli._build_parser()
        text = parser.format_help()
        # The overview is a concise summary: the subcommand categories
        # and the bundle-config layout, not per-column / per-subcommand
        # detail (that lives in each subcommand's own --help).
        assert "Subcommands fall into categories:" in text
        assert "configuration (`config`)" in text
        assert "deployment (`apply`, `destroy`)" in text
        # The epilog's Title-Case section headers are present.
        for header in (
            "Exit Status:",
            "Description:",
            "Getting Started:",
            "Platform Specifics:",
        ):
            assert header in text
        # The Exit Status section leads the epilog, before the overview.
        assert text.index("Exit Status:") < text.index("Subcommands fall")
        # The internal `_run` exit codes are hidden from --help, like the
        # man page filters them.
        assert "run.lock held by another" not in text
        assert "run precondition failed" not in text


class TestUnknownArgRoutedToSubparser(UnknownArgRoutedToSubparserBase):
    """Unknown args print the subcommand's usage line."""

    PARSER_FUNC = staticmethod(crony_cli._build_parser)
    CASES = [
        (["status", "--bogus"], "status"),
        (["logs", "--bogus"], "logs"),
        (["enable", "--bogus"], "enable"),
    ]


class TestCmdCallbacks(CmdCallbacksBase):
    """Test command callback dispatch table."""

    CALLBACKS = crony_cli._COMMAND_CALLBACKS
    PARSER_FUNC = crony_cli._build_parser
    CLI_FUNC = staticmethod(crony_cli.cli)
    EXIT_CODE_USAGE = ExitCode.USAGE
    TEST_SUBCOMMAND = "status"
    # destroy / enable / disable / trigger validators consume --all once
    # checked, so it never reaches the handler signatures.
    POPPED_ARGS = {"all_jobs"}
    EXCEPTION_EXIT_CODE_MAP = [
        (UsageError("t"), ExitCode.USAGE),
        (ConfigError("t"), ExitCode.CONFIG),
        (
            SubprocessError(1, ["bogus"]),
            ExitCode.SUBPROCESS,
        ),
        (LockBusyError("t"), ExitCode.LOCK_BUSY),
        (
            PreconditionError("t"),
            ExitCode.PRECONDITION,
        ),
        (
            JobTimeoutError("t"),
            ExitCode.TIMEOUT,
        ),
        (RuntimeError("t"), ExitCode.CRASHED),
    ]


class TestRunGuardDispatch:
    """The internal `_run-guard` subcommand routes to do_run_guard with
    the cap parsed as an int and the inner command captured verbatim via
    REMAINDER -- the inner `--script` / flags must not be parsed as guard
    options.
    """

    def test_dispatches_with_cap_and_inner_argv(self) -> None:
        mock_cb = MagicMock()
        inner = [
            "/abs/uv",
            "run",
            "--script",
            "/abs/crony",
            "_run",
            "x:y",
        ]
        with (
            patch.dict(
                crony_cli._COMMAND_CALLBACKS,
                {"_run-guard": mock_cb},
            ),
            patch("sys.argv", ["prog", "_run-guard", "180", *inner]),
        ):
            result = crony_cli.cli()
        assert result == 0
        mock_cb.assert_called_once_with(cap=180, argv=inner)


class TestRunLegacyAlias:
    """`run` is a transitional alias for the renamed `_run`: units baked
    with the old token before the rename keep firing, both routing to
    do_run with identical args, until a re-apply re-renders them."""

    def test_legacy_run_dispatches_to_do_run(self) -> None:
        mock_cb = MagicMock()
        with (
            patch.dict(
                crony_cli._COMMAND_CALLBACKS,
                {crony_model.RUN_SUBCOMMAND_LEGACY: mock_cb},
            ),
            patch("sys.argv", ["prog", "run", "default:u-test"]),
        ):
            result = crony_cli.cli()
        assert result == 0
        mock_cb.assert_called_once_with(ref="default:u-test")

    def test_both_spellings_route_to_the_same_handler(self) -> None:
        cb = crony_cli._COMMAND_CALLBACKS
        assert (
            cb[crony_model.RUN_SUBCOMMAND]
            is cb[crony_model.RUN_SUBCOMMAND_LEGACY]
            is crony_runner.do_run
        )


class TestConfigSubcommandDispatch:
    """The `config` parent routes its nested actions through the
    "<command> <action>" key in _COMMAND_CALLBACKS. These tests pin
    that the nested form actually reaches the right callback (a
    flat dispatch table without the join would silently do
    nothing), that a missing action prints the parent's help, and
    that an unknown action fires argparse's strict-subparsers error
    path.
    """

    def test_config_init_dispatches_to_do_init(self) -> None:
        mock_cb = MagicMock()
        with (
            patch.dict(
                crony_cli._COMMAND_CALLBACKS,
                {"config init": mock_cb},
            ),
            patch("sys.argv", ["prog", "config", "init", "--force"]),
        ):
            result = crony_cli.cli()
        assert result == 0
        mock_cb.assert_called_once_with(force=True, bundle=None)

    def test_config_validate_dispatches_to_do_validate(self) -> None:
        mock_cb = MagicMock()
        with (
            patch.dict(
                crony_cli._COMMAND_CALLBACKS,
                {"config validate": mock_cb},
            ),
            patch("sys.argv", ["prog", "config", "validate", "-b", "foo"]),
        ):
            result = crony_cli.cli()
        assert result == 0
        mock_cb.assert_called_once_with(bundle="foo", file=None)

    def test_config_without_action_prints_help(self, capsys: Any) -> None:
        # No action -> print config's own help (stdout) and exit USAGE,
        # not argparse's terse "required" error.
        with (
            patch("sys.argv", ["prog", "config"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            crony_cli.cli()
        assert exc_info.value.code == ExitCode.USAGE
        out = capsys.readouterr().out
        # The subcommand's full help (usage line + the action list),
        # not just a usage stub.
        assert "config [-h] <command>" in out
        assert "init" in out and "generate-uuid" in out

    def test_config_unknown_action_errors(self, capsys: Any) -> None:
        with (
            patch("sys.argv", ["prog", "config", "bogus"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            crony_cli.cli()
        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "bogus" in err


class TestNotifyTestArgValidation:
    """`notify-test`'s parser rejects a fully-qualified --channel whose
    bundle contradicts an explicit --bundle."""

    def test_channel_bundle_contradicts_bundle_rejected(self) -> None:
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(
                ["notify-test", "--bundle", "a", "--channel", "b.ntfy"]
            )
        assert exc.value.code == 2

    def test_channel_bundle_matches_bundle_accepted(self) -> None:
        # A qualified channel agreeing with --bundle parses cleanly.
        args = crony_cli._build_parser().parse_command(
            ["notify-test", "--bundle", "a", "--channel", "a.ntfy"]
        )
        assert args.command == "notify-test"
        assert args.bundle == "a"
        assert args.channel == "a.ntfy"

    def test_malformed_channel_does_not_crash_validator(self) -> None:
        # A channel with a dot but an empty component is not a
        # contradiction; the validator must not raise (parse_full_name
        # would), so parsing succeeds and the handler reports it later.
        args = crony_cli._build_parser().parse_command(
            ["notify-test", "--bundle", "a", "--channel", "a."]
        )
        assert args.command == "notify-test"
        assert args.channel == "a."


class TestBrokenPipeHandler:
    """Smoke check that _BrokenPipeAwareStreamHandler swallows
    BrokenPipeError without raising and swaps to /dev/null so the
    next emit doesn't blow up either.
    """

    def test_handler_swaps_stream_on_broken_pipe(self, tmp_path: Path) -> None:
        # Create the handler attached to a regular file we can verify.
        log_path = tmp_path / "out"
        stream = open(log_path, "w")
        handler = crony_cli._BrokenPipeAwareStreamHandler(stream)
        # Synthesize a "BrokenPipeError caught while emitting" by
        # stuffing one into sys.exc_info via a dummy raise.
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        try:
            raise BrokenPipeError("simulated")
        except BrokenPipeError:
            handler.handleError(record)
        # Stream should be swapped (and not the original anymore).
        assert handler.stream is not stream
        # And future emits should not raise.
        handler.emit(record)


class TestHelpWidth(HelpWidthBase):
    PROG = "crony"
    PARSER_FUNC = staticmethod(crony_cli._build_parser)


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
