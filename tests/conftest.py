"""Pytest configuration - runs before test collection."""
# This is AI generated code

from __future__ import annotations

import argparse
import atexit
import inspect
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Iterator
from unittest.mock import create_autospec

# Repository root
_REPO_ROOT = Path(__file__).parent.parent

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


def pytest_sessionfinish(
    session,  # type: ignore[no-untyped-def]
    exitstatus,  # type: ignore[no-untyped-def]
) -> None:
    """Clean up pycache directories after test session."""
    _cleanup_all_caches()


class CodeQualityBase:
    """Base class for code quality tests.

    Subclasses must define SCRIPT_PATH and TEST_PATH.
    """

    SCRIPT_PATH: ClassVar[Path]
    TEST_PATH: ClassVar[Path]

    def test_ruff_check_compliance(self) -> None:
        """Test that code passes ruff linting."""
        result = subprocess.run(
            ["uvx", "ruff", "check", str(self.SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"ruff check failed:\n{result.stdout}"
        )

    def test_ruff_check_compliance_tests(self) -> None:
        """Test that tests pass ruff linting."""
        result = subprocess.run(
            ["uvx", "ruff", "check", str(self.TEST_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"ruff check failed:\n{result.stdout}"
        )

    def test_ruff_format_compliance(self) -> None:
        """Test that code is formatted with ruff."""
        result = subprocess.run(
            [
                "uvx",
                "ruff",
                "format",
                "--check",
                str(self.SCRIPT_PATH),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"ruff format check failed:\n{result.stderr}"
        )

    def test_ruff_format_compliance_tests(self) -> None:
        """Test that tests are formatted with ruff."""
        result = subprocess.run(
            [
                "uvx",
                "ruff",
                "format",
                "--check",
                str(self.TEST_PATH),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"ruff format check failed:\n{result.stderr}"
        )

    def test_mypy_compliance(self, tmp_path: Path) -> None:
        """Test that code passes mypy."""
        cache_dir = tmp_path / "mypy_cache"
        result = subprocess.run(
            [
                "uvx",
                "mypy",
                "--cache-dir",
                str(cache_dir),
                str(self.SCRIPT_PATH),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"mypy check failed:\n{result.stdout}"
        )

    def test_mypy_compliance_tests(self, tmp_path: Path) -> None:
        """Test that tests pass mypy."""
        cache_dir = tmp_path / "mypy_cache"
        env = os.environ.copy()
        env["MYPYPATH"] = str(_REPO_ROOT / "bin")
        result = subprocess.run(
            [
                "uvx",
                "--with",
                "pytest",
                "mypy",
                "--cache-dir",
                str(cache_dir),
                str(self.TEST_PATH),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            f"mypy check failed:\n{result.stdout}"
        )


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
        all_classes = (
            [self.BASE_ERROR]
            + self.BASE_ERROR.__subclasses__()
        )
        covered = {
            cls.exit_code
            for cls in all_classes
            if "exit_code" in cls.__dict__
        }
        expected = set(self.EXIT_CODE) - self.EXCLUDED_CODES
        assert covered == expected

    def test_exception_exit_codes_are_unique(self) -> None:
        """Subclasses with explicit exit_code have unique codes."""
        codes = [
            cls.exit_code
            for cls in self.BASE_ERROR.__subclasses__()
            if "exit_code" in cls.__dict__
        ]
        assert len(codes) == len(set(codes))


class CmdCallbacksBase:
    """Base class for command callback table tests.

    Subclasses must define CALLBACKS, PARSER_FUNC, and
    SELF_TEST_CMD.
    """

    CALLBACKS: ClassVar[Any]
    PARSER_FUNC: ClassVar[Any]
    SELF_TEST_CMD: ClassVar[str] = "self-test"
    POPPED_ARGS: ClassVar[set[str]] = set()

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
            if not isinstance(
                action, argparse._SubParsersAction
            ):
                continue
            for cmd, sub in action.choices.items():
                label = f"{prefix} {cmd}".strip()
                nested = list(
                    CmdCallbacksBase._leaf_subparsers(
                        sub, label
                    )
                )
                if nested:
                    yield from nested
                else:
                    yield label, sub

    def test_dispatch_covers_all_commands(self) -> None:
        """COMMAND_CALLBACKS matches parser commands."""
        parser = type(self).PARSER_FUNC()
        parser_cmds = {
            cmd
            for cmd, _ in self._leaf_subparsers(parser)
        }
        parser_cmds.discard(self.SELF_TEST_CMD)
        assert set(self.CALLBACKS.keys()) == parser_cmds

    def test_dispatch_handlers_have_no_defaults(
        self,
    ) -> None:
        """Dispatch handlers don't define default values."""
        for cmd, fn in self.CALLBACKS.items():
            sig = inspect.signature(fn)
            for name, param in sig.parameters.items():
                assert (
                    param.default is inspect.Parameter.empty
                ), (
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
            arg_names = {
                n for n in arg_names if not n.startswith("_")
            }

            # autospec enforces the real signature
            mock_fn = create_autospec(fn)
            kwargs = {name: None for name in arg_names}
            try:
                mock_fn(**kwargs)
            except TypeError as e:
                raise AssertionError(
                    f"Signature mismatch for '{cmd}' "
                    f"({fn.__name__}): {e}"
                ) from e

    def test_all_subcommands_have_help(self) -> None:
        """All subcommands and arguments have help text."""
        parser = type(self).PARSER_FUNC()

        def check_parser(
            p: argparse.ArgumentParser, path: str
        ) -> None:
            for action in p._actions:
                if isinstance(action, argparse._HelpAction):
                    continue
                if isinstance(
                    action, argparse._SubParsersAction
                ):
                    assert action.choices, (
                        f"Empty subparsers in '{path}'"
                    )
                    for name, sub in action.choices.items():
                        check_parser(sub, f"{path} {name}")
                    continue
                assert action.help and action.help.strip(), (
                    f"Missing help for argument(s) "
                    f"{action.option_strings or action.dest}"
                    f" in '{path}'"
                )

        check_parser(parser, parser.prog)


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

    import pytest  # type: ignore[import-not-found]

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
    args = parser.parse_args()

    pytest_args = [test_file, "-p", "no:cacheprovider"]
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
