#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov"]
# ///
# This is human generated code that's been AI modified

"""
Unit tests for borgadm
"""

from __future__ import annotations

import argparse
import collections
import importlib.machinery
import importlib.util
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest  # type: ignore[import-not-found]

# Repository root directory (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent

# Import borgadm module from bin/ (works with or without .py extension)
_script_path = REPO_ROOT / "bin" / "borgadm"
if not _script_path.exists():
    _script_path = REPO_ROOT / "bin" / "borgadm.py"
_loader = importlib.machinery.SourceFileLoader("borgadm", str(_script_path))
_spec = importlib.util.spec_from_loader("borgadm", _loader)
assert _spec and _spec.loader
ba = importlib.util.module_from_spec(_spec)
sys.modules["borgadm"] = ba
_spec.loader.exec_module(ba)


@pytest.fixture
def mock_cfg() -> Any:
    """Create a mock config for testing."""
    config_file = tempfile.NamedTemporaryFile(mode="w+", delete=False)
    config_file.write("""
    BORG_REPO = "foobar"
    BACKUP_SETS = { "set1": {"paths": ["foo"]} }
    """)
    config_file.flush()

    cfg = ba.Config(config_file.name, {"command": "test"})
    original_cfg = getattr(ba, "CFG")
    setattr(ba, "CFG", cfg)
    yield cfg
    setattr(ba, "CFG", original_cfg)


class TestArgumentParser:
    """Test argument parser structure."""

    def test_parser_builds_successfully(self) -> None:
        """Verify parser can be built without errors."""
        parser = ba.args_parser()
        assert parser is not None

    def test_all_subcommands_have_help(self) -> None:
        """Verify all subcommands and their arguments have help text."""
        parser = ba.args_parser()

        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for subcmd_name, subparser in action.choices.items():
                    for sub_action in subparser._actions:
                        # Ignore default help option added by argparse
                        if isinstance(sub_action, argparse._HelpAction):
                            continue
                        # Skip subparsers (automate's enable/disable/status)
                        if isinstance(sub_action, argparse._SubParsersAction):
                            continue
                        assert sub_action.help and sub_action.help.strip(), (
                            f"Missing help for argument(s) "
                            f"{sub_action.option_strings or sub_action.dest} "
                            f"in subcommand '{subcmd_name}'"
                        )

    def test_automate_subcommands_have_help(self) -> None:
        """Smoke test: verify automate subcommands can show help."""
        parser = ba.args_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["automate", "enable", "--help"])
        with pytest.raises(SystemExit):
            parser.parse_args(["automate", "disable", "--help"])
        with pytest.raises(SystemExit):
            parser.parse_args(["automate", "status", "--help"])

    def test_self_test_subcommand_parses(self) -> None:
        """Test self-test subcommand parses flags."""
        parser = ba.args_parser()
        args = parser.parse_args(["self-test", "-v", "--coverage"])
        assert args.command == "self-test"
        assert args.verbose is True
        assert args.coverage is True


class TestTimestampPruning:
    """Test timestamp pruning logic."""

    @pytest.fixture(autouse=True)
    def setup_cfg(self, mock_cfg: Any) -> None:
        """Set up pruning config values."""
        mock_cfg.PRUNE_KEEP_HOURLY = 2
        mock_cfg.PRUNE_KEEP_DAILY = 2
        mock_cfg.PRUNE_KEEP_WEEKLY = 2
        mock_cfg.PRUNE_KEEP_MONTHLY = 2
        mock_cfg.PRUNE_KEEP_YEARLY = 2

    def test_empty_set(self) -> None:
        """Test that empty input returns empty output."""
        assert ba.ts_to_keep(set()) == collections.OrderedDict()

    def test_incremental_prune(self) -> None:
        """Test incremental pruning simulation."""
        start = datetime(2000, 1, 1, 0, 0, 0)
        ts_all: set[str] = set()
        for i in range(int(24 * 365 * 3.5)):
            ts = start + timedelta(hours=i)
            ts_all.add(ts.strftime("%Y%m%d_%H%M%S"))
            ts_keep = ba.ts_to_keep(ts_all)
            ts_all = set(ts_keep)
        ts_keep_verify = collections.OrderedDict(
            [
                ("20030701_110000", "hour-0"),
                ("20030701_100000", "hour-1"),
                ("20030701_000000", "day-0"),
                ("20030630_000000", "day-1"),
                ("20030629_000000", "week-0"),
                ("20030623_000000", "month-0"),
                ("20030620_000000", "week-1"),
                ("20030521_000000", "month-1"),
                ("20021231_000000", "year-0"),
                ("20011231_000000", "year-1"),
            ]
        )
        assert ts_keep == ts_keep_verify, f"Incorrect ts_keep: {ts_keep}"

    def test_bulk_prune(self) -> None:
        """Test bulk pruning of many timestamps."""
        start = datetime(2000, 1, 1, 0, 0, 0)
        ts_all = set(
            [
                (start + timedelta(hours=i)).strftime("%Y%m%d_%H%M%S")
                for i in range(int(24 * 365 * 3.5))
            ]
        )
        ts_keep = ba.ts_to_keep(ts_all)
        ts_keep_verify = collections.OrderedDict(
            [
                ("20030701_110000", "hour-0"),
                ("20030701_100000", "hour-1"),
                ("20030701_000000", "day-0"),
                ("20030630_000000", "day-1"),
                ("20030628_000000", "week-0"),
                ("20030621_000000", "week-1"),
                ("20030614_000000", "month-0"),
                ("20030515_000000", "month-1"),
                ("20021231_000000", "year-0"),
                ("20011231_000000", "year-1"),
            ]
        )
        assert ts_keep == ts_keep_verify, f"Incorrect ts_keep: {ts_keep}"


class TestCodeQuality:
    """Test code quality with black, flake8, and mypy."""

    def test_black_compliance(self) -> None:
        """Test that code is formatted with black."""
        result = subprocess.run(
            ["uvx", "black", "-l80", "--check", str(_script_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"black check failed:\n{result.stderr}"

    def test_black_compliance_tests(self) -> None:
        """Test that tests are formatted with black."""
        result = subprocess.run(
            [
                "uvx",
                "black",
                "-l80",
                "--check",
                str(REPO_ROOT / "tests" / "test_borgadm.py"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"black check failed:\n{result.stderr}"

    def test_flake8_compliance(self) -> None:
        """Test that code passes flake8."""
        result = subprocess.run(
            [
                "uvx",
                "flake8",
                "--max-line-length=80",
                "--extend-ignore=E203,W503",
                str(_script_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"flake8 check failed:\n{result.stdout}"

    def test_flake8_compliance_tests(self) -> None:
        """Test that tests pass flake8."""
        result = subprocess.run(
            [
                "uvx",
                "flake8",
                "--max-line-length=80",
                "--extend-ignore=E203,W503",
                str(REPO_ROOT / "tests" / "test_borgadm.py"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"flake8 check failed:\n{result.stdout}"

    def test_mypy_compliance(self, tmp_path: Path) -> None:
        """Test that code passes mypy."""
        cache_dir = tmp_path / "mypy_cache"
        result = subprocess.run(
            ["uvx", "mypy", "--cache-dir", str(cache_dir), str(_script_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"mypy check failed:\n{result.stdout}"

    def test_mypy_compliance_tests(self, tmp_path: Path) -> None:
        """Test that tests pass mypy."""
        import os

        cache_dir = tmp_path / "mypy_cache"
        env = os.environ.copy()
        env["MYPYPATH"] = str(REPO_ROOT / "bin")
        result = subprocess.run(
            [
                "uvx",
                "--with",
                "pytest",
                "mypy",
                "--cache-dir",
                str(cache_dir),
                str(REPO_ROOT / "tests" / "test_borgadm.py"),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"mypy check failed:\n{result.stdout}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
