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
import io
import importlib.util
import logging
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import Mock, patch
from xml.etree import ElementTree

import pytest  # type: ignore[import-not-found]
from conftest import (
    CmdCallbacksBase,
    CodeQualityBase,
    ExceptionHierarchyBase,
)

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


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path) -> Iterator[Path]:
    """Redirect HOME and tempdir to empty temp directories.

    Prevents tests from accidentally accessing real user files
    (e.g., ~/.borgadm, ~/.borg_passphrase, ~/.ssh/id_borg.net)
    or writing to the real system temp directory.
    Any test that needs these files must create them explicitly.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    old_home = os.environ.get("HOME")
    old_tempdir = tempfile.tempdir
    basename: str = getattr(ba, "BASENAME")
    old_config = getattr(ba, "CONFIG")
    old_logfile = getattr(ba, "LOGFILE")

    os.environ["HOME"] = str(fake_home)
    tempfile.tempdir = str(tmp_path)
    setattr(ba, "CONFIG", Path(fake_home / f".{basename}"))
    setattr(
        ba,
        "LOGFILE",
        Path(tempfile.gettempdir()) / f"{basename}.log",
    )

    try:
        yield fake_home
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            del os.environ["HOME"]
        tempfile.tempdir = old_tempdir
        setattr(ba, "CONFIG", old_config)
        setattr(ba, "LOGFILE", old_logfile)


_CONFIG_CONSUMED_KEYS = (
    "seconds",
    "keep_hourly",
    "keep_daily",
    "keep_weekly",
    "keep_monthly",
    "keep_yearly",
)


def mock_config_constructor(cfg: Any) -> Any:
    """Return a Config side_effect that pops args like the real one."""

    def constructor(_config_path: str, args: dict[str, Any]) -> Any:
        for key in _CONFIG_CONSUMED_KEYS:
            args.pop(key, None)
        return cfg

    return constructor


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

    def test_check_legacy_rewrite(self) -> None:
        """Test legacy check-* commands are rewritten to check *."""
        cases: dict[str, list[str]] = {
            "check-age": ["borgadm", "check", "age"],
            "check-all": ["borgadm", "check", "all"],
        }
        for old_cmd, expected in cases.items():
            result = ba.rewrite_legacy_args(["borgadm", old_cmd])
            assert result == expected, (
                f"rewrite_legacy_args({old_cmd!r}): "
                f"expected {expected}, got {result}"
            )

    def test_check_legacy_rewrite_preserves_args(self) -> None:
        """Test legacy rewrite preserves trailing arguments."""
        result = ba.rewrite_legacy_args(
            ["borgadm", "check-age", "--enable-notifications"]
        )
        assert result == [
            "borgadm",
            "check",
            "age",
            "--enable-notifications",
        ]

    def test_check_legacy_rewrite_ignores_non_legacy(self) -> None:
        """Test that non-legacy commands pass through unchanged."""
        argv = ["borgadm", "create", "--dry-run"]
        assert ba.rewrite_legacy_args(argv) == argv

    def test_repair_subcommand_parses(self) -> None:
        """Test repair delete-cache subcommand parses."""
        parser = ba.args_parser()
        args = parser.parse_args(["repair", "delete-cache"])
        assert args.command == "repair"
        assert args.action == "delete-cache"

    def test_repair_repo_yes_parses(self) -> None:
        """Test repair repo --yes parses."""
        parser = ba.args_parser()
        args = parser.parse_args(["repair", "repo", "--yes"])
        assert args.command == "repair"
        assert args.action == "repo"
        assert args.yes is True

    def test_repair_repo_no_yes_parses(self) -> None:
        """Test repair repo without --yes defaults to False."""
        parser = ba.args_parser()
        args = parser.parse_args(["repair", "repo"])
        assert args.command == "repair"
        assert args.action == "repo"
        assert args.yes is False

    def test_common_args_rejected_before_action(self) -> None:
        """Common args between subcommand and action should fail."""
        parser = ba.args_parser()
        cases = [
            ["check", "--verbose", "age"],
            ["check", "--config", "/tmp/c", "age"],
            ["check", "--enable-notifications", "age"],
            ["automate", "--verbose", "enable"],
            ["repair", "--verbose", "delete-cache"],
        ]
        for argv in cases:
            with pytest.raises(SystemExit) as exc_info:
                parser.parse_args(argv)
            assert exc_info.value.code == 2, (
                f"Expected parse error for {argv!r}"
            )

    def test_common_args_accepted_after_action(self) -> None:
        """Common args are accepted after the action name."""
        parser = ba.args_parser()
        cases: list[tuple[list[str], str, object]] = [
            (["check", "age", "--verbose"], "verbose", True),
            (
                ["check", "age", "--enable-notifications"],
                "enable_notifications",
                True,
            ),
            (
                ["check", "age", "--config", "/tmp/c"],
                "config",
                "/tmp/c",
            ),
            (
                ["automate", "enable", "--verbose"],
                "verbose",
                True,
            ),
            (
                ["repair", "delete-cache", "--verbose"],
                "verbose",
                True,
            ),
        ]
        for argv, attr, expected in cases:
            args = parser.parse_args(argv)
            assert getattr(args, attr) == expected, (
                f"Failed for {argv!r}: expected {attr}={expected!r}"
            )

    def test_common_args_on_subcommands_without_actions(
        self,
    ) -> None:
        """Subcommands without actions accept common args."""
        parser = ba.args_parser()
        args = parser.parse_args(["break-lock", "--verbose"])
        assert args.verbose is True
        args = parser.parse_args(["create", "--enable-notifications"])
        assert args.enable_notifications is True
        args = parser.parse_args(["list", "--config", "/tmp/c"])
        assert args.config == "/tmp/c"

    def test_help_shows_common_args_at_correct_level(
        self,
    ) -> None:
        """Common args shown in help only where accepted."""
        parser = ba.args_parser()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                check_parser = action.choices["check"]
                check_help = check_parser.format_help()
                assert "--verbose" not in check_help, (
                    "check help should not show --verbose"
                )
                assert "--config" not in check_help, (
                    "check help should not show --config"
                )
                for sub in check_parser._actions:
                    if isinstance(sub, argparse._SubParsersAction):
                        age_parser = sub.choices["age"]
                        age_help = age_parser.format_help()
                        assert "--verbose" in age_help, (
                            "check age help should show --verbose"
                        )
                        assert "--config" in age_help, (
                            "check age help should show --config"
                        )
                break


class TestCmdCallbacks(CmdCallbacksBase):
    """Test COMMAND_CALLBACKS table."""

    CALLBACKS = ba.COMMAND_CALLBACKS
    PARSER_FUNC = ba.args_parser
    CLI_FUNC = staticmethod(ba.cli)
    MODULE = ba
    EXIT_CODE_USAGE = ba.ExitCode.USAGE
    TEST_SUBCOMMAND = "environment"
    EXCEPTION_EXIT_CODE_MAP = [
        (ba.ConfigError("t"), ba.ExitCode.CONFIG),
        (
            ba.SubprocessError(1, "cmd"),
            ba.ExitCode.SUBPROCESS,
        ),
        (
            ba.CheckNoBackupsError("t"),
            ba.ExitCode.CHECK_NO_BACKUPS,
        ),
        (ba.CheckAgeError("t"), ba.ExitCode.CHECK_AGE),
        (
            ba.CheckRepoError("t"),
            ba.ExitCode.CHECK_REPO,
        ),
        (
            ba.CheckArchivesError("t"),
            ba.ExitCode.CHECK_ARCHIVES,
        ),
        (
            ba.CheckPruneError("t"),
            ba.ExitCode.CHECK_PRUNE,
        ),
        (ba.BorgadmError("t"), ba.ExitCode.ERROR),
        (RuntimeError("t"), ba.ExitCode.ERROR),
    ]
    POPPED_ARGS = {
        "validate",
        "config",
        "verbose",
        "timestamp_messages",
        "enable_notifications",
        "action",
        # Consumed by Config constructor:
        "seconds",
        "keep_hourly",
        "keep_daily",
        "keep_weekly",
        "keep_monthly",
        "keep_yearly",
    }

    @pytest.fixture(autouse=True)
    def _mock_cfg(self, mock_cfg: Any) -> Iterator[Any]:
        """Activate mock config for all inherited tests.

        Patches Config and initialize_borg_environment so
        main() reaches COMMAND_CALLBACKS without needing
        real config, SSH keys, or borg passphrase.
        Saves/restores globals that main() modifies.
        """
        saved_title = getattr(ba, "_notify_title")
        with (
            patch.object(ba, "Config", return_value=mock_cfg),
            patch.object(ba, "initialize_borg_environment"),
        ):
            yield mock_cfg
        setattr(ba, "_notify_title", saved_title)


class TestRepair:
    """Test repair subcommand."""

    def test_delete_cache(self, mock_cfg: Any) -> None:
        """Test that delete-cache calls borg delete --cache-only."""
        with (
            patch.object(ba, "run_cmd", autospec=True) as mock_run_cmd,
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
        ):
            ba.do_repair_delete_cache()
            mock_run_cmd.assert_called_once_with(
                [
                    "borg",
                    "delete",
                    "--cache-only",
                    mock_cfg.BORG_REPO,
                ]
            )

    def test_repair_repo_without_yes_exits(self, mock_cfg: Any) -> None:
        """Test that repair repo without --yes raises BorgadmError."""
        with (
            patch.object(ba, "run_cmd", autospec=True) as mock_run_cmd,
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
            pytest.raises(ba.BorgadmError),
        ):
            ba.do_repair_repo(progress=False, yes=False)
        mock_run_cmd.assert_not_called()

    def test_repair_repo(self, mock_cfg: Any) -> None:
        """Test that repair repo calls borg check --repair."""
        with (
            patch.object(ba, "run_cmd", autospec=True) as mock_run_cmd,
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
        ):
            ba.do_repair_repo(progress=False, yes=True)
            mock_run_cmd.assert_called_once_with(
                [
                    "borg",
                    "check",
                    "--repair",
                    mock_cfg.BORG_REPO,
                ],
                allow_output=True,
                env={
                    **os.environ,
                    "BORG_CHECK_I_KNOW_WHAT_I_AM_DOING": "YES",
                },
            )

    def test_repair_repo_progress(self, mock_cfg: Any) -> None:
        """Test that repair repo passes --progress to borg."""
        with (
            patch.object(ba, "run_cmd", autospec=True) as mock_run_cmd,
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
        ):
            ba.do_repair_repo(progress=True, yes=True)
            mock_run_cmd.assert_called_once_with(
                [
                    "borg",
                    "check",
                    "--repair",
                    "--progress",
                    mock_cfg.BORG_REPO,
                ],
                allow_output=True,
                env={
                    **os.environ,
                    "BORG_CHECK_I_KNOW_WHAT_I_AM_DOING": "YES",
                },
            )


class TestDelete:
    """Test delete subcommand."""

    def test_delete_parses_timestamp(self) -> None:
        """Test delete subcommand parses a timestamp argument."""
        parser = ba.args_parser()
        args = parser.parse_args(["delete", "20250101_120000"])
        assert args.command == "delete"
        assert args.archive == "20250101_120000"
        assert args.latest is False

    def test_delete_parses_latest(self) -> None:
        """Test delete subcommand parses --latest flag."""
        parser = ba.args_parser()
        args = parser.parse_args(["delete", "--latest"])
        assert args.command == "delete"
        assert args.archive is None
        assert args.latest is True

    def test_delete_parses_archive_name(self) -> None:
        """Test delete subcommand parses a full archive name."""
        parser = ba.args_parser()
        args = parser.parse_args(["delete", "home-local-20250101_120000"])
        assert args.command == "delete"
        assert args.archive == "home-local-20250101_120000"

    def test_delete_latest_and_archive_errors(self) -> None:
        """Test that --latest with an archive is a parser error."""
        parser = ba.args_parser()
        args = parser.parse_args(["delete", "--latest", "20250101_120000"])
        with pytest.raises(SystemExit) as exc_info:
            args.validate(args)
        assert exc_info.value.code == 2

    def test_delete_no_args_errors(self) -> None:
        """Test that no archive and no --latest is a parser error."""
        parser = ba.args_parser()
        args = parser.parse_args(["delete"])
        with pytest.raises(SystemExit) as exc_info:
            args.validate(args)
        assert exc_info.value.code == 2

    def test_delete_latest(self, mock_cfg: Any) -> None:
        """Test deleting the latest full backup."""
        repo = mock_cfg.BORG_REPO
        backups = {
            "20250101_120000": [
                f"{repo}::home-set1-20250101_120000",
            ]
        }

        def list_backups_side_effect(
            latest: bool = False,
            partial: bool = False,
        ) -> dict[str, list[str]]:
            return backups

        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                side_effect=list_backups_side_effect,
            ),
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
        ):
            ba.do_delete(
                archive=None,
                dry_run=False,
                latest=True,
                progress=False,
            )
            mock_run.assert_called_once_with(
                [
                    "borg",
                    "delete",
                    "--stats",
                    repo,
                    "home-set1-20250101_120000",
                ],
                allow_output=False,
            )

    def test_delete_latest_no_backups(self, mock_cfg: Any) -> None:
        """Test --latest with no backups raises BorgadmError."""
        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                return_value={},
            ),
            pytest.raises(ba.BorgadmError),
        ):
            ba.do_delete(
                archive=None,
                dry_run=False,
                latest=True,
                progress=False,
            )

    def test_delete_by_timestamp(self, mock_cfg: Any) -> None:
        """Test deleting all archives at a timestamp."""
        repo = mock_cfg.BORG_REPO

        def list_backups_side_effect(
            partial: bool = False,
            latest: bool = False,
        ) -> dict[str, list[str]]:
            if partial:
                return {}
            return {
                "20250101_120000": [
                    f"{repo}::home-set1-20250101_120000",
                ]
            }

        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                side_effect=list_backups_side_effect,
            ),
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
        ):
            ba.do_delete(
                archive="20250101_120000",
                dry_run=False,
                latest=False,
                progress=False,
            )
            mock_run.assert_called_once_with(
                [
                    "borg",
                    "delete",
                    "--stats",
                    repo,
                    "home-set1-20250101_120000",
                ],
                allow_output=False,
            )

    def test_delete_by_timestamp_not_found(self, mock_cfg: Any) -> None:
        """Test deleting a nonexistent timestamp exits with error."""

        def list_backups_side_effect(
            partial: bool = False,
            latest: bool = False,
        ) -> dict[str, list[str]]:
            return {}

        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                side_effect=list_backups_side_effect,
            ),
            pytest.raises(ba.BorgadmError),
        ):
            ba.do_delete(
                archive="20250101_120000",
                dry_run=False,
                latest=False,
                progress=False,
            )

    def test_delete_by_archive_name(self, mock_cfg: Any) -> None:
        """Test deleting a single archive by full name."""
        repo = mock_cfg.BORG_REPO
        raw_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="home-set1-20250101_120000\n",
            stderr="",
        )
        with (
            patch.object(
                ba,
                "list_backups_raw",
                autospec=True,
                return_value=raw_result,
            ),
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
        ):
            ba.do_delete(
                archive="home-set1-20250101_120000",
                dry_run=False,
                latest=False,
                progress=False,
            )
            mock_run.assert_called_once_with(
                [
                    "borg",
                    "delete",
                    "--stats",
                    repo,
                    "home-set1-20250101_120000",
                ],
                allow_output=False,
            )

    def test_delete_by_archive_name_not_found(self, mock_cfg: Any) -> None:
        """Test deleting a nonexistent archive name exits with error."""
        raw_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="other-archive-20250101_120000\n",
            stderr="",
        )
        with (
            patch.object(
                ba,
                "list_backups_raw",
                autospec=True,
                return_value=raw_result,
            ),
            pytest.raises(ba.BorgadmError),
        ):
            ba.do_delete(
                archive="home-set1-20250101_120000",
                dry_run=False,
                latest=False,
                progress=False,
            )

    def test_delete_dry_run(self, mock_cfg: Any) -> None:
        """Test that --dry-run passes --dry-run to borg."""
        repo = mock_cfg.BORG_REPO
        raw_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="home-set1-20250101_120000\n",
            stderr="",
        )
        with (
            patch.object(
                ba,
                "list_backups_raw",
                autospec=True,
                return_value=raw_result,
            ),
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
        ):
            ba.do_delete(
                archive="home-set1-20250101_120000",
                dry_run=True,
                latest=False,
                progress=False,
            )
            mock_run.assert_called_once_with(
                [
                    "borg",
                    "delete",
                    "--dry-run",
                    repo,
                    "home-set1-20250101_120000",
                ],
                allow_output=True,
            )

    def test_delete_progress(self, mock_cfg: Any) -> None:
        """Test that --progress passes --progress to borg."""
        repo = mock_cfg.BORG_REPO
        raw_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="home-set1-20250101_120000\n",
            stderr="",
        )
        with (
            patch.object(
                ba,
                "list_backups_raw",
                autospec=True,
                return_value=raw_result,
            ),
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
        ):
            ba.do_delete(
                archive="home-set1-20250101_120000",
                dry_run=False,
                latest=False,
                progress=True,
            )
            mock_run.assert_called_once_with(
                [
                    "borg",
                    "delete",
                    "--progress",
                    "--stats",
                    repo,
                    "home-set1-20250101_120000",
                ],
                allow_output=True,
            )

    def test_delete_timestamp_includes_partial(self, mock_cfg: Any) -> None:
        """Test timestamp deletion includes partial archives."""
        repo = mock_cfg.BORG_REPO

        def list_backups_side_effect(
            partial: bool = False,
            latest: bool = False,
        ) -> dict[str, list[str]]:
            if partial:
                return {
                    "20250101_120000": [
                        f"{repo}::home-set1-20250101_120000",
                    ]
                }
            return {}

        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                side_effect=list_backups_side_effect,
            ),
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
        ):
            ba.do_delete(
                archive="20250101_120000",
                dry_run=False,
                latest=False,
                progress=False,
            )
            mock_run.assert_called_once_with(
                [
                    "borg",
                    "delete",
                    "--stats",
                    repo,
                    "home-set1-20250101_120000",
                ],
                allow_output=False,
            )

    def test_delete_latest_excludes_partial(self, mock_cfg: Any) -> None:
        """Test --latest only deletes full backups, not partials."""
        repo = mock_cfg.BORG_REPO

        def list_backups_side_effect(
            partial: bool = False,
            latest: bool = False,
        ) -> dict[str, list[str]]:
            if partial:
                # Partial archive exists at the same timestamp
                return {
                    "20250101_120000": [
                        f"{repo}::home-set2-20250101_120000",
                    ]
                }
            return {
                "20250101_120000": [
                    f"{repo}::home-set1-20250101_120000",
                ]
            }

        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                side_effect=list_backups_side_effect,
            ),
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
        ):
            ba.do_delete(
                archive=None,
                dry_run=False,
                latest=True,
                progress=False,
            )
            # Should only delete the full backup, not the partial
            mock_run.assert_called_once_with(
                [
                    "borg",
                    "delete",
                    "--stats",
                    repo,
                    "home-set1-20250101_120000",
                ],
                allow_output=False,
            )


class TestList:
    """Test list subcommand."""

    FULL_BACKUPS: dict[str, list[str]] = {
        "20250103_120000": ["foobar::home-set1-20250103_120000"],
        "20250102_120000": ["foobar::home-set1-20250102_120000"],
        "20250101_120000": ["foobar::home-set1-20250101_120000"],
    }
    PARTIAL_BACKUPS: dict[str, list[str]] = {
        "20250104_060000": ["foobar::home-set1-20250104_060000"],
    }

    def _list_side_effect(
        self,
        full_backups: dict[str, list[str]],
        partial_backups: dict[str, list[str]],
    ) -> Any:
        def side_effect(
            latest: bool = False, partial: bool = False
        ) -> dict[str, list[str]]:
            src = partial_backups if partial else full_backups
            if latest and src:
                first_key = next(iter(src))
                return {first_key: src[first_key]}
            return src

        return side_effect

    def _run_list(
        self,
        caplog: Any,
        full: dict[str, list[str]] | None = None,
        partial: dict[str, list[str]] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        if full is None:
            full = self.FULL_BACKUPS
        if partial is None:
            partial = self.PARTIAL_BACKUPS
        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                side_effect=self._list_side_effect(full, partial),
            ),
            patch.object(
                ba,
                "ts_to_keep",
                autospec=True,
                return_value=collections.OrderedDict(
                    {
                        "20250103_120000": "hour",
                        "20250102_120000": "day",
                    }
                ),
            ),
            caplog.at_level(logging.INFO),
        ):
            ba.do_list(
                latest=kwargs.get("latest", False),
                full_names=kwargs.get("full_names", False),
                include_partial=kwargs.get("include_partial", True),
                only_partial=kwargs.get("only_partial", False),
                keep_tags=kwargs.get("keep_tags", True),
            )
        return [r.message for r in caplog.records]

    def test_list_defaults(self, mock_cfg: Any, caplog: Any) -> None:
        """Default list shows full + partial with keep tags."""
        msgs = self._run_list(caplog)
        # All timestamps present (3 full + 1 partial)
        assert any("20250103_120000" in m for m in msgs)
        assert any("20250102_120000" in m for m in msgs)
        assert any("20250101_120000" in m for m in msgs)
        assert any("20250104_060000" in m for m in msgs)
        # Keep tags present
        assert any("(hour)" in m for m in msgs)
        assert any("(day)" in m for m in msgs)
        assert any("(prune)" in m for m in msgs)

    def test_list_no_keep_tags(self, mock_cfg: Any, caplog: Any) -> None:
        """--no-keep-tags omits keep tags."""
        msgs = self._run_list(caplog, keep_tags=False)
        assert any("20250103_120000" in m for m in msgs)
        assert not any("(hour)" in m for m in msgs)
        assert not any("(prune)" in m for m in msgs)

    def test_list_no_include_partial(self, mock_cfg: Any, caplog: Any) -> None:
        """--no-include-partial excludes partial backups."""
        msgs = self._run_list(caplog, include_partial=False)
        assert any("20250103_120000" in m for m in msgs)
        assert not any("20250104_060000" in m for m in msgs)

    def test_list_only_partial(self, mock_cfg: Any, caplog: Any) -> None:
        """--only-partial shows only partial backups."""
        msgs = self._run_list(caplog, only_partial=True)
        assert any("20250104_060000" in m for m in msgs)
        assert not any("20250103_120000" in m for m in msgs)

    def test_list_full_names(self, mock_cfg: Any, caplog: Any) -> None:
        """--full-names shows full archive names."""
        msgs = self._run_list(caplog, full_names=True)
        assert any("foobar::home-set1-20250103_120000" in m for m in msgs)

    def test_list_latest(self, mock_cfg: Any, caplog: Any) -> None:
        """--latest shows only the most recent backup."""
        msgs = self._run_list(caplog, latest=True)
        assert any("20250103_120000" in m for m in msgs)
        assert not any("20250102_120000" in m for m in msgs)
        assert not any("20250101_120000" in m for m in msgs)


class TestAutomate:
    """Test automate subcommand."""

    @pytest.fixture
    def automate_env(
        self, tmp_path: Path, mock_cfg: Any
    ) -> Iterator[tuple[Path, Any, Any]]:
        """Set up mocked environment for automate tests."""
        task_dir = tmp_path / "Library" / "LaunchAgents"
        task_dir.mkdir(parents=True)

        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", return_value="/usr/bin/tool"),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(ba, "create_plist_element", autospec=True),
            patch.object(
                ba, "create_plist_file", autospec=True
            ) as mock_create_file,
            patch.object(
                ba,
                "_wrapper_needs_rebuild",
                autospec=True,
                return_value=(False, ""),
            ),
            patch.object(
                ba,
                "_build_wrapper",
                autospec=True,
            ),
            patch.dict(os.environ, {"HOME": str(tmp_path)}),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            yield task_dir, mock_run, mock_create_file

    CURRENT_TASKS = {"create", "check-daily", "check-weekly"}

    def _created_task_names(self, mock_create_file: Any) -> set[str]:
        """Extract task names from create_plist_file mock calls."""
        return {
            Path(call.args[1]).stem.removeprefix("local.borgadm.")
            for call in mock_create_file.call_args_list
        }

    def test_enable_creates_current_plists(self, automate_env: Any) -> None:
        """Test that enable creates plists for current tasks."""
        _task_dir, _mock_run, mock_create_file = automate_env

        ba.do_automate_enable()

        created = self._created_task_names(mock_create_file)
        assert created == self.CURRENT_TASKS

    def test_enable_skips_legacy_tasks(self, automate_env: Any) -> None:
        """Test that enable does not create plists for legacy tasks."""
        _task_dir, _mock_run, mock_create_file = automate_env

        ba.do_automate_enable()

        created = self._created_task_names(mock_create_file)
        legacy_names = {"check_age", "check_all"}
        assert not created & legacy_names, (
            f"Legacy plists should not be created: {created & legacy_names}"
        )

    def test_disable_removes_current_plists(self, automate_env: Any) -> None:
        """Test that disable removes current plist files."""
        task_dir, _mock_run, _ = automate_env
        for name in self.CURRENT_TASKS:
            (task_dir / f"local.borgadm.{name}.plist").write_text("<plist/>")

        ba.do_automate_disable()

        remaining = list(task_dir.glob("*.plist"))
        assert remaining == [], f"Plists not removed: {remaining}"

    def test_disable_removes_legacy_plists(self, automate_env: Any) -> None:
        """Test that disable removes legacy plist files."""
        task_dir, _mock_run, _ = automate_env
        for name in ["check_age", "check_all"]:
            (task_dir / f"local.borgadm.{name}.plist").write_text("<plist/>")

        ba.do_automate_disable()

        remaining = list(task_dir.glob("*.plist"))
        assert remaining == [], f"Legacy plists not removed: {remaining}"

    def test_status_no_recommendation_when_all_enabled(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that status shows no recommendation when all enabled."""
        _task_dir, mock_run, _ = automate_env
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "123\t0\tlocal.borgadm.create\n"
                "456\t0\tlocal.borgadm.check-daily\n"
                "789\t0\tlocal.borgadm.check-weekly\n"
            ),
            stderr="",
        )

        with caplog.at_level(logging.INFO):
            ba.do_automate_status()

        assert not any("automate enable" in r.message for r in caplog.records)

    def test_status_recommends_enable_when_disabled(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that status recommends enable when all disabled."""
        _task_dir, _mock_run, _ = automate_env

        with caplog.at_level(logging.INFO):
            ba.do_automate_status()

        assert any("automate enable" in r.message for r in caplog.records)

    def test_status_recommends_enable_when_partial(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that status recommends enable when partially enabled."""
        _task_dir, mock_run, _ = automate_env
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="123\t0\tlocal.borgadm.create\n",
            stderr="",
        )

        with caplog.at_level(logging.INFO):
            ba.do_automate_status()

        assert any("automate enable" in r.message for r in caplog.records)

    def test_status_recommends_enable_on_legacy_tasks(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that status recommends enable for loaded legacy tasks."""
        _task_dir, mock_run, _ = automate_env
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "123\t0\tlocal.borgadm.check_age\n"
                "456\t0\tlocal.borgadm.check_all\n"
            ),
            stderr="",
        )

        with caplog.at_level(logging.INFO):
            ba.do_automate_status()

        legacy_msgs = [
            r for r in caplog.records if "legacy" in r.message.lower()
        ]
        # One message per legacy task
        assert len(legacy_msgs) >= 2
        # Summary should tell user to run enable
        assert any("automate enable" in r.message for r in caplog.records)

    def test_status_recommends_enable_on_unloaded_legacy(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that status recommends enable for unloaded legacy plists."""
        task_dir, _mock_run, _ = automate_env
        stale = task_dir / "local.borgadm.check_age.plist"
        stale.write_text("<plist/>")

        with caplog.at_level(logging.INFO):
            ba.do_automate_status()

        unloaded_msgs = [
            r
            for r in caplog.records
            if "unloaded" in r.message and "legacy" in r.message
        ]
        assert len(unloaded_msgs) >= 1

    @pytest.fixture
    def status_env(self, tmp_path: Path, mock_cfg: Any) -> Iterator[Any]:
        """Set up environment for status tests that need real plists."""
        task_dir = tmp_path / "Library" / "LaunchAgents"
        task_dir.mkdir(parents=True)

        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", return_value="/usr/bin/tool"),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(
                ba,
                "_wrapper_needs_rebuild",
                autospec=True,
                return_value=(False, ""),
            ),
            patch.dict(os.environ, {"HOME": str(tmp_path)}),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            yield mock_run

    def _write_current_plist(
        self, task: str, cfg: dict[str, Any], path: Path
    ) -> None:
        """Write a plist file matching current create_plist_element."""
        elem = ba.create_plist_element(
            task,
            cfg["args"],
            cfg["interval"],
            ba._launchd_env(),
            ba._task_log_path(task),
        )
        ba.create_plist_file(elem, path)

    def test_status_recommends_enable_on_outdated(
        self, status_env: Any, caplog: Any
    ) -> None:
        """Test that status recommends enable for outdated plists."""
        mock_run = status_env
        tasks, task2path = ba._automate_tasks()

        # Write stale plist content for all current tasks
        for task, cfg in tasks.items():
            if cfg.get("legacy", False):
                continue
            task2path[task].write_text("<plist>stale</plist>")

        # All current tasks loaded in launchd
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "123\t0\tlocal.borgadm.create\n"
                "456\t0\tlocal.borgadm.check-daily\n"
                "789\t0\tlocal.borgadm.check-weekly\n"
            ),
            stderr="",
        )

        with caplog.at_level(logging.INFO):
            ba.do_automate_status()

        outdated_msgs = [
            r for r in caplog.records if "outdated" in r.message.lower()
        ]
        # One message per outdated task
        assert len(outdated_msgs) >= 3
        # Summary should tell user to run enable
        assert any("automate enable" in r.message for r in caplog.records)

    def test_status_no_recommendation_when_current(
        self, status_env: Any, caplog: Any
    ) -> None:
        """Test no recommendation when plists match current config."""
        mock_run = status_env
        tasks, task2path = ba._automate_tasks()

        # Write current plist content for all current tasks
        for task, cfg in tasks.items():
            if cfg.get("legacy", False):
                continue
            self._write_current_plist(task, cfg, task2path[task])

        # All current tasks loaded in launchd
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "123\t0\tlocal.borgadm.create\n"
                "456\t0\tlocal.borgadm.check-daily\n"
                "789\t0\tlocal.borgadm.check-weekly\n"
            ),
            stderr="",
        )

        with caplog.at_level(logging.INFO):
            ba.do_automate_status()

        assert not any("outdated" in r.message.lower() for r in caplog.records)

    def test_enable_replaces_legacy_plists(self, automate_env: Any) -> None:
        """Test that enable removes legacy plists and creates current."""
        task_dir, _mock_run, mock_create_file = automate_env
        # Pre-populate legacy plists as if upgraded without disabling
        for name in ["check_age", "check_all"]:
            (task_dir / f"local.borgadm.{name}.plist").write_text("<plist/>")

        ba.do_automate_enable()

        # Legacy plists should be removed
        for name in ["check_age", "check_all"]:
            p = task_dir / f"local.borgadm.{name}.plist"
            assert not p.exists(), f"Legacy plist not removed: {p}"
        # Current plists should be created
        created = self._created_task_names(mock_create_file)
        assert created == self.CURRENT_TASKS

    def test_script_path_uses_wrapper_app(self) -> None:
        """Test that automation uses wrapper app path."""
        result = ba.automation_script_path()
        assert result.endswith(
            "Applications/BorgAdm.app/Contents/MacOS/BorgAdm"
        )

    def test_enable_compiles_wrapper(
        self, tmp_path: Path, mock_cfg: Any
    ) -> None:
        """Test that enable calls _build_wrapper when needed."""
        task_dir = tmp_path / "Library" / "LaunchAgents"
        task_dir.mkdir(parents=True)
        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", return_value="/usr/bin/tool"),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(ba, "create_plist_element", autospec=True),
            patch.object(ba, "create_plist_file", autospec=True),
            patch.object(
                ba,
                "_wrapper_needs_rebuild",
                autospec=True,
                return_value=(True, "test"),
            ),
            patch.object(
                ba,
                "_build_wrapper",
                autospec=True,
            ) as mock_build,
            patch.dict(os.environ, {"HOME": str(tmp_path)}),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            ba.do_automate_enable()
        mock_build.assert_called()

    def test_status_reports_wrapper_outdated(
        self, tmp_path: Path, mock_cfg: Any, caplog: Any
    ) -> None:
        """Test that status reports when wrapper needs rebuilding."""
        task_dir = tmp_path / "Library" / "LaunchAgents"
        task_dir.mkdir(parents=True)
        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", return_value="/usr/bin/tool"),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(
                ba,
                "_wrapper_needs_rebuild",
                autospec=True,
                return_value=(True, "test"),
            ),
            patch.object(ba, "_build_wrapper", autospec=True),
            patch.dict(os.environ, {"HOME": str(tmp_path)}),
            caplog.at_level(logging.INFO),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            ba.do_automate_status()
        assert any("wrapper" in r.message.lower() for r in caplog.records)
        assert any("automate enable" in r.message for r in caplog.records)

    def test_automate_exits_on_non_darwin(self, mock_cfg: Any) -> None:
        """Test that automate raises BorgadmError on non-Darwin."""
        with (
            patch.object(ba.platform, "system", return_value="Linux"),
            pytest.raises(ba.BorgadmError),
        ):
            ba.do_automate_enable()

    def test_automate_exits_without_plutil(self, mock_cfg: Any) -> None:
        """Test that automate raises when plutil is not found."""
        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", side_effect=lambda x: None),
            pytest.raises(ba.BorgadmError),
        ):
            ba.do_automate_enable()

    def test_automate_exits_without_launchctl(self, mock_cfg: Any) -> None:
        """Test that automate raises when launchctl is not found."""

        def which_side_effect(cmd: str) -> str | None:
            return "/usr/bin/plutil" if cmd == "plutil" else None

        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", side_effect=which_side_effect),
            pytest.raises(ba.BorgadmError),
        ):
            ba.do_automate_enable()

    def test_daily_task_runs_checks_independently(
        self, automate_env: Any
    ) -> None:
        """Daily task uses ';' not '&&' so all checks run."""
        _task_dir, _mock_run, _mock_create_file = automate_env
        tasks, _ = ba._automate_tasks()
        daily_args = tasks["check-daily"]["args"]
        shell_cmd = daily_args[2]
        assert "&&" not in shell_cmd, "Daily checks should use ';' not '&&'"
        assert "; " in shell_cmd
        assert "check age" in shell_cmd
        assert "check prune" in shell_cmd

    def test_weekly_task_only_runs_repo_and_archives(
        self, automate_env: Any
    ) -> None:
        """Weekly task runs only repo and archives checks."""
        _task_dir, _mock_run, _mock_create_file = automate_env
        tasks, _ = ba._automate_tasks()
        weekly_args = tasks["check-weekly"]["args"]
        shell_cmd = weekly_args[2]
        assert "check repo" in shell_cmd
        assert "check archives" in shell_cmd
        assert "check age" not in shell_cmd
        assert "check prune" not in shell_cmd
        assert "check-all" not in shell_cmd

    @staticmethod
    def _write_plist(
        path: Path, log_path: str, stderr_path: str | None = None
    ) -> None:
        """Write a minimal plist file with log path entries."""
        plist = ElementTree.Element("plist", version="1.0")
        d = ElementTree.SubElement(plist, "dict")
        ElementTree.SubElement(d, "key").text = "Label"
        ElementTree.SubElement(d, "string").text = "test"
        ElementTree.SubElement(d, "key").text = "StandardOutPath"
        ElementTree.SubElement(d, "string").text = log_path
        ElementTree.SubElement(d, "key").text = "StandardErrorPath"
        ElementTree.SubElement(d, "string").text = (
            stderr_path if stderr_path else log_path
        )
        tree = ElementTree.ElementTree(plist)
        tree.write(path, xml_declaration=True, encoding="UTF-8")

    def test_log_files_shows_default_logfile(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that log-files always includes the default log file."""
        _task_dir, _mock_run, _ = automate_env

        with caplog.at_level(logging.INFO):
            ba.do_automate_log_files()

        messages = [r.message for r in caplog.records]
        assert str(ba.LOGFILE) in messages

    def test_log_files_default_logfile_listed_first(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that the default log file is listed first."""
        task_dir, _mock_run, _ = automate_env
        plist_path = task_dir / "local.borgadm.create.plist"
        self._write_plist(plist_path, "/tmp/logs/create.log")

        with caplog.at_level(logging.INFO):
            ba.do_automate_log_files()

        messages = [r.message for r in caplog.records]
        assert messages[0] == str(ba.LOGFILE)

    def test_log_files_default_logfile_not_duplicated(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test default log file isn't duplicated if a plist uses it."""
        task_dir, _mock_run, _ = automate_env
        plist_path = task_dir / "local.borgadm.create.plist"
        self._write_plist(plist_path, str(ba.LOGFILE))

        with caplog.at_level(logging.INFO):
            ba.do_automate_log_files()

        matches = [r for r in caplog.records if r.message == str(ba.LOGFILE)]
        assert len(matches) == 1

    def test_log_files_shows_paths_from_plists(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that log-files shows paths from existing plists."""
        task_dir, _mock_run, _ = automate_env
        for name in self.CURRENT_TASKS:
            plist_path = task_dir / f"local.borgadm.{name}.plist"
            log = f"/tmp/logs/{name}.log"
            self._write_plist(plist_path, log)

        with caplog.at_level(logging.INFO):
            ba.do_automate_log_files()

        for name in self.CURRENT_TASKS:
            assert any(
                f"/tmp/logs/{name}.log" in r.message for r in caplog.records
            )

    def test_log_files_deduplicates_stdout_stderr(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that identical stdout/stderr paths appear only once."""
        task_dir, _mock_run, _ = automate_env
        plist_path = task_dir / "local.borgadm.create.plist"
        self._write_plist(plist_path, "/tmp/logs/create.log")

        with caplog.at_level(logging.INFO):
            ba.do_automate_log_files()

        matches = [
            r for r in caplog.records if "/tmp/logs/create.log" in r.message
        ]
        assert len(matches) == 1

    def test_log_files_shows_distinct_stderr(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that distinct stderr path is also shown."""
        task_dir, _mock_run, _ = automate_env
        plist_path = task_dir / "local.borgadm.create.plist"
        self._write_plist(plist_path, "/tmp/logs/out.log", "/tmp/logs/err.log")

        with caplog.at_level(logging.INFO):
            ba.do_automate_log_files()

        messages = [r.message for r in caplog.records]
        assert any("/tmp/logs/out.log" in m for m in messages)
        assert any("/tmp/logs/err.log" in m for m in messages)

    def test_log_files_no_plists(self, automate_env: Any, caplog: Any) -> None:
        """Test that default log file is shown even with no plists."""
        _task_dir, _mock_run, _ = automate_env

        with caplog.at_level(logging.INFO):
            ba.do_automate_log_files()

        messages = [r.message for r in caplog.records]
        assert str(ba.LOGFILE) in messages
        assert len(messages) == 1

    def test_log_files_reads_legacy_plists(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that log-files reads legacy plist files too."""
        task_dir, _mock_run, _ = automate_env
        plist_path = task_dir / "local.borgadm.check_age.plist"
        self._write_plist(plist_path, "/tmp/logs/check_age.log")

        with caplog.at_level(logging.INFO):
            ba.do_automate_log_files()

        assert any(
            "/tmp/logs/check_age.log" in r.message for r in caplog.records
        )


class TestCheck:
    """Test check subcommands."""

    @staticmethod
    def _check_subcommands() -> set[str]:
        """Discover check subcommands from the parser."""
        parser = ba.args_parser()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                check_parser = action.choices.get("check")
                if check_parser:
                    for sub in check_parser._actions:
                        if isinstance(sub, argparse._SubParsersAction):
                            return set(sub.choices.keys())
        return set()

    def test_check_subcommands_accept_common_args(self) -> None:
        """Verify all check subcommands accept --enable-notifications."""
        parser = ba.args_parser()
        for action in self._check_subcommands():
            # Should parse without error
            args = parser.parse_args(
                ["check", action, "--enable-notifications"]
            )
            assert args.enable_notifications is True, (
                f"check {action} did not accept --enable-notifications"
            )

    def test_check_all_runs_all_checks(self, mock_cfg: Any) -> None:
        """Verify do_check_all calls every individual check function."""
        actions = self._check_subcommands() - {"all"}
        assert actions, "No individual check subcommands found"

        mocks: dict[str, Any] = {}
        patches = []
        for action in actions:
            p = patch.object(ba, f"do_check_{action}", autospec=True)
            mocks[action] = p.start()
            patches.append(p)

        try:
            ba.do_check_all(progress=False)
        finally:
            for p in patches:
                p.stop()

        for action, mock_fn in mocks.items():
            assert mock_fn.called, (
                f"do_check_all did not call do_check_{action}"
            )

    def test_check_age_no_backups(self, mock_cfg: Any) -> None:
        """Test check age raises CheckNoBackupsError."""
        with (
            patch.object(ba, "list_backups", autospec=True, return_value={}),
            pytest.raises(ba.CheckNoBackupsError),
        ):
            ba.do_check_age()

    def test_check_age_too_old(self, mock_cfg: Any) -> None:
        """Test check age raises CheckAgeError."""
        old_ts = (datetime.now() - timedelta(hours=48)).strftime(
            "%Y%m%d_%H%M%S"
        )
        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                return_value={old_ts: ["repo::backup"]},
            ),
            pytest.raises(ba.CheckAgeError),
        ):
            ba.do_check_age()

    def test_check_age_ok(self, mock_cfg: Any) -> None:
        """Test check age succeeds when backup is recent."""
        recent_ts = (datetime.now() - timedelta(hours=1)).strftime(
            "%Y%m%d_%H%M%S"
        )
        with patch.object(
            ba,
            "list_backups",
            autospec=True,
            return_value={recent_ts: ["repo::backup"]},
        ):
            ba.do_check_age()  # Should not raise

    def test_check_archives_no_backups(self, mock_cfg: Any) -> None:
        """Test check archives raises CheckNoBackupsError."""
        with (
            patch.object(ba, "list_backups", autospec=True, return_value={}),
            pytest.raises(ba.CheckNoBackupsError),
        ):
            ba.do_check_archives(progress=False)

    def test_check_prune_partial_archives(self, mock_cfg: Any) -> None:
        """Test check prune fails on partial archives."""

        def list_backups_side_effect(
            partial: bool = False,
        ) -> dict[str, list[str]]:
            if partial:
                return {
                    "20250101_000000": ["foobar::home-set1-20250101_000000"]
                }
            return {}

        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                side_effect=list_backups_side_effect,
            ),
            pytest.raises(ba.CheckPruneError),
        ):
            ba.do_check_prune()

    def test_check_prune_unpruned_backups(self, mock_cfg: Any) -> None:
        """Test check prune fails when old backups need pruning."""
        mock_cfg.PRUNE_KEEP_HOURLY = 1
        mock_cfg.PRUNE_KEEP_DAILY = 0
        mock_cfg.PRUNE_KEEP_WEEKLY = 0
        mock_cfg.PRUNE_KEEP_MONTHLY = 0
        mock_cfg.PRUNE_KEEP_YEARLY = 0

        def list_backups_side_effect(
            partial: bool = False,
        ) -> dict[str, list[str]]:
            if partial:
                return {}
            # Two backups but only 1 hourly kept
            return {
                "20250101_020000": ["foobar::home-set1-20250101_020000"],
                "20250101_010000": ["foobar::home-set1-20250101_010000"],
            }

        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                side_effect=list_backups_side_effect,
            ),
            pytest.raises(ba.CheckPruneError),
        ):
            ba.do_check_prune()

    def test_check_prune_ok(self, mock_cfg: Any) -> None:
        """Test check prune succeeds when no pruning needed."""
        mock_cfg.PRUNE_KEEP_HOURLY = 24
        mock_cfg.PRUNE_KEEP_DAILY = 7
        mock_cfg.PRUNE_KEEP_WEEKLY = 4
        mock_cfg.PRUNE_KEEP_MONTHLY = 12
        mock_cfg.PRUNE_KEEP_YEARLY = 2

        def list_backups_side_effect(
            partial: bool = False,
        ) -> dict[str, list[str]]:
            if partial:
                return {}
            return {"20250101_000000": ["foobar::home-set1-20250101_000000"]}

        with patch.object(
            ba,
            "list_backups",
            autospec=True,
            side_effect=list_backups_side_effect,
        ):
            ba.do_check_prune()  # Should not raise

    def test_warning_exit_triggers_notification(self, mock_cfg: Any) -> None:
        """Verify osascript_notify is called when _warning_occurred."""
        original_warning = getattr(ba, "_warning_occurred")
        original_title = getattr(ba, "_notify_title")
        try:
            setattr(ba, "_warning_occurred", True)
            with (
                patch.object(
                    ba, "osascript_notify", autospec=True
                ) as mock_notify,
                patch(
                    "sys.argv",
                    ["borgadm", "check", "age"],
                ),
                patch.object(
                    ba,
                    "Config",
                    side_effect=mock_config_constructor(mock_cfg),
                ),
                patch.object(
                    ba,
                    "initialize_logger",
                    autospec=True,
                ),
                patch.object(
                    ba,
                    "initialize_borg_environment",
                    autospec=True,
                ),
                patch.dict(
                    ba.COMMAND_CALLBACKS,
                    {"check age": lambda **_: None},
                ),
            ):
                ba.main(
                    command="check",
                    config=str(ba.CONFIG),
                    verbose=False,
                    timestamp_messages=False,
                    enable_notifications=True,
                    args_dict={"action": "age"},
                )
            mock_notify.assert_called_once()
        finally:
            setattr(ba, "_warning_occurred", original_warning)
            setattr(ba, "_notify_title", original_title)


class TestNotifyTitle:
    """Test osascript_notify uses _notify_title."""

    def test_notify_title_default(self) -> None:
        """Default _notify_title is the script basename."""
        assert getattr(ba, "_notify_title") == ba.BASENAME

    def test_notify_title_used_in_dialog(self) -> None:
        """osascript_notify uses _notify_title in the dialog title."""
        original_title = getattr(ba, "_notify_title")
        original_buffer = getattr(ba, "_logger_buffer", None)
        try:
            setattr(ba, "_notify_title", "borgadm check repo")
            setattr(ba, "_logger_buffer", io.StringIO("test error"))
            with (
                patch.object(ba, "has_tty", return_value=False),
                patch.object(
                    ba.shutil, "which", return_value="/usr/bin/osascript"
                ),
                patch.object(
                    ba.subprocess, "Popen", autospec=True
                ) as mock_popen,
            ):
                ba.osascript_notify()
            script_arg: str = mock_popen.call_args[0][0][2]
            assert '"borgadm check repo error"' in script_arg
        finally:
            setattr(ba, "_notify_title", original_title)
            if original_buffer is not None:
                setattr(ba, "_logger_buffer", original_buffer)

    def test_main_sets_notify_title(self, mock_cfg: Any) -> None:
        """main() sets _notify_title to 'borgadm <command>'."""
        original_title = getattr(ba, "_notify_title")
        try:
            with (
                patch(
                    "sys.argv",
                    ["borgadm", "check", "repo"],
                ),
                patch.object(
                    ba,
                    "Config",
                    side_effect=mock_config_constructor(mock_cfg),
                ),
                patch.object(
                    ba,
                    "initialize_logger",
                    autospec=True,
                ),
                patch.object(
                    ba,
                    "initialize_borg_environment",
                    autospec=True,
                ),
                patch.dict(
                    ba.COMMAND_CALLBACKS,
                    {"check repo": lambda **_: None},
                ),
            ):
                ba.main(
                    command="check",
                    config=str(ba.CONFIG),
                    verbose=False,
                    timestamp_messages=False,
                    enable_notifications=False,
                    args_dict={"action": "repo"},
                )
            assert getattr(ba, "_notify_title") == "borgadm check repo"
        finally:
            setattr(ba, "_notify_title", original_title)


class TestTimestampMessages:
    """Test --timestamp-messages flag."""

    def test_timestamp_messages_adds_timestamps(self, tmp_path: Path) -> None:
        """Test that timestamp_messages adds asctime to formatters."""
        root = logging.getLogger()
        old_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            logfile = tmp_path / "test.log"
            ba.initialize_logger(str(logfile), timestamp_messages=True)
            for h in root.handlers:
                if not isinstance(h, logging.StreamHandler):
                    continue
                if getattr(h, "stream", None) is sys.stdout:
                    assert h.formatter is not None
                    fmt = h.formatter._fmt or ""
                    assert "asctime" in fmt
                elif getattr(h, "stream", None) is sys.stderr:
                    assert h.formatter is not None
                    fmt = h.formatter._fmt or ""
                    assert "asctime" in fmt
        finally:
            root.handlers = old_handlers

    def test_default_no_timestamps_on_stdout(self, tmp_path: Path) -> None:
        """Test that stdout has no timestamps by default."""
        root = logging.getLogger()
        old_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            logfile = tmp_path / "test.log"
            ba.initialize_logger(str(logfile))
            for h in root.handlers:
                if not isinstance(h, logging.StreamHandler):
                    continue
                if getattr(h, "stream", None) is sys.stdout:
                    assert h.formatter is not None
                    fmt = h.formatter._fmt or ""
                    assert "asctime" not in fmt
        finally:
            root.handlers = old_handlers


class TestStartEndMarkers:
    """Test start/end timing markers for repo-operating commands."""

    def test_timed_command_emits_start_and_end(
        self, mock_cfg: Any, caplog: Any
    ) -> None:
        """Test that repo-operating commands emit start/end."""
        with (
            patch("sys.argv", ["borgadm", "break-lock"]),
            patch.object(
                ba, "Config", side_effect=mock_config_constructor(mock_cfg)
            ),
            patch.object(ba, "initialize_logger", autospec=True),
            patch.object(ba, "initialize_borg_environment", autospec=True),
            patch.dict(
                ba.COMMAND_CALLBACKS,
                {"break-lock": lambda **_: None},
            ),
            caplog.at_level(logging.INFO),
        ):
            ba.main(
                command="break-lock",
                config=str(ba.CONFIG),
                verbose=False,
                timestamp_messages=False,
                enable_notifications=False,
                args_dict={},
            )

        assert any(
            "borgadm break-lock: started" in r.message for r in caplog.records
        )
        assert any(
            "borgadm break-lock: finished (elapsed:" in r.message
            for r in caplog.records
        )

    def test_quick_command_no_timing(self, mock_cfg: Any, caplog: Any) -> None:
        """Test that quick commands don't emit start/end."""
        with (
            patch("sys.argv", ["borgadm", "environment"]),
            patch.object(
                ba, "Config", side_effect=mock_config_constructor(mock_cfg)
            ),
            patch.object(ba, "initialize_logger", autospec=True),
            patch.object(ba, "initialize_borg_environment", autospec=True),
            patch.dict(
                ba.COMMAND_CALLBACKS,
                {"environment": lambda **_: None},
            ),
            caplog.at_level(logging.INFO),
        ):
            ba.main(
                command="environment",
                config=str(ba.CONFIG),
                verbose=False,
                timestamp_messages=False,
                enable_notifications=False,
                args_dict={},
            )

        assert not any("started" in r.message for r in caplog.records)
        assert not any("finished" in r.message for r in caplog.records)

    def test_timed_command_includes_action(
        self, mock_cfg: Any, caplog: Any
    ) -> None:
        """Test that action name is included in timing message."""
        with (
            patch("sys.argv", ["borgadm", "check", "age"]),
            patch.object(
                ba, "Config", side_effect=mock_config_constructor(mock_cfg)
            ),
            patch.object(ba, "initialize_logger", autospec=True),
            patch.object(ba, "initialize_borg_environment", autospec=True),
            patch.dict(
                ba.COMMAND_CALLBACKS,
                {"check age": lambda **_: None},
            ),
            caplog.at_level(logging.INFO),
        ):
            ba.main(
                command="check",
                config=str(ba.CONFIG),
                verbose=False,
                timestamp_messages=False,
                enable_notifications=False,
                args_dict={"action": "age"},
            )

        assert any(
            "borgadm check age: started" in r.message for r in caplog.records
        )
        assert any(
            "borgadm check age: finished (elapsed:" in r.message
            for r in caplog.records
        )

    def test_timed_command_emits_end_on_error(
        self, mock_cfg: Any, caplog: Any
    ) -> None:
        """Test that finished is emitted even on exception."""
        with (
            patch("sys.argv", ["borgadm", "compact"]),
            patch.object(
                ba, "Config", side_effect=mock_config_constructor(mock_cfg)
            ),
            patch.object(ba, "initialize_logger", autospec=True),
            patch.object(ba, "initialize_borg_environment", autospec=True),
            patch.dict(
                ba.COMMAND_CALLBACKS,
                {"compact": Mock(side_effect=ba.BorgadmError("test error"))},
            ),
            patch.object(ba, "osascript_notify", autospec=True),
            caplog.at_level(logging.INFO),
            pytest.raises(ba.BorgadmError),
        ):
            ba.main(
                command="compact",
                config=str(ba.CONFIG),
                verbose=False,
                timestamp_messages=False,
                enable_notifications=False,
                args_dict={},
            )

        assert any(
            "borgadm compact: finished (elapsed:" in r.message
            for r in caplog.records
        )


class TestAutomateTimestampFlag:
    """Test that automation plists include --timestamp-messages."""

    def test_enable_includes_timestamp_messages(
        self, tmp_path: Path, mock_cfg: Any
    ) -> None:
        """Test that plist args include --timestamp-messages."""
        task_dir = tmp_path / "Library" / "LaunchAgents"
        task_dir.mkdir(parents=True)
        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", return_value="/usr/bin/tool"),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(ba, "create_plist_element", autospec=True) as mock_cpe,
            patch.object(ba, "create_plist_file", autospec=True),
            patch.dict(os.environ, {"HOME": str(tmp_path)}),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            ba.do_automate_enable()

        for call in mock_cpe.call_args_list:
            task_args: list[str] = call.args[1]
            args_str = " ".join(task_args)
            assert "--timestamp-messages" in args_str, (
                f"--timestamp-messages missing from: {task_args}"
            )


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

    def test_zero_keep_retains_nothing(self, mock_cfg: Any) -> None:
        """Test that keep=0 for an interval keeps nothing for it."""
        mock_cfg.PRUNE_KEEP_HOURLY = 1
        mock_cfg.PRUNE_KEEP_DAILY = 0
        mock_cfg.PRUNE_KEEP_WEEKLY = 0
        mock_cfg.PRUNE_KEEP_MONTHLY = 0
        mock_cfg.PRUNE_KEEP_YEARLY = 0

        # Two hourly timestamps — only 1 hourly kept, nothing
        # from other intervals since they're all 0
        ts_all = {"20250101_010000", "20250101_020000"}
        ts_keep = ba.ts_to_keep(ts_all)
        assert len(ts_keep) == 1
        assert "20250101_020000" in ts_keep

    def test_all_zero_keep_is_config_error(self) -> None:
        """Test that all keep=0 is rejected as a config error."""
        config_file = tempfile.NamedTemporaryFile(mode="w+", delete=False)
        config_file.write(
            'BORG_REPO = "foobar"\n'
            'BACKUP_SETS = { "set1": {"paths": ["foo"]} }\n'
            "PRUNE_KEEP_HOURLY = 0\n"
            "PRUNE_KEEP_DAILY = 0\n"
            "PRUNE_KEEP_WEEKLY = 0\n"
            "PRUNE_KEEP_MONTHLY = 0\n"
            "PRUNE_KEEP_YEARLY = 0\n"
        )
        config_file.flush()

        with pytest.raises(ba.ConfigError):
            ba.Config(config_file.name, {})


class TestBrokenPipeHandling:
    """Test that broken pipes are handled gracefully."""

    def test_no_broken_pipe_error_when_piped_to_head(
        self, tmp_path: Path
    ) -> None:
        """Test that piping output to head doesn't cause BrokenPipeError."""
        # Create a script that imports borgadm (which sets up the SIGPIPE
        # handler) and outputs many lines via logging to stdout.
        # borgadm has no .py extension so we must use importlib to load it.
        script_file = tmp_path / "test_sigpipe.py"
        script_file.write_text(f"""
import importlib.machinery
import importlib.util
import logging
import sys

# Load borgadm module (no .py extension, so use importlib)
script_path = {str(REPO_ROOT / "bin" / "borgadm")!r}
loader = importlib.machinery.SourceFileLoader("borgadm", script_path)
spec = importlib.util.spec_from_loader("borgadm", loader)
borgadm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(borgadm)

# Now test logging output with many lines
logger = logging.getLogger("test_sigpipe")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)
for i in range(1000):
    logger.info(f"line {{i}}")
""")
        # Pipe to head -1 which will close the pipe after reading one line
        result = subprocess.run(
            f"python3 {script_file} | head -1",
            shell=True,
            capture_output=True,
            text=True,
        )
        # The key assertion: no Python exceptions in stderr
        assert "BrokenPipeError" not in result.stderr, (
            f"BrokenPipeError found in stderr:\n{result.stderr}"
        )
        assert "Traceback" not in result.stderr, (
            f"Traceback found in stderr:\n{result.stderr}"
        )
        # First line should be in output
        assert "line 0" in result.stdout


class TestCreatePlistElement:
    """Test that create_plist_element produces correct plist fields."""

    def _plist_dict(
        self, element: ElementTree.Element
    ) -> dict[str, str | bool | int]:
        """Parse plist element into a flat dict of key-value pairs."""
        result: dict[str, str | bool | int] = {}
        dict_elem = element.find("dict")
        assert dict_elem is not None
        keys = list(dict_elem)
        i = 0
        while i < len(keys):
            if keys[i].tag == "key":
                key_text = keys[i].text or ""
                val_elem = keys[i + 1]
                if val_elem.tag == "string":
                    result[key_text] = val_elem.text or ""
                elif val_elem.tag == "integer":
                    result[key_text] = int(val_elem.text or "0")
                elif val_elem.tag == "true":
                    result[key_text] = True
                elif val_elem.tag == "false":
                    result[key_text] = False
                i += 2
            else:
                i += 1
        return result

    def test_scheduling_fields(self) -> None:
        """Verify ProcessType, Nice, LowPriorityIO, PreventSleep."""
        elem = ba.create_plist_element(
            task="test",
            args=["/usr/bin/true"],
            interval=3600,
            env={},
            log_path="/dev/null",
        )
        d = self._plist_dict(elem)
        assert d["ProcessType"] == "Interactive"
        assert d["Nice"] == 0
        assert d["LowPriorityIO"] is False
        assert d["PreventSleep"] is True


class TestCli:
    """Test cli() entry point."""

    def test_cli_returns_warning(self, mock_cfg: Any) -> None:
        """cli() returns WARNING when _warning_occurred is set."""
        original = getattr(ba, "_warning_occurred")
        try:
            with (
                patch("sys.argv", ["borgadm", "environment"]),
                patch.object(ba, "main", autospec=True),
            ):
                setattr(ba, "_warning_occurred", True)
                assert ba.cli() == ba.ExitCode.WARNING
        finally:
            setattr(ba, "_warning_occurred", original)


class TestWrapperRebuild:
    """Test _wrapper_needs_rebuild() and _build_wrapper()."""

    @pytest.fixture
    def wrapper_env(self, tmp_path: Path) -> Iterator[tuple[Path, Path, Path]]:
        """Set up source, binary, and hash paths in tmp_path."""
        src = tmp_path / "BorgAdm.c"
        binary = tmp_path / "BorgAdm"
        hash_file = tmp_path / ".BorgAdm.source-sha256"
        with (
            patch.object(
                ba,
                "_wrapper_source_path",
                autospec=True,
                return_value=src,
            ),
            patch.object(
                ba,
                "_wrapper_binary_path",
                autospec=True,
                return_value=binary,
            ),
            patch.object(
                ba,
                "_wrapper_hash_path",
                autospec=True,
                return_value=hash_file,
            ),
        ):
            yield src, binary, hash_file

    def test_needs_rebuild_no_binary(
        self, wrapper_env: tuple[Path, Path, Path]
    ) -> None:
        """Rebuild needed when binary does not exist."""
        src, _binary, _hash_file = wrapper_env
        src.write_text("int main(){}")
        rebuild, reason = ba._wrapper_needs_rebuild()
        assert rebuild is True
        assert "binary" in reason

    def test_needs_rebuild_no_hash(
        self, wrapper_env: tuple[Path, Path, Path]
    ) -> None:
        """Rebuild needed when hash file does not exist."""
        src, binary, _hash_file = wrapper_env
        src.write_text("int main(){}")
        binary.write_text("binary")
        rebuild, reason = ba._wrapper_needs_rebuild()
        assert rebuild is True
        assert "hash" in reason

    def test_needs_rebuild_hash_mismatch(
        self, wrapper_env: tuple[Path, Path, Path]
    ) -> None:
        """Rebuild needed when source hash differs from stored."""
        src, binary, hash_file = wrapper_env
        src.write_text("int main(){}")
        binary.write_text("binary")
        hash_file.write_text("stale_hash\n")
        rebuild, reason = ba._wrapper_needs_rebuild()
        assert rebuild is True
        assert "mismatch" in reason

    def test_needs_rebuild_current(
        self, wrapper_env: tuple[Path, Path, Path]
    ) -> None:
        """No rebuild when hash matches."""
        src, binary, hash_file = wrapper_env
        src.write_text("int main(){}")
        binary.write_text("binary")
        hash_file.write_text(ba._source_sha256(src) + "\n")
        rebuild, reason = ba._wrapper_needs_rebuild()
        assert rebuild is False
        assert reason == ""

    def test_needs_rebuild_no_source(
        self, wrapper_env: tuple[Path, Path, Path]
    ) -> None:
        """Raises BorgadmError when source is missing."""
        _src, _binary, _hash_file = wrapper_env
        # src not created → doesn't exist
        with pytest.raises(ba.BorgadmError, match="source missing"):
            ba._wrapper_needs_rebuild()

    def test_build_calls_cc_and_codesign(
        self, wrapper_env: tuple[Path, Path, Path]
    ) -> None:
        """Build compiles source and signs the app bundle."""
        src, binary, _hash_file = wrapper_env
        src.write_text("int main(){}")
        with (
            patch.object(ba.shutil, "which", return_value="/usr/bin/cc"),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(
                ba,
                "_wrapper_app_path",
                autospec=True,
                return_value=Path("/fake/BorgAdm.app"),
            ),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            ba._build_wrapper()

        calls = [c.args[0] for c in mock_run.call_args_list]
        # First call: cc
        assert calls[0][0] == "cc"
        assert str(src) in calls[0]
        assert str(binary) in calls[0]
        # Second call: codesign
        assert calls[1][0] == "codesign"

    def test_build_writes_hash(
        self, wrapper_env: tuple[Path, Path, Path]
    ) -> None:
        """Build writes source hash after compilation."""
        src, _binary, hash_file = wrapper_env
        src.write_text("int main(){}")
        with (
            patch.object(ba.shutil, "which", return_value="/usr/bin/cc"),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(
                ba,
                "_wrapper_app_path",
                autospec=True,
                return_value=Path("/fake/BorgAdm.app"),
            ),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            ba._build_wrapper()

        assert hash_file.exists()
        assert hash_file.read_text().strip() == ba._source_sha256(src)

    def test_build_raises_without_cc(
        self, wrapper_env: tuple[Path, Path, Path]
    ) -> None:
        """Build raises BorgadmError when cc not found."""
        src, _binary, _hash_file = wrapper_env
        src.write_text("int main(){}")
        with (
            patch.object(ba.shutil, "which", return_value=None),
            pytest.raises(ba.BorgadmError, match="cc.*not found"),
        ):
            ba._build_wrapper()


@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="BorgAdm wrapper uses macOS-specific APIs (mach-o/dyld.h)",
)
class TestWrapperBinary:
    """Integration tests for the compiled BorgAdm wrapper binary."""

    WRAPPER_SOURCE = REPO_ROOT / (
        "Applications/BorgAdm.app/Contents/MacOS/BorgAdm.c"
    )

    @pytest.fixture
    def wrapper_tree(self, tmp_path: Path) -> Iterator[tuple[Path, Path, Path]]:
        """Build a temp directory tree matching the expected layout.

        Compiles BorgAdm.c, creates a mock borgadm, and yields
        (binary_path, mock_borgadm_path, fake_home).
        """
        # Mirror: tmp/Applications/BorgAdm.app/Contents/MacOS/
        macos_dir = (
            tmp_path / "Applications" / "BorgAdm.app" / "Contents" / "MacOS"
        )
        macos_dir.mkdir(parents=True)
        binary = macos_dir / "BorgAdm"

        # Compile the real source into the temp tree
        result = subprocess.run(
            [
                "cc",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-O2",
                "-o",
                str(binary),
                str(self.WRAPPER_SOURCE),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"

        # Create mock borgadm at tmp/bin/borgadm
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        mock_borgadm = bin_dir / "borgadm"
        mock_borgadm.write_text(
            '#!/bin/bash\necho "$@" > "$(dirname "$0")/../args.txt"\n'
        )
        mock_borgadm.chmod(0o755)

        # Fake HOME (no TCC dir → FDA check sees ENOENT → proceeds)
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()

        yield binary, mock_borgadm, fake_home

    def test_source_compiles_to_macho(
        self, wrapper_tree: tuple[Path, Path, Path]
    ) -> None:
        """Compiled wrapper is a Mach-O binary."""
        binary, _, _ = wrapper_tree
        result = subprocess.run(
            ["file", str(binary)],
            capture_output=True,
            text=True,
        )
        assert "Mach-O" in result.stdout

    def test_source_compiles_warning_clean(self, tmp_path: Path) -> None:
        """Source compiles with no warnings under -Wall -Wextra."""
        binary = tmp_path / "BorgAdm"
        result = subprocess.run(
            [
                "cc",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-pedantic",
                "-O2",
                "-o",
                str(binary),
                str(self.WRAPPER_SOURCE),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Warnings or errors:\n{result.stderr}"

    def test_forwards_args_to_borgadm(
        self, wrapper_tree: tuple[Path, Path, Path]
    ) -> None:
        """Wrapper forwards command-line arguments to borgadm."""
        binary, _, fake_home = wrapper_tree
        args_file = binary.parent.parent.parent.parent.parent / "args.txt"
        result = subprocess.run(
            [str(binary), "create", "--dry-run"],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(fake_home)},
        )
        assert result.returncode == 0, f"Wrapper failed: {result.stderr}"
        assert args_file.exists()
        assert args_file.read_text().strip() == "create --dry-run"

    def test_forwards_no_args(
        self, wrapper_tree: tuple[Path, Path, Path]
    ) -> None:
        """Wrapper runs borgadm with no extra args when none given."""
        binary, _, fake_home = wrapper_tree
        args_file = binary.parent.parent.parent.parent.parent / "args.txt"
        result = subprocess.run(
            [str(binary)],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(fake_home)},
        )
        assert result.returncode == 0
        assert args_file.exists()
        # No args → empty line
        assert args_file.read_text().strip() == ""

    def test_fda_check_denied_exits(
        self, wrapper_tree: tuple[Path, Path, Path]
    ) -> None:
        """Wrapper exits with code 77 when FDA is denied."""
        binary, _, fake_home = wrapper_tree
        # Create a TCC dir that can't be opened
        tcc_dir = (
            fake_home / "Library" / "Application Support" / "com.apple.TCC"
        )
        tcc_dir.mkdir(parents=True)
        tcc_dir.chmod(0o000)
        try:
            result = subprocess.run(
                [str(binary)],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": str(fake_home)},
            )
            assert result.returncode == 77
            assert "Full Disk Access" in result.stderr
        finally:
            tcc_dir.chmod(0o700)

    def test_missing_borgadm_exits_with_error(
        self, wrapper_tree: tuple[Path, Path, Path]
    ) -> None:
        """Wrapper exits with error when borgadm is not found."""
        binary, mock_borgadm, fake_home = wrapper_tree
        mock_borgadm.unlink()
        result = subprocess.run(
            [str(binary)],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(fake_home)},
        )
        assert result.returncode != 0
        assert "borgadm" in result.stderr.lower()

    def test_rejects_world_writable_borgadm(
        self, wrapper_tree: tuple[Path, Path, Path]
    ) -> None:
        """Wrapper refuses to exec borgadm if writable by others."""
        binary, mock_borgadm, fake_home = wrapper_tree
        mock_borgadm.chmod(0o757)
        try:
            result = subprocess.run(
                [str(binary)],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": str(fake_home)},
            )
            assert result.returncode != 0
            assert "writable" in result.stderr.lower()
        finally:
            mock_borgadm.chmod(0o755)

    def test_rejects_group_writable_borgadm(
        self, wrapper_tree: tuple[Path, Path, Path]
    ) -> None:
        """Wrapper refuses to exec borgadm if writable by group."""
        binary, mock_borgadm, fake_home = wrapper_tree
        mock_borgadm.chmod(0o775)
        try:
            result = subprocess.run(
                [str(binary)],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": str(fake_home)},
            )
            assert result.returncode != 0
            assert "writable" in result.stderr.lower()
        finally:
            mock_borgadm.chmod(0o755)


class TestExceptionHierarchy(ExceptionHierarchyBase):
    """Test BorgadmError exception hierarchy."""

    BASE_ERROR = ba.BorgadmError
    EXIT_CODE = ba.ExitCode
    EXCLUDED_CODES = {
        ba.ExitCode.SUCCESS,
        ba.ExitCode.WARNING,
        ba.ExitCode.USAGE,
    }


class TestCodeQuality(CodeQualityBase):
    """Test code quality with black, flake8, and mypy."""

    SCRIPT_PATH = _script_path
    TEST_PATH = REPO_ROOT / "tests" / "test_borgadm.py"


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
