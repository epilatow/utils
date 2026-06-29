"""Pytest configuration - runs before test collection."""
# This is AI generated code

import argparse
import atexit
import inspect
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock, create_autospec, patch

import pytest

# Repository root
_REPO_ROOT = Path(__file__).parent.parent

# Make the first-party src/ packages (e.g. `common`) importable in tests,
# the same way each bin/ entry prepends src/ at runtime.
sys.path.insert(0, str(_REPO_ROOT / "src"))

# Create a temporary directory for __pycache__ and redirect all bytecode there
_pycache_tmpdir = tempfile.mkdtemp(prefix="pytest_pycache_")
sys.pycache_prefix = _pycache_tmpdir

# Also prevent bytecode writing for subsequent imports (belt and suspenders)
sys.dont_write_bytecode = True


def _cleanup_all_caches() -> None:
    """Remove temp dirs and any cache dirs in the repo."""
    # Clean up the temp pycache directory
    shutil.rmtree(_pycache_tmpdir, ignore_errors=True)
    # Clean up any __pycache__ created before sys.pycache_prefix was set
    # (e.g., conftest.py's own bytecode)
    for pycache in _REPO_ROOT.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)
    # Clean up any .mypy_cache directories
    for mypy_cache in _REPO_ROOT.rglob(".mypy_cache"):
        if mypy_cache.is_dir():
            shutil.rmtree(mypy_cache, ignore_errors=True)


# Register cleanup for when Python exits
atexit.register(_cleanup_all_caches)


def pytest_sessionfinish() -> None:
    """Clean up pycache directories after test session."""
    _cleanup_all_caches()


class ExceptionHierarchyBase:
    """Base class for exception hierarchy tests.

    Subclasses must define BASE_ERROR, EXIT_CODE, and
    EXCLUDED_CODES.
    """

    BASE_ERROR: ClassVar[Any]
    EXIT_CODE: ClassVar[Any]
    EXCLUDED_CODES: ClassVar[set[Any]]

    def test_all_exit_codes_have_exception(self) -> None:
        """Every non-excluded ExitCode has an exception."""
        all_classes = [self.BASE_ERROR] + self.BASE_ERROR.__subclasses__()
        covered = {
            cls.exit_code for cls in all_classes if "exit_code" in cls.__dict__
        }
        expected = set(self.EXIT_CODE) - self.EXCLUDED_CODES
        assert covered == expected

    def test_usage_code_matches_argparse(self) -> None:
        """ExitCode.USAGE matches argparse's error exit code."""
        parser = argparse.ArgumentParser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--bogus"])
        assert self.EXIT_CODE["USAGE"] == exc_info.value.code

    def test_exception_exit_codes_are_unique(self) -> None:
        """Subclasses with explicit exit_code have unique codes."""
        codes = [
            cls.exit_code
            for cls in self.BASE_ERROR.__subclasses__()
            if "exit_code" in cls.__dict__
        ]
        assert len(codes) == len(set(codes))

    def test_common_exit_codes_match_canon(self) -> None:
        """Common codes a utility declares match the canonical subset
        (value + description), so they can't drift. SUCCESS..SUBPROCESS
        and CRASHED are mandatory; the rest (e.g. TIMEOUT) are used
        where they apply. Utility-specific codes start at 10."""
        from common.exitcodes import CommonExitCode

        canon = {
            name: getattr(CommonExitCode, name)
            for name in vars(CommonExitCode)
            if not name.startswith("_")
        }
        members = self.EXIT_CODE.__members__
        mandatory = {
            "SUCCESS",
            "WARNING",
            "USAGE",
            "CONFIG",
            "ERROR",
            "SUBPROCESS",
            "CRASHED",
        }
        assert mandatory <= set(members), (
            f"missing mandatory common codes: {mandatory - set(members)}"
        )
        for name, (value, description) in canon.items():
            if name in members:
                assert members[name].value == value
                assert members[name].description == description
        # Specifics live in the reserved 10+ range so a utility's own
        # code can never collide with a later-added common code.
        for name, member in members.items():
            if name not in canon:
                assert member.value >= 10, (
                    f"{name}={member.value} must be >= 10 "
                    "(0-9 reserved for common codes)"
                )


def isolate_home(
    module: Any,
    installed_basename: str,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Default Path.home() and a module's INSTALLED_FILE to a
    nonexistent path under tmp_path so a test that forgets to patch
    them cannot touch the real $HOME or installed-tracking file.

    Tests that explicitly set their own home via patch.object /
    monkeypatch.setattr override this. Lives in conftest so the
    per-tool autouse fixtures (linkfiles, crony, ...) share one
    definition.
    """
    sentinel = tmp_path / "_home_sentinel_unwritten"
    monkeypatch.setattr(Path, "home", lambda: sentinel)
    monkeypatch.setattr(module, "INSTALLED_FILE", sentinel / installed_basename)


class SentinelHomeBase:
    """Common meta-tests pinning the `Path.home()` redirection that
    every per-tool autouse home-isolation fixture provides. Tool-specific
    bases (e.g. crony) inherit these two checks so the sentinel-naming
    contract stays uniform.
    """

    def test_home_diverted_to_sentinel(self, tmp_path: Path) -> None:
        """Path.home() returns a per-test sentinel, not the real home."""
        assert Path.home() == tmp_path / "_home_sentinel_unwritten"

    def test_sentinel_does_not_exist(self) -> None:
        """The sentinel intentionally does not exist on disk."""
        assert not Path.home().exists()


class CmdCallbacksBase:
    """Base class for command callback table tests.

    Subclasses must define CALLBACKS, PARSER_FUNC,
    CLI_FUNC, and EXIT_CODE_USAGE.
    """

    CALLBACKS: ClassVar[Any]
    PARSER_FUNC: ClassVar[Any]
    CLI_FUNC: ClassVar[Any]
    EXIT_CODE_USAGE: ClassVar[int]
    POPPED_ARGS: ClassVar[set[str]] = set()
    TEST_SUBCOMMAND: ClassVar[str] = ""
    EXCEPTION_EXIT_CODE_MAP: ClassVar[list[tuple[Exception, int]]] = []

    @staticmethod
    def _leaf_subparsers(
        parser: argparse.ArgumentParser,
        prefix: str = "",
    ) -> Iterator[tuple[str, argparse.ArgumentParser]]:
        """Yield ``(command_key, subparser)`` for leaf commands.

        Handles both flat and nested subparsers, building
        compound keys like ``"check age"`` for nested ones.
        """
        for action in parser._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for cmd, sub in action.choices.items():
                label = f"{prefix} {cmd}".strip()
                nested = list(CmdCallbacksBase._leaf_subparsers(sub, label))
                if nested:
                    yield from nested
                else:
                    yield label, sub

    def test_dispatch_covers_all_commands(self) -> None:
        """COMMAND_CALLBACKS matches parser commands."""
        parser = type(self).PARSER_FUNC()
        parser_cmds = {cmd for cmd, _ in self._leaf_subparsers(parser)}
        assert set(self.CALLBACKS.keys()) == parser_cmds

    def test_dispatch_handlers_have_no_defaults(
        self,
    ) -> None:
        """Dispatch handlers don't define default values."""
        for _cmd, fn in self.CALLBACKS.items():
            sig = inspect.signature(fn)
            for name, param in sig.parameters.items():
                assert param.default is inspect.Parameter.empty, (
                    f"{fn.__name__}({name}=...) has a "
                    f"default; defaults belong in the "
                    f"argument parser"
                )

    def test_dispatch_signatures_match_parsers(
        self,
    ) -> None:
        """Callback signatures match their subparser args."""
        parser = type(self).PARSER_FUNC()
        subs = dict(self._leaf_subparsers(parser))
        skip = {"command"} | self.POPPED_ARGS

        for cmd, fn in self.CALLBACKS.items():
            sub = subs[cmd]
            # Collect arg dest names from the subparser
            arg_names = set()
            for action in sub._actions:
                if isinstance(
                    action,
                    (
                        argparse._HelpAction,
                        argparse._SubParsersAction,
                    ),
                ):
                    continue
                arg_names.add(action.dest)
            arg_names -= skip
            arg_names = {n for n in arg_names if not n.startswith("_")}

            # autospec enforces the real signature
            mock_fn = create_autospec(fn)
            kwargs = {name: None for name in arg_names}
            try:
                mock_fn(**kwargs)
            except TypeError as e:
                raise AssertionError(
                    f"Signature mismatch for '{cmd}' ({fn.__name__}): {e}"
                ) from e

    def test_all_subcommands_have_help(self) -> None:
        """All subcommands and arguments have help text."""
        parser = type(self).PARSER_FUNC()

        def check_parser(p: argparse.ArgumentParser, path: str) -> None:
            for action in p._actions:
                if isinstance(action, argparse._HelpAction):
                    continue
                if isinstance(action, argparse._SubParsersAction):
                    assert action.choices, f"Empty subparsers in '{path}'"
                    for name, sub in action.choices.items():
                        check_parser(sub, f"{path} {name}")
                    continue
                assert action.help and action.help.strip(), (
                    f"Missing help for argument(s) "
                    f"{action.option_strings or action.dest}"
                    f" in '{path}'"
                )

        check_parser(parser, parser.prog)

    def test_parser_builds_successfully(self) -> None:
        """Verify parser can be built without errors."""
        parser = type(self).PARSER_FUNC()
        assert parser is not None

    def test_no_args_leaves_top_level_unset(self) -> None:
        """The top-level command group is non-required, so parse_args([])
        leaves its dest at None -- the state parse_command turns into a
        help-and-exit rather than argparse's terse required error."""
        parser = type(self).PARSER_FUNC()
        args = parser.parse_args([])
        assert args.cmd1 is None

    def test_no_args_shows_help(self, capsys: Any) -> None:
        """No arguments prints help and exits USAGE."""
        with (
            patch("sys.argv", ["prog"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            type(self).CLI_FUNC()
        assert exc_info.value.code == type(self).EXIT_CODE_USAGE
        captured = capsys.readouterr()
        assert "usage:" in captured.out.lower()

    def test_help_exits_success(self) -> None:
        """--help exits with code 0."""
        with (
            patch("sys.argv", ["prog", "--help"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            type(self).CLI_FUNC()
        assert exc_info.value.code == 0

    def test_cli_exception_to_exit_code(
        self,
    ) -> None:
        """cli() maps exceptions to correct exit codes."""
        exc_map = type(self).EXCEPTION_EXIT_CODE_MAP
        if not exc_map:
            return
        subcommand = type(self).TEST_SUBCOMMAND
        for exc, expected_code in exc_map:
            mock_cb = MagicMock(side_effect=exc)
            with (
                patch.dict(
                    type(self).CALLBACKS,
                    {subcommand: mock_cb},
                ),
                patch(
                    "sys.argv",
                    ["prog", subcommand],
                ),
            ):
                result = type(self).CLI_FUNC()
            assert result == expected_code, (
                f"Expected {expected_code} for "
                f"{type(exc).__name__}, got {result}"
            )

    def test_cli_returns_success(self) -> None:
        """cli() returns SUCCESS for a valid subcommand."""
        mock_cb = MagicMock()
        subcommand = type(self).TEST_SUBCOMMAND
        with (
            patch.dict(
                type(self).CALLBACKS,
                {subcommand: mock_cb},
            ),
            patch(
                "sys.argv",
                ["prog", subcommand],
            ),
        ):
            result = type(self).CLI_FUNC()
        assert result == 0
        assert mock_cb.called

    def test_cli_keyboard_interrupt(self) -> None:
        """Ctrl-C exits on the SIGINT convention (128 + SIGINT), not a
        traceback. KeyboardInterrupt is a BaseException, so cli() must
        catch it explicitly -- a plain `except Exception` lets it
        escape."""
        from common.exitcodes import SIGINT_EXIT_CODE

        mock_cb = MagicMock(side_effect=KeyboardInterrupt())
        subcommand = type(self).TEST_SUBCOMMAND
        with (
            patch.dict(
                type(self).CALLBACKS,
                {subcommand: mock_cb},
            ),
            patch(
                "sys.argv",
                ["prog", subcommand],
            ),
        ):
            result = type(self).CLI_FUNC()
        assert result == SIGINT_EXIT_CODE

    def test_cli_unexpected_exception(self) -> None:
        """An exception escaping the entry point's own handlers is
        turned into the dedicated crash exit code (with a traceback),
        not a raw traceback exit. The shared cli_entrypoint decorator
        owns this fallback, and the crash code is distinct from a
        deliberate ERROR."""
        from common.exitcodes import CommonExitCode

        mock_cb = MagicMock(side_effect=RuntimeError("boom"))
        subcommand = type(self).TEST_SUBCOMMAND
        with (
            patch.dict(
                type(self).CALLBACKS,
                {subcommand: mock_cb},
            ),
            patch(
                "sys.argv",
                ["prog", subcommand],
            ),
        ):
            result = type(self).CLI_FUNC()
        assert result == CommonExitCode.CRASHED[0]


class UnknownArgRoutedToSubparserBase:
    """Assert unknown args print the chosen subparser's usage line.

    Stdlib argparse stashes leftover tokens on the top-level namespace
    and raises from the root parser, so the user sees the root's
    program name and usage line for a flag the subparser rejected.
    Utilities that install a strict subparsers action route the error
    through the chosen subparser instead. This base verifies that the
    "usage:" line and the "<prog> <subcommand>: error: ..." line both
    reference the actual subcommand path.

    Subclasses set:
      PARSER_FUNC: callable returning a fresh ArgumentParser.
      CASES: list of (argv, subcommand_path) tuples, where
        subcommand_path is the space-joined subcommand chain expected
        in the usage / error lines (e.g. "list", "check age").
    """

    PARSER_FUNC: ClassVar[Any]
    CASES: ClassVar[list[tuple[list[str], str]]]

    def test_unknown_arg_reports_subparser_usage(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        for argv, suffix in self.CASES:
            parser = type(self).PARSER_FUNC()
            with pytest.raises(SystemExit) as exc_info:
                parser.parse_args(argv)
            assert exc_info.value.code == 2, (
                f"Expected parse error for {argv!r}"
            )
            err = capsys.readouterr().err
            first_line = err.splitlines()[0]
            assert first_line.startswith("usage: "), (
                f"Expected 'usage:' line for {argv!r}, got: {err!r}"
            )
            assert f" {suffix} [-h]" in first_line, (
                f"Expected subcommand {suffix!r} in usage line "
                f"for {argv!r}, got: {first_line!r}"
            )
            assert f" {suffix}: error: " in err, (
                f"Expected {suffix!r} error line for {argv!r}, got: {err!r}"
            )


class HelpWidthBase:
    """Assert every utility's --help fits the terminal argparse formats for.

    argparse targets ``terminal_columns - 2`` as its wrap width (it keeps a
    two-column margin: ``HelpFormatter`` does ``width = columns; width -=
    2``). The test pins the terminal to ``TERMINAL_COLUMNS`` and checks
    each line against ``TERMINAL_COLUMNS - 2`` -- one knob feeds both the
    pinned terminal and the asserted limit, so they cannot drift apart.

    A line exceeds the target only where argparse hits a part it cannot
    break: verbatim text it copies (a long ``description=`` /
    ``RawDescriptionHelpFormatter`` epilog, a wide subparser metavar) or,
    before Python 3.13, a wide mutually-exclusive group. The interpreter is
    pinned (see ``.python-version``) so that wrapping behavior is fixed too,
    leaving genuinely over-wide CLI surface as the only thing this catches.

    This base walks the parser and every subparser, renders each
    ``--help``, and asserts no line exceeds ``TERMINAL_COLUMNS - 2``.

    Subclasses set:
      PROG: the command name as users invoke it (e.g. "firefox-cookies").
      PARSER_FUNC: callable returning a fresh ArgumentParser.
    """

    PROG: ClassVar[str]
    PARSER_FUNC: ClassVar[Callable[[], argparse.ArgumentParser]]
    # The single knob: the terminal width the test pins. The asserted
    # limit is argparse's wrap target (this minus 2), computed in the test
    # so overriding this alone cannot desync the terminal and the limit.
    TERMINAL_COLUMNS: ClassVar[int] = 80

    @staticmethod
    def _all_parsers(
        parser: argparse.ArgumentParser,
    ) -> Iterator[argparse.ArgumentParser]:
        """Yield ``parser`` and every (transitive) subparser once."""
        seen: set[int] = set()

        def walk(
            p: argparse.ArgumentParser,
        ) -> Iterator[argparse.ArgumentParser]:
            if id(p) in seen:
                return
            seen.add(id(p))
            yield p
            for action in p._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for sub in action.choices.values():
                        yield from walk(sub)

        yield from walk(parser)

    def test_help_fits_terminal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No --help line (top level or any subcommand) exceeds the width
        argparse targets at the pinned terminal size."""
        # argparse derives help width from the terminal; pin it so the gate
        # is deterministic wherever the suite runs, and derive the limit
        # (TERMINAL_COLUMNS - 2) from that same value so the two stay tied.
        columns = type(self).TERMINAL_COLUMNS
        max_width = columns - 2
        monkeypatch.setenv("COLUMNS", str(columns))
        # Build under the real program name: argparse derives each parser's
        # prog (and so its usage-line width) from argv[0] at build time, and
        # under pytest that would otherwise be the runner's path.
        monkeypatch.setattr(sys, "argv", [type(self).PROG])
        overflow: list[str] = []
        for sub in self._all_parsers(type(self).PARSER_FUNC()):
            for line in sub.format_help().splitlines():
                if len(line) > max_width:
                    overflow.append(f"{sub.prog}: ({len(line)}) {line!r}")
        assert not overflow, (
            f"--help lines exceed {max_width} columns:\n" + "\n".join(overflow)
        )

    def test_subcommand_groups_set_command_metavar(self) -> None:
        """Every subcommand group sets ``metavar="<command>"`` so its usage
        line shows the placeholder, not the full (unwrappable) choices set
        that would otherwise blow the width budget."""
        offenders: list[str] = []
        for sub in self._all_parsers(type(self).PARSER_FUNC()):
            for action in sub._actions:
                if isinstance(action, argparse._SubParsersAction):
                    if action.metavar != "<command>":
                        offenders.append(
                            f"{sub.prog}: metavar={action.metavar!r}"
                        )
        assert not offenders, (
            'subcommand groups must set metavar="<command>":\n'
            + "\n".join(offenders)
        )


def run_tests(
    test_file: str,
    script_path: Path,
    repo_root: Path,
) -> None:
    """Entry point for running a test file directly.

    Handles ``--verbose`` and ``--coverage`` flags, then
    invokes ``pytest.main()``.  Called from each test
    file's ``__main__`` block.

    Args:
        test_file: The test file's ``__file__`` path.
        script_path: Path to the script under test
            (used to derive the coverage module name).
        repo_root: Repository root directory.
    """
    import argparse

    import pytest

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose test output",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Run with coverage report",
    )
    parser.add_argument(
        "--e2e",
        action="store_true",
        help=(
            "Include tests marked @pytest.mark.e2e (slow, "
            "subprocess-based). Off by default; on for explicit "
            "utility-change verification."
        ),
    )
    args = parser.parse_args()

    pytest_args = [test_file, "-p", "no:cacheprovider"]
    if args.e2e:
        # pytest-xdist parallelises the slow E2E suite if the
        # test file's PEP 723 deps include it; otherwise we
        # silently fall back to serial execution, which is fine
        # for utilities that have no e2e tests to parallelise.
        import importlib.util

        if importlib.util.find_spec("xdist") is not None:
            pytest_args.extend(["-n", "auto"])
    else:
        pytest_args.extend(["-m", "not e2e"])
    if args.verbose:
        pytest_args.append("-v")
    if args.coverage:
        module = script_path.stem.replace("-", "_")
        cov_dir = Path(tempfile.gettempdir())
        pytest_args.extend(
            [
                f"--cov={module}",
                "--cov-report=term-missing",
                f"--cov-report=html:{cov_dir / (module + '_htmlcov')}",
            ]
        )
        os.environ["PYTHONPATH"] = str(repo_root / "bin")
        os.environ["COVERAGE_FILE"] = str(cov_dir / f"{module}.coverage")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(pytest.main(pytest_args))
