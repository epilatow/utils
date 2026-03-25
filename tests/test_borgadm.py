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
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch
from xml.etree import ElementTree

import pytest  # type: ignore[import-not-found]
from conftest import CodeQualityBase

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
    """Redirect HOME to an empty temp directory.

    Prevents tests from accidentally accessing real user files
    (e.g., ~/.borgadm, ~/.borg_passphrase, ~/.ssh/id_borg.net).
    Any test that needs these files must create them explicitly.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    old_home = os.environ.get("HOME")
    basename: str = getattr(ba, "BASENAME")
    old_config = getattr(ba, "CONFIG")
    old_logfile = getattr(ba, "LOGFILE")

    os.environ["HOME"] = str(fake_home)
    setattr(ba, "CONFIG", Path(fake_home / f".{basename}"))
    setattr(ba, "LOGFILE", tmp_path / f"{basename}.log")

    try:
        yield fake_home
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            del os.environ["HOME"]
        setattr(ba, "CONFIG", old_config)
        setattr(ba, "LOGFILE", old_logfile)


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
        """Verify all subcommands and arguments have help text."""
        parser = ba.args_parser()

        def check_parser(p: argparse.ArgumentParser, path: str) -> None:
            for action in p._actions:
                if isinstance(action, argparse._HelpAction):
                    continue
                if isinstance(action, argparse._SubParsersAction):
                    assert action.choices, f"Empty subparsers in '{path}'"
                    for name, subparser in action.choices.items():
                        check_parser(subparser, f"{path} {name}")
                    continue
                assert action.help and action.help.strip(), (
                    f"Missing help for argument(s) "
                    f"{action.option_strings or action.dest} "
                    f"in '{path}'"
                )

        check_parser(parser, "borgadm")

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

    def test_self_test_subcommand_parses(self) -> None:
        """Test self-test subcommand parses flags."""
        parser = ba.args_parser()
        args = parser.parse_args(["self-test", "-v", "--coverage"])
        assert args.command == "self-test"
        assert args.verbose is True
        assert args.coverage is True

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
        """Test that repair repo without --yes exits with error."""
        with (
            patch.object(ba, "run_cmd", autospec=True) as mock_run_cmd,
            patch.object(
                ba,
                "borg_cmd",
                autospec=True,
                return_value=["borg"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_repair_repo()
        assert exc_info.value.code == ba.ExitCode.ERROR
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
            ba.do_repair_repo(yes=True)
            mock_run_cmd.assert_called_once_with(
                [
                    "borg",
                    "check",
                    "--repair",
                    "--repository-only",
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
                    "--repository-only",
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
        """Test --latest with no backups exits with error."""
        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                return_value={},
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_delete(
                archive=None,
                dry_run=False,
                latest=True,
                progress=False,
            )
        assert exc_info.value.code == ba.ExitCode.ERROR

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
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_delete(
                archive="20250101_120000",
                dry_run=False,
                latest=False,
                progress=False,
            )
        assert exc_info.value.code == ba.ExitCode.ERROR

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
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_delete(
                archive="home-set1-20250101_120000",
                dry_run=False,
                latest=False,
                progress=False,
            )
        assert exc_info.value.code == ba.ExitCode.ERROR

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


class TestAutomate:
    """Test automate subcommand."""

    @pytest.fixture
    def automate_env(self, tmp_path: Path, mock_cfg: Any) -> Any:
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

    def test_status_reports_enabled(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that status reports all-enabled correctly."""
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

        assert any("Automation is enabled" in r.message for r in caplog.records)

    def test_status_reports_disabled(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that status reports all-disabled correctly."""
        _task_dir, _mock_run, _ = automate_env

        with caplog.at_level(logging.INFO):
            ba.do_automate_status()

        assert any(
            "Automation is disabled" in r.message for r in caplog.records
        )

    def test_status_reports_partially_enabled(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that status reports partial enablement."""
        _task_dir, mock_run, _ = automate_env
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="123\t0\tlocal.borgadm.create\n",
            stderr="",
        )

        with caplog.at_level(logging.INFO):
            ba.do_automate_status()

        assert any("partially enabled" in r.message for r in caplog.records)

    def test_status_warns_on_legacy_tasks(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that status warns about loaded legacy tasks."""
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

        with caplog.at_level(logging.WARNING):
            ba.do_automate_status()

        legacy_warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "legacy" in r.message.lower()
        ]
        # One warning per legacy task + one summary
        assert len(legacy_warnings) >= 3
        # Summary should tell user to run enable (not disable+enable)
        summary = [r for r in legacy_warnings if "automate enable" in r.message]
        assert len(summary) == 1
        assert "disable" not in summary[0].message

    def test_status_warns_on_stale_legacy_plist(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that status warns about stale legacy plist files."""
        task_dir, _mock_run, _ = automate_env
        stale = task_dir / "local.borgadm.check_age.plist"
        stale.write_text("<plist/>")

        with caplog.at_level(logging.WARNING):
            ba.do_automate_status()

        stale_warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "stale" in r.message
        ]
        assert len(stale_warnings) >= 1

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
        """Test that automation uses wrapper app when it exists."""
        result = ba.automation_script_path()
        # The wrapper app exists in this repo
        assert result.endswith(
            "Applications/BorgAdm.app/Contents/MacOS/BorgAdm"
        )

    def test_script_path_fallback(self) -> None:
        """Test fallback to direct script path without wrapper."""
        with patch("pathlib.Path.exists", return_value=False):
            result = ba.automation_script_path()
        assert ba.__file__ is not None
        assert result == os.path.abspath(ba.__file__)

    def test_automate_exits_on_non_darwin(self, mock_cfg: Any) -> None:
        """Test that automate exits with error on non-Darwin."""
        with (
            patch.object(ba.platform, "system", return_value="Linux"),
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_automate_enable()
        assert exc_info.value.code == ba.ExitCode.ERROR

    def test_automate_exits_without_plutil(self, mock_cfg: Any) -> None:
        """Test that automate exits when plutil is not found."""
        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", side_effect=lambda x: None),
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_automate_enable()
        assert exc_info.value.code == ba.ExitCode.ERROR

    def test_automate_exits_without_launchctl(self, mock_cfg: Any) -> None:
        """Test that automate exits when launchctl is not found."""

        def which_side_effect(cmd: str) -> str | None:
            return "/usr/bin/plutil" if cmd == "plutil" else None

        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", side_effect=which_side_effect),
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_automate_enable()
        assert exc_info.value.code == ba.ExitCode.ERROR

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
        """Test message when no plist files exist."""
        _task_dir, _mock_run, _ = automate_env

        with caplog.at_level(logging.INFO):
            ba.do_automate_log_files()

        assert any(
            "No automation log files found" in r.message for r in caplog.records
        )

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

    def test_dispatcher_handles_all_subcommands(self, mock_cfg: Any) -> None:
        """Verify do_check can dispatch every check subcommand."""
        actions = self._check_subcommands()
        assert actions, "No check subcommands found in parser"

        # Mock all do_check_* functions so dispatch doesn't run
        # real checks. Discovered dynamically so new check functions
        # are picked up automatically.
        check_fn_names = [
            name
            for name in dir(ba)
            if name.startswith("do_check_") and callable(getattr(ba, name))
        ]
        patches = [patch.object(ba, name) for name in check_fn_names]
        for p in patches:
            p.start()
        try:
            for action in actions:
                ba.do_check(action=action)
        finally:
            for p in patches:
                p.stop()

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
        """Test check age exits with EXIT_CHECK_NO_BACKUPS."""
        with (
            patch.object(ba, "list_backups", autospec=True, return_value={}),
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_check_age()
        assert exc_info.value.code == ba.ExitCode.CHECK_NO_BACKUPS

    def test_check_age_too_old(self, mock_cfg: Any) -> None:
        """Test check age exits with EXIT_CHECK_AGE."""
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
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_check_age()
        assert exc_info.value.code == ba.ExitCode.CHECK_AGE

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
        """Test check archives exits with EXIT_CHECK_NO_BACKUPS."""
        with (
            patch.object(ba, "list_backups", autospec=True, return_value={}),
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_check_archives(progress=False)
        assert exc_info.value.code == ba.ExitCode.CHECK_NO_BACKUPS

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
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_check_prune()
        assert exc_info.value.code == ba.ExitCode.CHECK_PRUNE

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
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_check_prune()
        assert exc_info.value.code == ba.ExitCode.CHECK_PRUNE

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
        """Verify osascript_notify is called when exiting via WARNING."""
        original_warning = getattr(ba, "_warning_occurred")
        original_notifications = getattr(ba, "_enable_notifications")
        try:
            setattr(ba, "_warning_occurred", True)
            setattr(ba, "_enable_notifications", True)
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
                    "initialize_logger",
                    autospec=True,
                ),
                patch.object(
                    ba,
                    "initialize_borg_environment",
                    autospec=True,
                ),
                patch.object(ba, "do_check", autospec=True),
                pytest.raises(SystemExit) as exc_info,
            ):
                ba.main()
            assert exc_info.value.code == ba.ExitCode.WARNING
            mock_notify.assert_called_once()
        finally:
            setattr(ba, "_warning_occurred", original_warning)
            setattr(ba, "_enable_notifications", original_notifications)


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
            patch.object(ba, "initialize_logger", autospec=True),
            patch.object(ba, "initialize_borg_environment", autospec=True),
            patch.object(ba, "do_break_lock", autospec=True),
            caplog.at_level(logging.INFO),
        ):
            ba.main()

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
            patch.object(ba, "initialize_logger", autospec=True),
            patch.object(ba, "initialize_borg_environment", autospec=True),
            patch.object(ba, "do_environment", autospec=True),
            caplog.at_level(logging.INFO),
        ):
            ba.main()

        assert not any("started" in r.message for r in caplog.records)
        assert not any("finished" in r.message for r in caplog.records)

    def test_timed_command_includes_action(
        self, mock_cfg: Any, caplog: Any
    ) -> None:
        """Test that action name is included in timing message."""
        with (
            patch("sys.argv", ["borgadm", "check", "age"]),
            patch.object(ba, "initialize_logger", autospec=True),
            patch.object(ba, "initialize_borg_environment", autospec=True),
            patch.object(ba, "do_check", autospec=True),
            caplog.at_level(logging.INFO),
        ):
            ba.main()

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
            patch.object(ba, "initialize_logger", autospec=True),
            patch.object(ba, "initialize_borg_environment", autospec=True),
            patch.object(
                ba,
                "do_compact",
                autospec=True,
                side_effect=subprocess.CalledProcessError(
                    1, ["borg", "compact"]
                ),
            ),
            patch.object(ba, "osascript_notify", autospec=True),
            caplog.at_level(logging.INFO),
            pytest.raises(SystemExit),
        ):
            ba.main()

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

        with pytest.raises(SystemExit) as exc_info:
            ba.Config(config_file.name, {})
        assert exc_info.value.code == ba.ExitCode.CONFIG


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


class TestCodeQuality(CodeQualityBase):
    """Test code quality with black, flake8, and mypy."""

    SCRIPT_PATH = _script_path
    TEST_PATH = REPO_ROOT / "tests" / "test_borgadm.py"


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
