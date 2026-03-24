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
from typing import Any
from unittest.mock import patch

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

    def test_self_test_subcommand_parses(self) -> None:
        """Test self-test subcommand parses flags."""
        parser = ba.args_parser()
        args = parser.parse_args(["self-test", "-v", "--coverage"])
        assert args.command == "self-test"
        assert args.verbose is True
        assert args.coverage is True


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
            ba.do_repair(action="delete-cache")
            mock_run_cmd.assert_called_once_with(
                [
                    "borg",
                    "delete",
                    "--cache-only",
                    mock_cfg.BORG_REPO,
                ]
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

        ba.do_automate(action="enable")

        created = self._created_task_names(mock_create_file)
        assert created == self.CURRENT_TASKS

    def test_enable_skips_legacy_tasks(self, automate_env: Any) -> None:
        """Test that enable does not create plists for legacy tasks."""
        _task_dir, _mock_run, mock_create_file = automate_env

        ba.do_automate(action="enable")

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

        ba.do_automate(action="disable")

        remaining = list(task_dir.glob("*.plist"))
        assert remaining == [], f"Plists not removed: {remaining}"

    def test_disable_removes_legacy_plists(self, automate_env: Any) -> None:
        """Test that disable removes legacy plist files."""
        task_dir, _mock_run, _ = automate_env
        for name in ["check_age", "check_all"]:
            (task_dir / f"local.borgadm.{name}.plist").write_text("<plist/>")

        ba.do_automate(action="disable")

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
            ba.do_automate(action="status")

        assert any("Automation is enabled" in r.message for r in caplog.records)

    def test_status_reports_disabled(
        self, automate_env: Any, caplog: Any
    ) -> None:
        """Test that status reports all-disabled correctly."""
        _task_dir, _mock_run, _ = automate_env

        with caplog.at_level(logging.INFO):
            ba.do_automate(action="status")

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
            ba.do_automate(action="status")

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
            ba.do_automate(action="status")

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
            ba.do_automate(action="status")

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

        ba.do_automate(action="enable")

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
            ba.do_automate(action="enable")
        assert exc_info.value.code == ba.ExitCode.ERROR

    def test_automate_exits_without_plutil(self, mock_cfg: Any) -> None:
        """Test that automate exits when plutil is not found."""
        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", side_effect=lambda x: None),
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.do_automate(action="enable")
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
            ba.do_automate(action="enable")
        assert exc_info.value.code == ba.ExitCode.ERROR


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
