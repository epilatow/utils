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
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
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
    BORG_REPO = foobar
    BACKUP_SETS = { "set1": {"paths": ["foo"]} }
    """)
    config_file.flush()

    cfg = ba.Config(config_file.name, {"command": "test"})
    original_cfg = getattr(ba, "CFG")
    setattr(ba, "CFG", cfg)
    yield cfg
    setattr(ba, "CFG", original_cfg)


# -----------------------------------------------------------------------------
# E2E fixture: real borg repo + real subprocess invocations of borgadm.
# -----------------------------------------------------------------------------

# Backup-set layout used by borg_e2e. Two sets, each rooted at a single
# source directory under BACKUP_ROOT. Trailing slash marks the path as a
# directory (vs. a file) for borgadm's dir-vs-file classification in
# backup_set_paths().
#
# Iteration order is load-bearing: borgadm's list_backups() emits archives
# in cfg.BACKUP_SETS order within a timestamp, and several E2E tests below
# assert on that exact ordering. Keep insertion order stable when editing.
_E2E_SETS: dict[str, list[str]] = {
    "set-a": ["set-a/"],
    "set-b": ["set-b/"],
}


@dataclass
class BorgE2EFixture:
    """Context object yielded by the borg_e2e fixture."""

    repo_path: Path
    backup_root: Path
    home: Path
    config_path: Path
    sets: dict[str, list[Path]] = field(default_factory=dict)

    @property
    def borgadm_bin(self) -> Path:
        return REPO_ROOT / "bin" / "borgadm"

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        # Unencrypted repos prompt interactively on first access. The
        # opt-in env var silences the prompt for our local test repo.
        env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
        env.setdefault("BORG_PASSPHRASE", "")
        return env

    def run(
        self,
        *args: str,
        check: bool = True,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        """Invoke borgadm as a subprocess with HOME pointing at the fake
        home so the subprocess picks up our test config."""
        return subprocess.run(
            [str(self.borgadm_bin), *args],
            env=self._subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )

    def borg(
        self,
        *args: str,
        check: bool = True,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        """Invoke borg directly. Used by tests to set up state without
        going through borgadm, or to assert on raw repo state."""
        return subprocess.run(
            ["borg", *args],
            env=self._subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )

    def archives(self) -> list[str]:
        """Return the list of archive short-names currently in the repo."""
        result = self.borg("list", "--short", str(self.repo_path))
        return [line for line in result.stdout.splitlines() if line]

    def make_archive(self, name: str, content_path: Path | None = None) -> None:
        """Create a borg archive at `name`. Defaults to archiving the
        whole backup_root, since archive *contents* don't matter for
        name-based classification tests -- callers care about the
        archive name being present in the repo, not what's inside."""
        path = content_path if content_path is not None else self.backup_root
        self.borg("create", f"{self.repo_path}::{name}", str(path))


def _archive_name(
    set_name: str,
    ts: str,
    n: int | None = None,
    m: int | None = None,
    backup_name: str = "test",
) -> str:
    """Build an archive name string for the test fixture's BACKUP_NAME.

    Delegates to the real ba._ArchiveName so the tests exercise the
    production renderer rather than a parallel copy of the format.
    With both n and m provided the NofM suffix is applied; without
    them a legacy (pre-NofM) name is produced.
    """
    return str(
        ba._ArchiveName(
            backup_name=backup_name,
            set_name=set_name,
            timestamp=ts,
            n=n,
            m=m,
        )
    )


def _have_borg() -> bool:
    return shutil.which("borg") is not None


# Skip the E2E suite when borg or ssh-keygen aren't installed. Both are
# required for the fixture itself, not just individual tests.
e2e_requires_borg = pytest.mark.skipif(
    not _have_borg() or shutil.which("ssh-keygen") is None,
    reason="borg and ssh-keygen must be installed for E2E tests",
)


@pytest.fixture
def borg_e2e(_isolate_home: Path, tmp_path: Path) -> Iterator[BorgE2EFixture]:
    """Spin up a real local borg repo with a minimal borgadm config so
    tests can drive `borgadm` via subprocess against actual archives.

    Layout:
      <short-home>/                # HOME for the subprocess; created
                                   # under /tmp because macOS
                                   # /usr/bin/ssh-agent puts its unix
                                   # socket at $HOME/.ssh/agent/... and
                                   # pytest's tmp_path under
                                   # /private/var/folders/... brushes
                                   # the sockaddr_un.sun_path limit
                                   # (104 bytes).
        .borgadm                   # config pointing at the local repo
        .borg_passphrase           # dummy, repo uses --encryption=none
        .ssh/id_borg.net           # passphrase-less ed25519 key
      <tmp_path>/repo/             # the borg repo (encryption=none)
      <tmp_path>/src/<set>/...     # source dirs, one per backup set
    """
    home = Path(tempfile.mkdtemp(prefix="be2e_home_", dir="/tmp"))
    try:
        repo_path = tmp_path / "repo"
        backup_root = tmp_path / "src"
        backup_root.mkdir()

        sets: dict[str, list[Path]] = {}
        for set_name, paths in _E2E_SETS.items():
            absolute_paths: list[Path] = []
            for rel in paths:
                full = backup_root / rel.rstrip("/")
                full.mkdir(parents=True)
                (full / f"{set_name}-file.txt").write_text(
                    f"{set_name} content\n"
                )
                absolute_paths.append(full)
            sets[set_name] = absolute_paths

        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        sshkey_file = ssh_dir / "id_borg.net"
        subprocess.run(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(sshkey_file),
                "-q",
                "-C",
                "borgadm-e2e-test",
            ],
            check=True,
            capture_output=True,
        )
        sshkey_file.chmod(0o600)

        passphrase_file = home / ".borg_passphrase"
        passphrase_file.write_text("e2e-test-passphrase\n")
        passphrase_file.chmod(0o600)

        subprocess.run(
            ["borg", "init", "--encryption=none", str(repo_path)],
            check=True,
            capture_output=True,
            text=True,
        )

        backup_sets_cfg = {
            name: {"paths": _E2E_SETS[name]} for name in _E2E_SETS
        }
        config_path = home / ".borgadm"
        config_path.write_text(
            f"BORG_REPO = {repo_path}\n"
            f"BACKUP_NAME = test\n"
            f"BACKUP_ROOT = {backup_root}\n"
            f"BORG_PASSPHRASE_FILE = {passphrase_file}\n"
            f"BORG_SSHKEY_FILE = {sshkey_file}\n"
            f"BACKUP_SETS = {json.dumps(backup_sets_cfg)}\n"
        )

        yield BorgE2EFixture(
            repo_path=repo_path,
            backup_root=backup_root,
            home=home,
            config_path=config_path,
            sets=sets,
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)


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


class TestLogFiles:
    """Test top-level log-files subcommand."""

    CURRENT_TASKS = {"create", "check-daily", "check-weekly"}

    @staticmethod
    def _launchctl_output(loaded_tasks: set[str]) -> str:
        """Build a fake `launchctl list` stdout marking given tasks loaded."""
        lines = ["PID\tStatus\tLabel"]
        for task in loaded_tasks:
            lines.append(f"123\t0\tlocal.borgadm.{task}")
        return "\n".join(lines) + "\n"

    @pytest.fixture
    def darwin_env(self, tmp_path: Path) -> Iterator[tuple[Path, Any]]:
        """Set up a Darwin environment with all current tasks loaded."""
        task_dir = tmp_path / "Library" / "LaunchAgents"
        task_dir.mkdir(parents=True)

        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", return_value="/usr/bin/tool"),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.dict(os.environ, {"HOME": str(tmp_path)}),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=self._launchctl_output(self.CURRENT_TASKS),
                stderr="",
            )
            yield task_dir, mock_run

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

    def test_shows_default_logfile(self, darwin_env: Any, caplog: Any) -> None:
        """log-files always includes the default log file."""
        _task_dir, _mock_run = darwin_env

        with caplog.at_level(logging.INFO):
            ba.do_log_files()

        messages = [r.message for r in caplog.records]
        assert str(ba.LOGFILE) in messages

    def test_default_logfile_listed_first(
        self, darwin_env: Any, caplog: Any
    ) -> None:
        """The default log file is listed first."""
        task_dir, _mock_run = darwin_env
        plist_path = task_dir / "local.borgadm.create.plist"
        self._write_plist(plist_path, "/tmp/logs/create.log")

        with caplog.at_level(logging.INFO):
            ba.do_log_files()

        messages = [r.message for r in caplog.records]
        assert messages[0] == str(ba.LOGFILE)

    def test_default_logfile_not_duplicated(
        self, darwin_env: Any, caplog: Any
    ) -> None:
        """Default log file isn't duplicated if a plist uses it."""
        task_dir, _mock_run = darwin_env
        plist_path = task_dir / "local.borgadm.create.plist"
        self._write_plist(plist_path, str(ba.LOGFILE))

        with caplog.at_level(logging.INFO):
            ba.do_log_files()

        matches = [r for r in caplog.records if r.message == str(ba.LOGFILE)]
        assert len(matches) == 1

    def test_shows_paths_from_enabled_plists(
        self, darwin_env: Any, caplog: Any
    ) -> None:
        """log-files shows paths from plists for enabled tasks."""
        task_dir, _mock_run = darwin_env
        for name in self.CURRENT_TASKS:
            plist_path = task_dir / f"local.borgadm.{name}.plist"
            log = f"/tmp/logs/{name}.log"
            self._write_plist(plist_path, log)

        with caplog.at_level(logging.INFO):
            ba.do_log_files()

        for name in self.CURRENT_TASKS:
            assert any(
                f"/tmp/logs/{name}.log" in r.message for r in caplog.records
            )

    def test_skips_unloaded_tasks(self, darwin_env: Any, caplog: Any) -> None:
        """Plists for tasks not loaded in launchctl are skipped."""
        task_dir, mock_run = darwin_env
        # Only "create" is loaded; "check-daily" has a plist but isn't.
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=self._launchctl_output({"create"}),
            stderr="",
        )
        self._write_plist(
            task_dir / "local.borgadm.create.plist", "/tmp/logs/create.log"
        )
        self._write_plist(
            task_dir / "local.borgadm.check-daily.plist",
            "/tmp/logs/check-daily.log",
        )

        with caplog.at_level(logging.INFO):
            ba.do_log_files()

        messages = [r.message for r in caplog.records]
        assert any("/tmp/logs/create.log" in m for m in messages)
        assert not any("/tmp/logs/check-daily.log" in m for m in messages)

    def test_no_paths_when_nothing_enabled(
        self, darwin_env: Any, caplog: Any
    ) -> None:
        """Only default log file is shown when no tasks are loaded."""
        task_dir, mock_run = darwin_env
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=self._launchctl_output(set()),
            stderr="",
        )
        # Plist exists, but task isn't loaded -- should be ignored.
        self._write_plist(
            task_dir / "local.borgadm.create.plist", "/tmp/logs/create.log"
        )

        with caplog.at_level(logging.INFO):
            ba.do_log_files()

        messages = [r.message for r in caplog.records]
        assert messages == [str(ba.LOGFILE)]

    def test_deduplicates_stdout_stderr(
        self, darwin_env: Any, caplog: Any
    ) -> None:
        """Identical stdout/stderr paths appear only once."""
        task_dir, _mock_run = darwin_env
        plist_path = task_dir / "local.borgadm.create.plist"
        self._write_plist(plist_path, "/tmp/logs/create.log")

        with caplog.at_level(logging.INFO):
            ba.do_log_files()

        matches = [
            r for r in caplog.records if "/tmp/logs/create.log" in r.message
        ]
        assert len(matches) == 1

    def test_shows_distinct_stderr(self, darwin_env: Any, caplog: Any) -> None:
        """Distinct stderr path is also shown."""
        task_dir, _mock_run = darwin_env
        plist_path = task_dir / "local.borgadm.create.plist"
        self._write_plist(plist_path, "/tmp/logs/out.log", "/tmp/logs/err.log")

        with caplog.at_level(logging.INFO):
            ba.do_log_files()

        messages = [r.message for r in caplog.records]
        assert any("/tmp/logs/out.log" in m for m in messages)
        assert any("/tmp/logs/err.log" in m for m in messages)

    def test_no_plists(self, darwin_env: Any, caplog: Any) -> None:
        """Default log file is shown even with no plists."""
        _task_dir, _mock_run = darwin_env

        with caplog.at_level(logging.INFO):
            ba.do_log_files()

        messages = [r.message for r in caplog.records]
        assert messages == [str(ba.LOGFILE)]

    def test_reads_legacy_plists_when_loaded(
        self, darwin_env: Any, caplog: Any
    ) -> None:
        """Legacy plists are read when their task is still loaded."""
        task_dir, mock_run = darwin_env
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=self._launchctl_output({"check_age"}),
            stderr="",
        )
        self._write_plist(
            task_dir / "local.borgadm.check_age.plist",
            "/tmp/logs/check_age.log",
        )

        with caplog.at_level(logging.INFO):
            ba.do_log_files()

        assert any(
            "/tmp/logs/check_age.log" in r.message for r in caplog.records
        )

    def test_non_darwin_shows_only_default(
        self, tmp_path: Path, caplog: Any
    ) -> None:
        """On non-Darwin platforms, only the default log file is shown."""
        with (
            patch.object(ba.platform, "system", return_value="Linux"),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            caplog.at_level(logging.INFO),
        ):
            ba.do_log_files()

        messages = [r.message for r in caplog.records]
        assert messages == [str(ba.LOGFILE)]
        # launchctl must not be invoked off Darwin.
        mock_run.assert_not_called()

    def test_no_launchctl_shows_only_default(
        self, tmp_path: Path, caplog: Any
    ) -> None:
        """If launchctl isn't on PATH, only the default log file is shown."""
        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba.shutil, "which", return_value=None),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            caplog.at_level(logging.INFO),
        ):
            ba.do_log_files()

        messages = [r.message for r in caplog.records]
        assert messages == [str(ba.LOGFILE)]
        mock_run.assert_not_called()


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


class TestMain:
    """Test main() error reporting."""

    def test_main_logs_setup_phase_error(self, caplog: Any) -> None:
        """BorgadmError raised before the command callback is logged."""
        with (
            patch("sys.argv", ["borgadm", "environment"]),
            patch.object(
                ba,
                "Config",
                autospec=True,
                side_effect=ba.ConfigError("setup-boom"),
            ),
            patch.object(ba, "initialize_logger", autospec=True),
            caplog.at_level(logging.ERROR),
        ):
            with pytest.raises(ba.ConfigError):
                ba.main(
                    command="environment",
                    config=str(ba.CONFIG),
                    verbose=False,
                    timestamp_messages=False,
                    enable_notifications=False,
                    args_dict={},
                )
        assert "setup-boom" in caplog.text


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

        # Two hourly timestamps -- only 1 hourly kept, nothing
        # from other intervals since they're all 0
        ts_all = {"20250101_010000", "20250101_020000"}
        ts_keep = ba.ts_to_keep(ts_all)
        assert len(ts_keep) == 1
        assert "20250101_020000" in ts_keep

    def test_all_zero_keep_is_config_error(self) -> None:
        """Test that all keep=0 is rejected as a config error."""
        config_file = tempfile.NamedTemporaryFile(mode="w+", delete=False)
        config_file.write(
            "BORG_REPO = foobar\n"
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

    # Helper that imports borgadm and configures both stdout and
    # stderr handlers using BrokenPipeAwareStreamHandler, mirroring
    # initialize_logger() but without needing a config file.
    _SETUP = """
import importlib.machinery
import importlib.util
import logging
import sys

script_path = {script!r}
loader = importlib.machinery.SourceFileLoader("borgadm", script_path)
spec = importlib.util.spec_from_loader("borgadm", loader)
borgadm = importlib.util.module_from_spec(spec)
# Register before exec_module: @dataclass resolves field annotations
# against sys.modules[cls.__module__], so the module must be findable
# there by the time the class body runs.
sys.modules["borgadm"] = borgadm
spec.loader.exec_module(borgadm)

logger = logging.getLogger("test_pipe")
logger.setLevel(logging.DEBUG)

stdout_handler = borgadm.BrokenPipeAwareStreamHandler(sys.stdout)
stdout_handler.setLevel(logging.INFO)
stdout_handler.addFilter(borgadm.InfoFilter())
stdout_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(stdout_handler)

stderr_handler = borgadm.BrokenPipeAwareStreamHandler(sys.stderr)
stderr_handler.setLevel(logging.WARNING)
stderr_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
logger.addHandler(stderr_handler)
"""

    def _run(self, tmp_path: Path, body: str, shell_cmd: str) -> Any:
        script_file = tmp_path / "test_pipe.py"
        setup = self._SETUP.format(script=str(REPO_ROOT / "bin" / "borgadm"))
        script_file.write_text(setup + body)
        return subprocess.run(
            shell_cmd.format(script=script_file),
            shell=True,
            capture_output=True,
            text=True,
        )

    def test_stdout_closed_by_head(self, tmp_path: Path) -> None:
        """Stdout truncated by `head -1`: no traceback, exits cleanly,
        post-truncation log is silently dropped (handler kept process
        alive instead of crashing)."""
        body = """
for i in range(1000):
    logger.info(f"line {i}")
logger.info("after pipe closed")
"""
        result = self._run(tmp_path, body, "python3 {script} | head -1")
        # The python script's exit code is in PIPESTATUS[0], not $?,
        # but capture_output gives us what python wrote to stderr,
        # which is what we care about.
        assert "BrokenPipeError" not in result.stderr, result.stderr
        assert "Traceback" not in result.stderr, result.stderr
        assert "Logging error" not in result.stderr, result.stderr
        assert "line 0" in result.stdout

    def test_stderr_closed(self, tmp_path: Path) -> None:
        """Stderr broken (piped to `head -1` via fd redirection):
        WARNING-level logs that don't fit get dropped silently and
        stdout INFO output is unaffected."""
        body = """
for i in range(500):
    logger.warning(f"warn {i}")
for i in range(5):
    logger.info(f"info {i}")
"""
        # Redirect stderr through head -1, capture stdout normally.
        # 2> >(head -1 >&2) means: replace stderr with a pipe to
        # `head -1` which writes its (single) line back to the real
        # stderr.
        result = self._run(
            tmp_path,
            body,
            "bash -c 'python3 {script} 2> >(head -1 >&2)'",
        )
        assert "BrokenPipeError" not in result.stderr, result.stderr
        assert "Traceback" not in result.stderr, result.stderr
        assert "Logging error" not in result.stderr, result.stderr
        # All 5 INFO lines made it to stdout despite the stderr break.
        for i in range(5):
            assert f"info {i}" in result.stdout

    def test_both_streams_closed(self, tmp_path: Path) -> None:
        """Both streams merged and truncated: no tracebacks anywhere,
        process exits cleanly."""
        body = """
for i in range(500):
    logger.info(f"info {i}")
    logger.warning(f"warn {i}")
logger.info("end-sentinel")
"""
        result = self._run(tmp_path, body, "python3 {script} 2>&1 | head -1")
        # Both captured streams must be free of error spam. The merged
        # content goes to stdout via the pipe; stderr was redirected
        # into the same pipe so nothing should land in stderr.
        combined = result.stdout + result.stderr
        assert "BrokenPipeError" not in combined, combined
        assert "Traceback" not in combined, combined
        assert "Logging error" not in combined, combined

    def test_safe_stream_handler_swaps_stream_on_broken_pipe(
        self,
    ) -> None:
        """Unit test: BrokenPipeAwareStreamHandler swaps to /dev/null
        on the first BrokenPipeError, and subsequent emits are silent
        no-ops."""

        class BrokenStream:
            def __init__(self) -> None:
                self.writes = 0

            def write(self, _data: str) -> int:
                self.writes += 1
                raise BrokenPipeError(32, "Broken pipe")

            def flush(self) -> None:
                pass

            def close(self) -> None:
                pass

        broken = BrokenStream()
        handler = ba.BrokenPipeAwareStreamHandler(broken)
        # Capture anything written to the handleError fallback path.
        with patch("sys.stderr", new_callable=io.StringIO) as fake_err:
            record = logging.LogRecord(
                name="t",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="first",
                args=None,
                exc_info=None,
            )
            handler.emit(record)
            assert handler.stream is not broken
            assert "Broken" not in fake_err.getvalue()

            # The replacement stream must be writable so subsequent
            # emits succeed silently.
            for i in range(10):
                record.msg = f"after-{i}"
                handler.emit(record)
            assert "Traceback" not in fake_err.getvalue()
            assert "Logging error" not in fake_err.getvalue()

    def test_pump_stream_handles_broken_sink(self) -> None:
        """Unit test: pump_stream stops writing to a broken sink but
        keeps draining the source so the subprocess doesn't block."""

        class BrokenSink:
            def __init__(self, fail_after: int) -> None:
                self.fail_after = fail_after
                self.writes = 0

            def write(self, _data: str) -> int:
                self.writes += 1
                if self.writes > self.fail_after:
                    raise BrokenPipeError(32, "Broken pipe")
                return len(_data)

            def flush(self) -> None:
                pass

        src = io.StringIO("".join(f"line {i}\n" for i in range(100)))
        acc = io.StringIO()
        sink = BrokenSink(fail_after=3)
        ba.pump_stream(src, sink, acc, allow_output=True)

        # Source fully drained into the accumulator.
        assert acc.getvalue().count("\n") == 100
        # Sink stopped being called after the first failure (4th
        # write raised; nothing was attempted after that).
        assert sink.writes == 4

    def test_pump_stream_no_passthrough_when_disabled(self) -> None:
        """Sanity check: with allow_output=False, sink is never
        touched even if it would raise."""

        class ExplodingSink:
            def write(self, _data: str) -> int:
                raise AssertionError("sink must not be written")

            def flush(self) -> None:
                raise AssertionError("sink must not be flushed")

        src = io.StringIO("a\nb\nc\n")
        acc = io.StringIO()
        ba.pump_stream(src, ExplodingSink(), acc, allow_output=False)
        assert acc.getvalue() == "a\nb\nc\n"


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
        # src not created -> doesn't exist
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

        # Fake HOME (no TCC dir -> FDA check sees ENOENT -> proceeds)
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
        # No args -> empty line
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


@e2e_requires_borg
class TestE2EFixture:
    """Smoke tests pinning the borg_e2e fixture itself.

    These do not exercise borgadm subcommand semantics in depth -- that
    is the job of the per-subcommand E2E test classes added in subsequent
    commits. The goal here is to detect fixture-setup regressions early.
    """

    def test_fixture_lays_out_repo_and_config(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """The fixture creates a usable repo, config, auth files, and
        source dirs."""
        assert borg_e2e.repo_path.is_dir()
        assert (borg_e2e.repo_path / "README").is_file()
        assert borg_e2e.config_path.is_file()
        assert (borg_e2e.home / ".borg_passphrase").is_file()
        assert (borg_e2e.home / ".ssh" / "id_borg.net").is_file()
        # Each declared set has at least one populated source path.
        for set_name, paths in borg_e2e.sets.items():
            assert paths, f"set {set_name!r} has no source paths"
            for path in paths:
                assert path.is_dir(), f"missing source dir: {path}"
                assert any(path.iterdir()), f"empty source dir: {path}"

    def test_borg_list_returns_empty_on_fresh_repo(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """A freshly-initialized repo has no archives."""
        assert borg_e2e.archives() == []

    def test_borgadm_list_runs_against_empty_repo(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm list` succeeds on an empty repo (smoke test that the
        subprocess invocation path -- config parsing, ssh-agent startup,
        env init -- all work end-to-end)."""
        result = borg_e2e.run("list")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


class TestArchiveCompleteness:
    """Unit-level coverage of _archive_entries_are_complete and the
    _ArchiveName struct. The E2E classes above exercise the
    integration path; these tests cover edge cases that are hard to
    reach from real archives (M=0, duplicate Ns, mixed suffixed +
    legacy at one timestamp, and the parse / round-trip path)."""

    _TS = "20260101_120000"

    def _entry(
        self,
        set_name: str,
        n: int | None,
        m: int | None,
        timestamp: str | None = None,
    ) -> Any:
        return ba._ArchiveName(
            backup_name="test",
            set_name=set_name,
            timestamp=timestamp or self._TS,
            n=n,
            m=m,
        )

    def test_legacy_only_is_complete(self) -> None:
        """Any timestamp containing a legacy archive is complete."""
        assert ba._archive_entries_are_complete(
            [self._entry("set-a", None, None)]
        )

    def test_mixed_legacy_and_suffixed_is_complete(self) -> None:
        """A legacy archive anywhere in the entry list short-circuits
        to complete -- the no-suffix archive's membership cannot be
        evaluated, so the safe answer is "do not prune"."""
        assert ba._archive_entries_are_complete(
            [
                self._entry("set-a", None, None),
                self._entry("set-b", 1, 2),
            ]
        )

    def test_full_nofm_coverage_is_complete(self) -> None:
        assert ba._archive_entries_are_complete(
            [self._entry("set-a", 1, 2), self._entry("set-b", 2, 2)]
        )

    def test_missing_n_is_incomplete(self) -> None:
        assert not ba._archive_entries_are_complete(
            [self._entry("set-a", 1, 3), self._entry("set-b", 2, 3)]
        )

    def test_inconsistent_m_is_incomplete(self) -> None:
        assert not ba._archive_entries_are_complete(
            [self._entry("set-a", 1, 2), self._entry("set-b", 1, 3)]
        )

    def test_duplicate_n_is_incomplete(self) -> None:
        """Two archives at the same N produce a set with fewer than M
        elements, so coverage does not reach {1..M} and the timestamp
        is partial. This is the corner case where set_name and N would
        otherwise have been treated as 1:1."""
        assert not ba._archive_entries_are_complete(
            [self._entry("set-a", 1, 2), self._entry("set-b", 1, 2)]
        )

    def test_m_zero_is_incomplete(self) -> None:
        """M=0 would emerge from a corrupted suffix; treat as
        partial rather than vacuously complete."""
        assert not ba._archive_entries_are_complete(
            [self._entry("set-a", 0, 0)]
        )

    def test_mixed_timestamps_violates_caller_contract(self) -> None:
        """The function's contract is "members of one set", which share
        a timestamp. It asserts on a mismatch rather than silently
        returning a misleading result, so a caller bug surfaces
        immediately."""
        e1 = self._entry("set-a", 1, 2, timestamp="20260101_120000")
        e2 = self._entry("set-b", 2, 2, timestamp="20260101_130000")
        with pytest.raises(AssertionError):
            ba._archive_entries_are_complete([e1, e2])

    def test_mixed_backup_names_violates_caller_contract(self) -> None:
        """Members of one set share a backup_name. A mix means the
        caller pulled in archives from a different borgadm config."""
        e1 = ba._ArchiveName("test", "set-a", self._TS, 1, 2)
        e2 = ba._ArchiveName("other", "set-b", self._TS, 2, 2)
        with pytest.raises(AssertionError):
            ba._archive_entries_are_complete([e1, e2])

    def test_duplicate_set_name_violates_caller_contract(self) -> None:
        """A set has one archive per member, so set names are distinct.
        Two entries with the same set_name means the caller grouped
        unrelated archives -- assert rather than guess which is the
        real member."""
        e1 = self._entry("set-a", 1, 2)
        e2 = self._entry("set-a", 2, 2)
        with pytest.raises(AssertionError):
            ba._archive_entries_are_complete([e1, e2])

    def test_str_round_trips_through_from_str(self) -> None:
        """Building an _ArchiveName, rendering it with str(), then
        parsing the result back yields an equal value. This pins the
        single-source-of-truth contract between do_create's emission
        path and list_backups' parse path."""
        name = self._entry("home-fuse", 1, 2)
        assert ba._ArchiveName.from_str("test", str(name)) == name

    def test_str_pads_when_m_two_digits(self) -> None:
        """N is zero-padded to M's width so archive names sort
        correctly when M >= 10."""
        name = self._entry("home-fuse", 3, 11)
        assert str(name) == "test-home-fuse-20260101_120000_03of11"

    def test_str_no_suffix_for_legacy(self) -> None:
        """Legacy names (n or m is None) render without a suffix."""
        name = self._entry("home-fuse", None, None)
        assert str(name) == "test-home-fuse-20260101_120000"

    def test_from_str_returns_none_for_unrecognized_shape(
        self,
    ) -> None:
        """A string that doesn't match the expected shape returns
        None rather than raising. list_backups uses this to silently
        skip non-borgadm archives in the same repo."""
        assert ba._ArchiveName.from_str("test", "manual-archive") is None
        assert ba._ArchiveName.from_str("test", "other-set-a-no-stamp") is None

    def test_from_str_returns_none_for_other_backup_name(self) -> None:
        """An archive whose backup_name prefix doesn't match the
        configured BACKUP_NAME is rejected so two borgadm-managed
        repositories sharing a borg target stay isolated."""
        name = "other-set-a-20260101_120000_1of2"
        assert ba._ArchiveName.from_str("test", name) is None

    def test_from_str_parses_legacy_archive(self) -> None:
        """Suffix-less names parse with n=m=None."""
        name = ba._ArchiveName.from_str("test", "test-set-a-20260101_120000")
        assert name == ba._ArchiveName(
            backup_name="test",
            set_name="set-a",
            timestamp="20260101_120000",
            n=None,
            m=None,
        )

    def test_from_str_parses_set_name_with_dashes(self) -> None:
        """The set-name token may contain dashes; the parser anchors
        on the trailing timestamp and NofM, not on a dash count."""
        name = ba._ArchiveName.from_str(
            "test", "test-home-fuse-mount-20260101_120000_1of2"
        )
        assert name is not None
        assert name.set_name == "home-fuse-mount"
        assert name.n == 1 and name.m == 2

    def test_lt_gives_total_order_across_timestamps(self) -> None:
        """_ArchiveName is fully sortable: timestamp first, then
        NofM-suffixed names ahead of legacy ones (suffixed ascending
        by N), then set_name. sorted() needs no key function."""
        a = self._entry("set-a", 1, 2, timestamp="20260101_120000")
        b = self._entry("set-b", 2, 2, timestamp="20260101_120000")
        c = self._entry("z-legacy", None, None, timestamp="20260101_120000")
        d = self._entry("set-a", 1, 1, timestamp="20260102_120000")
        assert sorted([d, c, b, a]) == [a, b, c, d]


def _list_borgadm(fixture: BorgE2EFixture, *flags: str) -> list[str]:
    """Run `borgadm list` and return non-empty stdout lines.

    `do_list` writes timestamp or archive-name lines via the INFO logger
    which the stdout handler emits without a level prefix, so simply
    stripping blank lines yields the user-visible list.

    `--keep-tags` defaults to True in production and appends labels like
    ` (hour-0)` whose values depend on wall time. Tests want a stable
    output to assert against, so the helper passes `--no-keep-tags`
    unless the caller specifically supplies its own keep-tags flag.
    """
    if not any(f in flags for f in ("--keep-tags", "--no-keep-tags")):
        flags = ("--no-keep-tags", *flags)
    result = fixture.run("list", *flags)
    return [line for line in result.stdout.splitlines() if line.strip()]


@e2e_requires_borg
class TestE2EList:
    """E2E coverage for borgadm's list_backups full/partial classification.

    Pins the user-observable output of `borgadm list` against archives
    manufactured at known timestamps. Future commits rework how set
    completeness is determined; these tests catch silent regressions in
    what the existing classification produces today.
    """

    def test_full_set_at_one_ts_shows_as_single_line(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """A timestamp whose NofM-suffixed archives cover 1..M is a
        full backup and collapses to one timestamp line in the
        default output."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        assert _list_borgadm(borg_e2e) == [ts]

    def test_partial_set_included_by_default(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm list` defaults to --include-partial, so a
        partial timestamp (NofM coverage of 1..M is incomplete)
        still appears in the default output."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        assert _list_borgadm(borg_e2e) == [ts]

    def test_no_include_partial_excludes_partial_sets(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`--no-include-partial` filters out incomplete timestamps."""
        full_ts = "20260102_120000"
        partial_ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", full_ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", full_ts, 2, 2))
        borg_e2e.make_archive(_archive_name("set-a", partial_ts, 1, 2))
        assert _list_borgadm(borg_e2e, "--no-include-partial") == [full_ts]

    def test_only_partial_returns_only_partial(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`--only-partial` filters out full backups."""
        full_ts = "20260102_120000"
        partial_ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", full_ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", full_ts, 2, 2))
        borg_e2e.make_archive(_archive_name("set-a", partial_ts, 1, 2))
        assert _list_borgadm(borg_e2e, "--only-partial") == [partial_ts]

    def test_full_names_emits_archive_names(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`--full-names` switches the output to one line per archive.
        Within a timestamp, suffixed archives sort by ascending N
        (mirroring do_create's emission order)."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        assert _list_borgadm(borg_e2e, "--full-names") == [
            f"{borg_e2e.repo_path}::test-set-a-{ts}_1of2",
            f"{borg_e2e.repo_path}::test-set-b-{ts}_2of2",
        ]

    def test_latest_no_partial_returns_only_newest_full(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`--latest --no-include-partial` ignores later partial archives
        and returns just the newest *full* timestamp."""
        for ts in ("20260101_120000", "20260102_120000"):
            borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
            borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        borg_e2e.make_archive(_archive_name("set-a", "20260103_120000", 1, 2))
        assert _list_borgadm(borg_e2e, "--latest", "--no-include-partial") == [
            "20260102_120000"
        ]

    def test_latest_default_returns_newest_of_each_completeness(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """With `--include-partial` (the default), `--latest` returns the
        newest full *and* the newest partial -- list_backups is called
        twice, once per completeness, and `latest` caps each call
        independently. do_list emits full timestamps first, then partial."""
        for ts in ("20260101_120000", "20260102_120000"):
            borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
            borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        borg_e2e.make_archive(_archive_name("set-a", "20260103_120000", 1, 2))
        assert _list_borgadm(borg_e2e, "--latest") == [
            "20260102_120000",
            "20260103_120000",
        ]

    def test_default_order_is_reverse_chronological(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Multiple full backups list newest-first."""
        timestamps = [
            "20260101_120000",
            "20260102_120000",
            "20260103_120000",
        ]
        for ts in timestamps:
            borg_e2e.make_archive(f"test-set-a-{ts}")
            borg_e2e.make_archive(f"test-set-b-{ts}")
        assert _list_borgadm(borg_e2e) == list(reversed(timestamps))

    def test_archive_names_outside_pattern_are_ignored(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Archives whose names don't match the BACKUP_NAME-set-TS
        pattern are ignored entirely (neither full nor partial)."""
        ts = "20260101_120000"
        borg_e2e.make_archive(f"test-set-a-{ts}")
        borg_e2e.make_archive(f"test-set-b-{ts}")
        borg_e2e.make_archive("manual-backup-keep-this")
        assert _list_borgadm(borg_e2e) == [ts]
        assert _list_borgadm(borg_e2e, "--no-include-partial") == [ts]
        assert _list_borgadm(borg_e2e, "--only-partial") == []

    def test_legacy_archives_classified_as_full(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Pre-NofM archives (no _NofM suffix on the name) are always
        classified as full so the upgrade does not retroactively mark
        any pre-existing archive as partial and eligible for pruning."""
        ts = "20260101_120000"
        borg_e2e.make_archive(f"test-set-a-{ts}")
        assert _list_borgadm(borg_e2e, "--no-include-partial") == [ts]
        assert _list_borgadm(borg_e2e, "--only-partial") == []

    def test_partial_requires_missing_nofm_member(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """An NofM-suffixed timestamp counts as full only when every N
        in 1..M is present at that timestamp. A timestamp with just one
        of a two-archive set is partial."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        assert _list_borgadm(borg_e2e, "--no-include-partial") == []
        assert _list_borgadm(borg_e2e, "--only-partial") == [ts]

    def test_inconsistent_m_at_one_ts_is_partial(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """If two archives at the same timestamp report different M
        values (e.g. 1of2 and 1of3) the timestamp is treated as
        partial -- the completeness rule cannot pick which M is
        authoritative, so the safe answer is "incomplete"."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 1, 3))
        assert _list_borgadm(borg_e2e, "--no-include-partial") == []
        assert _list_borgadm(borg_e2e, "--only-partial") == [ts]


# Matches archive names produced by do_create:
# `{BACKUP_NAME}-{set_name}-YYYYMMDD_HHMMSS_NofM`. Captures set name,
# timestamp, N, and M so callers can assert on the NofM suffix in
# addition to cross-archive timestamp equality. The suffix is padded
# to the width of M when M >= 10 (e.g. 01of11) so archive names sort
# correctly; N and M are captured as raw strings to let the caller
# inspect the padding rather than implicitly normalizing. The set
# token is `.+?` so tests can use arbitrary set names; the trailing
# `_\d+of\d+$` anchor pins the parse against the timestamp + suffix
# rather than against any specific set name.
_CREATE_ARCHIVE_RE = re.compile(
    r"^test-(?P<set>.+?)-(?P<ts>\d{8}_\d{6})_(?P<n>\d+)of(?P<m>\d+)$"
)


def _parse_archive_name(name: str) -> re.Match[str]:
    """Match an archive name against the do_create scheme, asserting on
    failure. Centralizes the parse so tests don't carry # type: ignore
    comments or silently drop unmatched archives via walrus filters."""
    m = _CREATE_ARCHIVE_RE.match(name)
    assert m is not None, f"archive name does not match: {name!r}"
    return m


def _set_backup_sets(
    fixture: BorgE2EFixture, sets: dict[str, dict[str, list[str]]]
) -> None:
    """Rewrite the BACKUP_SETS line in the fixture's config without
    disturbing the rest of the file. Tests use this when they need to
    exercise a config different from the fixture's default two-set
    layout (different set count, set names, or path shapes)."""
    lines = fixture.config_path.read_text().splitlines()
    rewritten = [line for line in lines if not line.startswith("BACKUP_SETS")]
    rewritten.append(f"BACKUP_SETS = {json.dumps(sets)}")
    fixture.config_path.write_text("\n".join(rewritten) + "\n")


@e2e_requires_borg
class TestE2ECreate:
    """E2E coverage for `borgadm create` archive naming.

    Pins the NofM-suffix scheme: each archive name carries a
    `_NofM` tail where M is the configured-set count and N is the
    1-based position of the set's name in sorted set-name order.
    """

    def test_create_produces_one_archive_per_set(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """A successful `borgadm create --no-prune` writes one archive
        per configured set, all sharing a single timestamp and tagged
        with a complete NofM = 1..M run."""
        result = borg_e2e.run("create", "--no-prune")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        archives = borg_e2e.archives()
        assert len(archives) == 2
        timestamps: set[str] = set()
        sets_seen: list[str] = []
        n_values: list[int] = []
        m_values: set[str] = set()
        for name in archives:
            m = _parse_archive_name(name)
            timestamps.add(m.group("ts"))
            sets_seen.append(m.group("set"))
            n_values.append(int(m.group("n")))
            m_values.add(m.group("m"))
        assert len(timestamps) == 1, (
            f"archives at different timestamps: {timestamps}"
        )
        assert sorted(sets_seen) == ["set-a", "set-b"]
        # Every archive in a run shares the same M, and N values cover
        # 1..M exactly once.
        assert m_values == {"2"}, f"mixed M values: {m_values}"
        assert sorted(n_values) == [1, 2]

    def test_create_assigns_n_by_sorted_set_name(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """N is assigned in sorted order of the BACKUP_SETS keys, not
        in cfg-dict insertion order. Uses a config whose insertion
        order is the REVERSE of sorted order (zebra before alpha) so
        a regression to dict-iteration would visibly flip the N
        assignment."""
        (borg_e2e.backup_root / "zebra").mkdir()
        (borg_e2e.backup_root / "zebra" / "f.txt").write_text("z")
        (borg_e2e.backup_root / "alpha").mkdir()
        (borg_e2e.backup_root / "alpha" / "f.txt").write_text("a")
        # Insertion order zebra-then-alpha; sorted order alpha-then-zebra.
        _set_backup_sets(
            borg_e2e,
            {
                "zebra": {"paths": ["zebra/"]},
                "alpha": {"paths": ["alpha/"]},
            },
        )
        borg_e2e.run("create", "--no-prune")
        n_by_set = {
            _parse_archive_name(name).group("set"): _parse_archive_name(
                name
            ).group("n")
            for name in borg_e2e.archives()
        }
        # alpha sorts first -> N=1; zebra second -> N=2.
        assert n_by_set == {"alpha": "1", "zebra": "2"}

    def test_create_pads_n_when_m_has_two_digits(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """When M >= 10 the suffix pads N to match M's width so archive
        names sort correctly in alphabetical listings (01of11 rather
        than 1of11, which would sort after 10of11). Rewrites the
        config to declare 11 sets and re-runs create against a fresh
        archive timestamp."""
        # Override the fixture config with 11 sets to exercise the
        # padding branch end-to-end.
        for i in range(1, 12):
            d = borg_e2e.backup_root / f"s{i:02d}"
            d.mkdir(exist_ok=True)
            (d / "f.txt").write_text("x")
        _set_backup_sets(
            borg_e2e,
            {f"s{i:02d}": {"paths": [f"s{i:02d}/"]} for i in range(1, 12)},
        )
        result = borg_e2e.run("create", "--no-prune")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        archives = borg_e2e.archives()
        assert len(archives) == 11
        # The NofM tail is the substring after the last underscore.
        suffixes = sorted(name.rsplit("_", 1)[-1] for name in archives)
        # All zero-padded to width 2; values cover 01..11.
        assert suffixes == [f"{i:02d}of11" for i in range(1, 12)]

    def test_create_dry_run_does_not_persist_archives(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm create --dry-run` exits 0 without writing archives."""
        result = borg_e2e.run("create", "--dry-run", "--no-prune")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert borg_e2e.archives() == []

    def test_create_requires_backup_sets(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm create` is the one subcommand that needs
        BACKUP_SETS to be non-empty -- it has nothing to back up
        otherwise. The validation now lives in do_create itself
        (rather than Config.__init__) so other subcommands can run
        against a repo with an empty BACKUP_SETS config."""
        _set_backup_sets(borg_e2e, {})
        result = borg_e2e.run("create", "--no-prune", check=False)
        assert result.returncode != 0
        assert "backup_sets not defined" in result.stderr.lower()

    def test_two_creates_use_distinct_timestamps(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Sequential `borgadm create` invocations produce archives at
        different timestamps. do_create stamps once at the top of the
        run and shares that stamp across the set, so successive runs
        must diverge -- otherwise the second run would collide with the
        first on archive name."""
        borg_e2e.run("create", "--no-prune")
        # Sleep 1s to guarantee a distinct YYYYMMDD_HHMMSS stamp; the
        # second create would otherwise hit `borg create` with an
        # already-existing archive name and fail.
        time.sleep(1.1)
        borg_e2e.run("create", "--no-prune")
        archives = borg_e2e.archives()
        assert len(archives) == 4
        timestamps = {
            _parse_archive_name(name).group("ts") for name in archives
        }
        assert len(timestamps) == 2


@e2e_requires_borg
class TestE2EPrune:
    """E2E coverage for `borgadm prune` retention + partial handling.

    Pins do_prune's two-stage behavior end-to-end: stage one deletes
    every partial archive unconditionally; stage two keeps full
    timestamps that satisfy the GFS retention buckets in ts_to_keep and
    deletes the rest. ts_to_keep itself is exhaustively unit-tested
    elsewhere -- the goal here is to confirm the wiring between
    list_backups, ts_to_keep, and the actual borg-delete invocations
    survives the upcoming completeness rework.
    """

    def test_prune_on_empty_repo_succeeds(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm prune` on a repo with no archives is a no-op."""
        result = borg_e2e.run("prune")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert borg_e2e.archives() == []

    def test_prune_dry_run_is_nondestructive(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`--dry-run` reports what would happen without touching the
        repo. Exercises both prune stages: a partial archive that stage
        one would delete and a stale full archive that stage two would
        prune under --keep-hourly=1."""
        for ts in ("20260101_120000", "20260101_130000"):
            borg_e2e.make_archive(f"test-set-a-{ts}")
            borg_e2e.make_archive(f"test-set-b-{ts}")
        borg_e2e.make_archive("test-set-a-20260101_140000")
        before = set(borg_e2e.archives())
        result = borg_e2e.run(
            "prune",
            "--dry-run",
            "--keep-hourly",
            "1",
            "--keep-daily",
            "0",
            "--keep-weekly",
            "0",
            "--keep-monthly",
            "0",
            "--keep-yearly",
            "0",
        )
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert set(borg_e2e.archives()) == before

    def test_prune_deletes_partials_keeps_full(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """A repo with one full set and one partial set: prune deletes
        only the partial archive."""
        full_ts = "20260102_120000"
        partial_ts = "20260101_120000"
        full_a = _archive_name("set-a", full_ts, 1, 2)
        full_b = _archive_name("set-b", full_ts, 2, 2)
        partial_a = _archive_name("set-a", partial_ts, 1, 2)
        borg_e2e.make_archive(full_a)
        borg_e2e.make_archive(full_b)
        borg_e2e.make_archive(partial_a)
        result = borg_e2e.run("prune")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert sorted(borg_e2e.archives()) == [full_a, full_b]

    def test_prune_keeps_full_backups_within_retention(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Three NofM-full backups at distinct hours all fall within
        the default 24-hourly retention window and survive prune."""
        timestamps = [
            "20260101_120000",
            "20260101_130000",
            "20260101_140000",
        ]
        for ts in timestamps:
            borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
            borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        result = borg_e2e.run("prune")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        expected = sorted(
            _archive_name(s, ts, n, 2)
            for ts in timestamps
            for s, n in (("set-a", 1), ("set-b", 2))
        )
        assert sorted(borg_e2e.archives()) == expected

    def test_prune_drops_full_backups_outside_retention(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """With --keep-hourly=1 (and other retention buckets set to 0),
        only the newest of three hourly NofM-full backups survives.

        ts_to_keep fills the hourly bucket from oldest to newest, then
        keeps the last N (newest) entries -- so 14:00 wins over 12:00
        and 13:00."""
        timestamps = [
            "20260101_120000",
            "20260101_130000",
            "20260101_140000",
        ]
        for ts in timestamps:
            borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
            borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        result = borg_e2e.run(
            "prune",
            "--keep-hourly",
            "1",
            "--keep-daily",
            "0",
            "--keep-weekly",
            "0",
            "--keep-monthly",
            "0",
            "--keep-yearly",
            "0",
        )
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert sorted(borg_e2e.archives()) == [
            _archive_name("set-a", "20260101_140000", 1, 2),
            _archive_name("set-b", "20260101_140000", 2, 2),
        ]

    def test_prune_partial_sweep_spares_legacy_archives(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """A lone pre-NofM archive looks like an incomplete set under
        naive set-membership, but the legacy bypass classifies it as
        full -- so the prune partial sweep does not touch it. With
        default retention it is the only backup and survives."""
        legacy_ts = "20260101_120000"
        borg_e2e.make_archive(f"test-set-a-{legacy_ts}")
        result = borg_e2e.run("prune")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert borg_e2e.archives() == [f"test-set-a-{legacy_ts}"]

    def test_prune_subjects_legacy_archives_to_retention(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Legacy archives are complete, not exempt: they age out
        through the same GFS retention as NofM archives. A legacy
        archive plus two newer NofM full backups, under --keep-hourly=1,
        leaves only the newest hourly bucket entry -- the legacy
        archive is pruned along with the older NofM timestamp."""
        legacy_ts = "20260101_120000"
        older_full_ts = "20260101_130000"
        newer_full_ts = "20260101_140000"
        borg_e2e.make_archive(f"test-set-a-{legacy_ts}")
        for ts in (older_full_ts, newer_full_ts):
            borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
            borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        result = borg_e2e.run(
            "prune",
            "--keep-hourly",
            "1",
            "--keep-daily",
            "0",
            "--keep-weekly",
            "0",
            "--keep-monthly",
            "0",
            "--keep-yearly",
            "0",
        )
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert sorted(borg_e2e.archives()) == [
            _archive_name("set-a", newer_full_ts, 1, 2),
            _archive_name("set-b", newer_full_ts, 2, 2),
        ]


@e2e_requires_borg
class TestE2EExtract:
    """E2E coverage for `borgadm extract --delete` path filtering.

    do_extract decides which paths under target_dir are "managed" by
    reading top-level entries from the archives being extracted. Only
    managed extras get cleaned up by --delete; any file outside every
    archive-managed root is preserved. Files that ARE in the archive
    are spared by the delete pass and overwritten by the extract pass.
    """

    @staticmethod
    def _populate_target(target_dir: Path) -> None:
        """Create the target-dir layout used by extract --delete tests:
        a managed extra under set-a/, a managed extra under set-b/, and
        an unmanaged file under other/ that sits outside every
        archive-managed path."""
        (target_dir / "set-a").mkdir()
        (target_dir / "set-a" / "extra-managed.txt").write_text(
            "managed-extra-a"
        )
        (target_dir / "set-b").mkdir()
        (target_dir / "set-b" / "extra-managed.txt").write_text(
            "managed-extra-b"
        )
        (target_dir / "other").mkdir()
        (target_dir / "other" / "preserve.txt").write_text("unmanaged")

    def test_extract_without_delete_preserves_extras(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """Without --delete, extract overlays archive contents on
        target_dir but does not remove any pre-existing files."""
        borg_e2e.run("create", "--no-prune")
        target = tmp_path / "extract-target"
        target.mkdir()
        self._populate_target(target)
        result = borg_e2e.run("extract", str(target))
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # Archive contents extracted on top.
        assert (target / "set-a" / "set-a-file.txt").is_file()
        assert (target / "set-b" / "set-b-file.txt").is_file()
        # Pre-existing extras (managed and unmanaged) all preserved.
        assert (target / "set-a" / "extra-managed.txt").is_file()
        assert (target / "set-b" / "extra-managed.txt").is_file()
        assert (target / "other" / "preserve.txt").is_file()

    def test_extract_with_delete_removes_managed_extras(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """With --delete, extract deletes pre-existing files that are
        under an archive-managed path AND not in any archive. Files
        outside every archive-managed path are preserved. Files that
        ARE in the archive are spared by the delete pass and
        overwritten by the extract pass."""
        borg_e2e.run("create", "--no-prune")
        target = tmp_path / "extract-target"
        target.mkdir()
        self._populate_target(target)
        # Pre-populate an archive-resident path with a known marker so
        # we can prove the delete pass spared it (rather than just
        # observing the extract pass writing it).
        (target / "set-a" / "set-a-file.txt").write_text("ORIGINAL")
        result = borg_e2e.run("extract", "--delete", str(target))
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # Managed extras: gone.
        assert not (target / "set-a" / "extra-managed.txt").exists()
        assert not (target / "set-b" / "extra-managed.txt").exists()
        # Archive-resident path: present, and replaced by archive
        # content (proving the delete pass spared it and the extract
        # pass overwrote).
        assert (target / "set-a" / "set-a-file.txt").read_text() == (
            "set-a content\n"
        )
        assert (target / "set-b" / "set-b-file.txt").is_file()
        # Unmanaged tree: untouched.
        assert (target / "other" / "preserve.txt").is_file()

    def test_extract_delete_dry_run_preserves_everything(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """`--dry-run --delete` neither extracts archive contents nor
        deletes managed extras."""
        borg_e2e.run("create", "--no-prune")
        target = tmp_path / "extract-target"
        target.mkdir()
        self._populate_target(target)
        result = borg_e2e.run("extract", "--delete", "--dry-run", str(target))
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # No new files written.
        assert not (target / "set-a" / "set-a-file.txt").exists()
        assert not (target / "set-b" / "set-b-file.txt").exists()
        # Nothing deleted.
        assert (target / "set-a" / "extra-managed.txt").is_file()
        assert (target / "set-b" / "extra-managed.txt").is_file()
        assert (target / "other" / "preserve.txt").is_file()

    def test_extract_delete_handles_multi_component_archive_roots(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """Backup-set paths can be multi-component relatives like
        Pictures/Family/. borg create archives just the explicit
        directory, not its parents -- the archive entries start at
        Pictures/Family with no Pictures or "" entry. archive_managed
        must still discover Pictures/Family as a managed root."""
        # Reconfigure with a multi-component backup-set path.
        nested = borg_e2e.backup_root / "Pictures" / "Family"
        nested.mkdir(parents=True)
        (nested / "photo.txt").write_text("family photo")
        _set_backup_sets(borg_e2e, {"family": {"paths": ["Pictures/Family/"]}})
        borg_e2e.run("create", "--no-prune")

        target = tmp_path / "extract-target"
        target.mkdir()
        (target / "Pictures" / "Family").mkdir(parents=True)
        (target / "Pictures" / "Family" / "extra-managed.txt").write_text(
            "managed-extra"
        )
        (target / "Pictures" / "preserve-sibling.txt").write_text(
            "unmanaged sibling under Pictures"
        )
        (target / "other").mkdir()
        (target / "other" / "preserve.txt").write_text("unmanaged")

        result = borg_e2e.run("extract", "--delete", str(target))
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # Inside the multi-component managed root: deleted.
        assert not (
            target / "Pictures" / "Family" / "extra-managed.txt"
        ).exists()
        # Archive-resident file: present.
        assert (target / "Pictures" / "Family" / "photo.txt").is_file()
        # Sibling under Pictures/ but outside Pictures/Family/: preserved.
        assert (target / "Pictures" / "preserve-sibling.txt").is_file()
        # Unrelated tree: preserved.
        assert (target / "other" / "preserve.txt").is_file()

    def test_extract_delete_uses_archive_managed_paths_not_config(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """managed-path classification reads top-level entries from the
        archives being extracted, not cfg.BACKUP_SETS. A backup set
        added to the config after the archives were created therefore
        does NOT become eligible for --delete cleanup: there is no
        archive to validate the local paths against, so the safe rule
        is to leave them alone."""
        borg_e2e.run("create", "--no-prune")
        # Add a new set-c entry to the config without creating any
        # archive for it. The fixture's source dir needs the directory
        # to exist for config validation reachable from later commands,
        # though no extract subcommand path triggers that validation.
        (borg_e2e.backup_root / "set-c").mkdir()
        (borg_e2e.backup_root / "set-c" / "f.txt").write_text("c")
        _set_backup_sets(
            borg_e2e,
            {
                "set-a": {"paths": ["set-a/"]},
                "set-b": {"paths": ["set-b/"]},
                "set-c": {"paths": ["set-c/"]},
            },
        )

        target = tmp_path / "extract-target"
        target.mkdir()
        (target / "set-a").mkdir()
        (target / "set-a" / "extra-managed.txt").write_text("managed-a")
        (target / "set-c").mkdir()
        (target / "set-c" / "extra-unmanaged.txt").write_text(
            "no archive covers me"
        )

        result = borg_e2e.run("extract", "--delete", str(target))
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # set-a extra: managed by the archive being extracted, deleted.
        assert not (target / "set-a" / "extra-managed.txt").exists()
        # set-c extra: no archive covers set-c, so the new code treats
        # it as outside every archive-managed root and preserves it,
        # even though cfg.BACKUP_SETS lists set-c.
        assert (target / "set-c" / "extra-unmanaged.txt").is_file()

    def test_extract_delete_reconciles_root_level_file(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """A backup set that is a bare file lands at the archive root.
        Its managed root is the file's parent -- target_dir itself --
        so `--delete` reconciles the whole target: a pre-existing
        root-level extra is removed, leaving only the archive's file."""
        (borg_e2e.backup_root / "new").write_text("new content")
        _set_backup_sets(borg_e2e, {"loose": {"paths": ["new"]}})
        borg_e2e.run("create", "--no-prune")

        target = tmp_path / "extract-target"
        target.mkdir()
        (target / "old").write_text("stale root-level file")

        result = borg_e2e.run("extract", "--delete", str(target))
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert (target / "new").read_text() == "new content"
        assert not (target / "old").exists()

    def test_extract_without_delete_keeps_root_level_sibling(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """Without --delete, a bare-file backup set extracts its file
        but leaves a pre-existing root-level sibling untouched."""
        (borg_e2e.backup_root / "new").write_text("new content")
        _set_backup_sets(borg_e2e, {"loose": {"paths": ["new"]}})
        borg_e2e.run("create", "--no-prune")

        target = tmp_path / "extract-target"
        target.mkdir()
        (target / "old").write_text("stale root-level file")

        result = borg_e2e.run("extract", str(target))
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert (target / "new").read_text() == "new content"
        assert (target / "old").read_text() == "stale root-level file"

    def test_extract_delete_root_file_preserves_archive_ancestor_dirs(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """A root-level file in the backup makes target_dir a managed
        root, but --delete must still spare directories that hold
        archive content. With a bare-file set plus a Pictures/Family/
        tree: the root-level extra is deleted and a stale leaf inside
        Family/ is deleted, but Pictures/ and Pictures/Family/ survive
        (borg writes no Pictures entry, yet it is an ancestor of
        archive content -- deleting it would only force a re-extract)."""
        (borg_e2e.backup_root / "new").write_text("new content")
        family = borg_e2e.backup_root / "Pictures" / "Family"
        family.mkdir(parents=True)
        (family / "photo.txt").write_text("family photo")
        _set_backup_sets(
            borg_e2e,
            {
                "loose": {"paths": ["new"]},
                "family": {"paths": ["Pictures/Family/"]},
            },
        )
        borg_e2e.run("create", "--no-prune")

        target = tmp_path / "extract-target"
        target.mkdir()
        (target / "old").write_text("stale root-level file")
        target_family = target / "Pictures" / "Family"
        target_family.mkdir(parents=True)
        (target_family / "photo.txt").write_text("stale photo")
        (target_family / "stale.txt").write_text("stale leaf inside Family")

        result = borg_e2e.run("extract", "--delete", str(target))
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # Root-level extra: deleted.
        assert not (target / "old").exists()
        # Ancestor dirs of archive content: preserved.
        assert (target / "Pictures").is_dir()
        assert target_family.is_dir()
        # Archive content: extracted (stale photo overwritten).
        assert (target / "new").read_text() == "new content"
        assert (target_family / "photo.txt").read_text() == "family photo"
        # Stale leaf inside a backed-up dir: deleted.
        assert not (target_family / "stale.txt").exists()

    def test_extract_on_empty_repo_fails(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """do_extract operates on the latest full backup; an empty repo
        has no full backups and surfaces as a BorgadmError with a
        message naming the missing-backup condition."""
        target = tmp_path / "extract-target"
        target.mkdir()
        result = borg_e2e.run("extract", str(target), check=False)
        assert result.returncode != 0
        assert "no full backups found" in result.stderr.lower()


@e2e_requires_borg
class TestE2EDelete:
    """E2E coverage for `borgadm delete` archive-resolution behavior.

    do_delete resolves the positional argument as either a full archive
    name or a YYYYMMDD_HHMMSS timestamp string. The timestamp form
    matches every archive at that timestamp, full set or partial. The
    --latest form resolves to the newest full timestamp. These tests
    pin those resolution paths so the upcoming completeness rework does
    not silently change which archives `borgadm delete` removes for a
    given input.
    """

    def test_delete_by_timestamp_removes_all_archives_at_ts(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm delete TIMESTAMP` deletes every archive whose name
        carries that timestamp, leaving other timestamps untouched."""
        keep_ts = "20260102_120000"
        delete_ts = "20260101_120000"
        for ts in (keep_ts, delete_ts):
            borg_e2e.make_archive(f"test-set-a-{ts}")
            borg_e2e.make_archive(f"test-set-b-{ts}")
        result = borg_e2e.run("delete", delete_ts)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert sorted(borg_e2e.archives()) == [
            f"test-set-a-{keep_ts}",
            f"test-set-b-{keep_ts}",
        ]

    def test_delete_by_timestamp_includes_partial_archives(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """A timestamp with only some configured sets present is still
        addressable by `borgadm delete TIMESTAMP` -- include_partial is
        on for the timestamp resolution path."""
        ts = "20260101_120000"
        borg_e2e.make_archive(f"test-set-a-{ts}")  # partial
        result = borg_e2e.run("delete", ts)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert borg_e2e.archives() == []

    def test_delete_by_archive_name_removes_single_archive(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm delete ARCHIVE_NAME` removes only the named archive,
        even when sibling archives at the same timestamp exist."""
        ts = "20260101_120000"
        borg_e2e.make_archive(f"test-set-a-{ts}")
        borg_e2e.make_archive(f"test-set-b-{ts}")
        result = borg_e2e.run("delete", f"test-set-a-{ts}")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert borg_e2e.archives() == [f"test-set-b-{ts}"]

    def test_delete_latest_removes_newest_full_set(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm delete --latest` resolves to the newest full
        timestamp and removes every archive at that timestamp.
        Partials at later timestamps are not eligible for --latest."""
        older_ts = "20260101_120000"
        newer_ts = "20260102_120000"
        partial_ts = "20260103_120000"
        for ts in (older_ts, newer_ts):
            borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
            borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        # Partial at a still-newer timestamp must not become "latest".
        borg_e2e.make_archive(_archive_name("set-a", partial_ts, 1, 2))
        result = borg_e2e.run("delete", "--latest")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert sorted(borg_e2e.archives()) == [
            _archive_name("set-a", older_ts, 1, 2),
            _archive_name("set-a", partial_ts, 1, 2),
            _archive_name("set-b", older_ts, 2, 2),
        ]

    def test_delete_dry_run_is_nondestructive(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`--dry-run` lists what would be deleted without removing
        any archive."""
        ts = "20260101_120000"
        borg_e2e.make_archive(f"test-set-a-{ts}")
        borg_e2e.make_archive(f"test-set-b-{ts}")
        before = set(borg_e2e.archives())
        result = borg_e2e.run("delete", "--dry-run", ts)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert set(borg_e2e.archives()) == before

    def test_delete_unknown_archive_fails(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Requesting a non-existent archive name surfaces a clear
        error rather than silently succeeding."""
        result = borg_e2e.run("delete", "does-not-exist", check=False)
        assert result.returncode != 0
        assert "archive not found" in result.stderr.lower()

    def test_delete_unknown_timestamp_fails(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Requesting an unknown well-formed timestamp also fails."""
        result = borg_e2e.run("delete", "19990101_000000", check=False)
        assert result.returncode != 0
        assert "no archives found for timestamp" in result.stderr.lower()


@e2e_requires_borg
class TestE2ECfgDrift:
    """E2E coverage for cfg.BACKUP_SETS drift against existing archives.

    The NofM rework's contract is that set-name choice is informational
    once an archive is written: completeness derives from the NofM
    suffix, not from cfg.BACKUP_SETS membership. These tests pin that
    contract end-to-end by mutating the config after archives exist
    and asserting that the archives stay visible, stay classified, and
    are not deleted by a subsequent prune.
    """

    def test_renamed_set_keeps_archives_classified(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Renaming a set in cfg.BACKUP_SETS does not hide archives
        that used the old set name. list_backups recognizes them by
        the {BACKUP_NAME}-...-{ts}_NofM shape; the literal set-name
        token in the middle is captured but not validated against
        cfg."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        _set_backup_sets(
            borg_e2e,
            {
                "renamed-a": {"paths": ["set-a/"]},
                "renamed-b": {"paths": ["set-b/"]},
            },
        )
        assert _list_borgadm(borg_e2e, "--no-include-partial") == [ts]
        assert _list_borgadm(borg_e2e, "--only-partial") == []

    def test_added_set_does_not_reclassify_existing_archives(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Adding a new set to cfg.BACKUP_SETS does not retroactively
        mark NofM-full archives as partial. M was current at the time
        the archive was created; the suffix records that, and an
        increase in the configured count does not invalidate prior
        coverage."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        _set_backup_sets(
            borg_e2e,
            {
                "set-a": {"paths": ["set-a/"]},
                "set-b": {"paths": ["set-b/"]},
                "set-c": {"paths": ["set-c/"]},
            },
        )
        assert _list_borgadm(borg_e2e, "--no-include-partial") == [ts]
        assert _list_borgadm(borg_e2e, "--only-partial") == []

    def test_removed_set_keeps_archives_visible(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Removing a set from cfg.BACKUP_SETS does not make archives
        for the removed set invisible. The archives stay in the repo
        and stay classified by their original NofM coverage."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        _set_backup_sets(borg_e2e, {"set-a": {"paths": ["set-a/"]}})
        assert _list_borgadm(borg_e2e, "--no-include-partial") == [ts]
        assert _list_borgadm(borg_e2e, "--only-partial") == []

    def test_renamed_set_archives_survive_prune(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """A prune run after a set rename does not delete the
        old-named archives. The classifier still sees them as full
        (NofM coverage holds) and retention preserves them as well."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        _set_backup_sets(
            borg_e2e,
            {
                "renamed-a": {"paths": ["set-a/"]},
                "renamed-b": {"paths": ["set-b/"]},
            },
        )
        result = borg_e2e.run("prune")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert sorted(borg_e2e.archives()) == [
            _archive_name("set-a", ts, 1, 2),
            _archive_name("set-b", ts, 2, 2),
        ]

    def test_list_works_with_empty_backup_sets(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm list` reads only cfg.BACKUP_NAME (for the archive-
        name prefix gate) and the per-archive NofM suffix. With
        BACKUP_SETS = {} -- a user who hasn't configured any sets
        yet, or who emptied the config -- the existing archives in
        the repo still appear, classified by their NofM coverage."""
        ts_full = "20260102_120000"
        ts_partial = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts_full, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts_full, 2, 2))
        borg_e2e.make_archive(_archive_name("set-a", ts_partial, 1, 2))
        _set_backup_sets(borg_e2e, {})
        assert _list_borgadm(borg_e2e, "--no-include-partial") == [ts_full]
        assert _list_borgadm(borg_e2e, "--only-partial") == [ts_partial]

    def test_extract_works_with_empty_backup_sets(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """`borgadm extract` succeeds on the latest full backup even
        when BACKUP_SETS has been emptied. The managed-paths logic
        reads only the archives being extracted, so a config-drifted
        user can still restore."""
        borg_e2e.run("create", "--no-prune")
        _set_backup_sets(borg_e2e, {})
        target = tmp_path / "extract-target"
        target.mkdir()
        result = borg_e2e.run("extract", str(target))
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert (target / "set-a" / "set-a-file.txt").is_file()
        assert (target / "set-b" / "set-b-file.txt").is_file()

    def test_extract_works_on_legacy_archive_with_empty_backup_sets(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """Pre-NofM archives in a repo with BACKUP_SETS = {}: the
        legacy bypass classifies them as complete, list_backups
        returns them, and extract restores their content. This is
        the upgrade-from-old-borgadm scenario."""
        # Use borgadm create (so the archive carries cwd-relative
        # paths, matching what an older borgadm would have produced)
        # then rename the result to a suffix-less legacy form. Limit
        # the config to a single set so create produces just one
        # archive; that lets the rename target be unambiguous.
        _set_backup_sets(borg_e2e, {"set-a": {"paths": ["set-a/"]}})
        borg_e2e.run("create", "--no-prune")
        suffixed = borg_e2e.archives()
        assert len(suffixed) == 1
        legacy_short = suffixed[0].rsplit("_", 1)[0]  # drop _1of1
        borg_e2e.borg(
            "rename", f"{borg_e2e.repo_path}::{suffixed[0]}", legacy_short
        )
        _set_backup_sets(borg_e2e, {})
        target = tmp_path / "extract-target"
        target.mkdir()
        result = borg_e2e.run("extract", str(target))
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert (target / "set-a" / "set-a-file.txt").is_file()

    def test_prune_works_with_empty_backup_sets(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm prune` with empty BACKUP_SETS still sweeps partial
        archives and retention-prunes full ones. Classification is
        archive-driven, so config drift does not interfere."""
        full_ts = "20260102_120000"
        partial_ts = "20260101_120000"
        full_a = _archive_name("set-a", full_ts, 1, 2)
        full_b = _archive_name("set-b", full_ts, 2, 2)
        partial_a = _archive_name("set-a", partial_ts, 1, 2)
        borg_e2e.make_archive(full_a)
        borg_e2e.make_archive(full_b)
        borg_e2e.make_archive(partial_a)
        _set_backup_sets(borg_e2e, {})
        result = borg_e2e.run("prune")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # Partial archive swept; full set kept.
        assert sorted(borg_e2e.archives()) == [full_a, full_b]

    def test_delete_works_with_empty_backup_sets(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm delete TIMESTAMP` resolves archives via
        list_backups, which is archive-driven, so empty BACKUP_SETS
        does not block deletion of legacy archives in the repo."""
        keep_ts = "20260102_120000"
        delete_ts = "20260101_120000"
        for ts in (keep_ts, delete_ts):
            borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
            borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        _set_backup_sets(borg_e2e, {})
        result = borg_e2e.run("delete", delete_ts)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert sorted(borg_e2e.archives()) == [
            _archive_name("set-a", keep_ts, 1, 2),
            _archive_name("set-b", keep_ts, 2, 2),
        ]


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
