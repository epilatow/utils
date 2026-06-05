#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "pytest-xdist", "tomlkit"]
# ///
# This is human generated code that's been AI modified

"""
Unit tests for borgadm
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import importlib.machinery
import importlib.util
import io
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
import tomllib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from conftest import (
    CmdCallbacksBase,
    ExceptionHierarchyBase,
    UnknownArgRoutedToSubparserBase,
)

# Repository root directory (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent

# Real user uv cache, captured at import time before the per-test
# `_isolate_home` fixture rewrites $HOME. Subprocesses launched out of
# e2e fixtures inherit this via UV_CACHE_DIR so they hit the warm
# parent cache instead of building a fresh venv per call under their
# fake HOME.
_REAL_UV_CACHE_DIR = os.environ.get(
    "UV_CACHE_DIR", os.path.expanduser("~/.cache/uv")
)

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
    basename: str = ba.BASENAME
    old_config = ba.CONFIG
    old_logfile = ba.LOGFILE

    os.environ["HOME"] = str(fake_home)
    tempfile.tempdir = str(tmp_path)
    # ba is a dynamically loaded module typed as ModuleType; a direct
    # attribute write fails mypy --strict (attr-defined), so setattr
    # stays.
    setattr(ba, "CONFIG", Path(fake_home / f".{basename}"))  # noqa: B010
    setattr(  # noqa: B010
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
        setattr(ba, "CONFIG", old_config)  # noqa: B010
        setattr(ba, "LOGFILE", old_logfile)  # noqa: B010


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
    original_cfg = ba.CFG
    # ba is a dynamically loaded module typed as ModuleType; a direct
    # attribute write fails mypy --strict (attr-defined), so setattr
    # stays.
    setattr(ba, "CFG", cfg)  # noqa: B010
    yield cfg
    setattr(ba, "CFG", original_cfg)  # noqa: B010


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
        # Borgadm is a `uv run --script` entrypoint, so each subprocess
        # call resolves its script venv via uv's cache. Without this, uv
        # falls back to $HOME/.cache/uv -- our fake-HOME -- which is
        # empty for every test and forces a fresh build per invocation.
        # Inherit the real user's uv cache (captured at module import
        # before the autouse `_isolate_home` fixture rewrote $HOME) so
        # subprocesses hit the warm parent cache. uv's cache is
        # content-addressed and lock-coordinated, so cross-process
        # sharing is safe.
        env.setdefault("UV_CACHE_DIR", _REAL_UV_CACHE_DIR)
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
    n: int,
    m: int,
    backup_name: str = "test",
) -> str:
    """Build an archive name string for the test fixture's BACKUP_NAME.

    Delegates to the real ba._ArchiveName so the tests exercise the
    production renderer rather than a parallel copy of the format.
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


def _require_borg_or_fail() -> None:
    if not _have_borg():
        pytest.fail(
            "borg must be installed to run the E2E suite (--e2e was "
            "requested but `borg` is not on PATH)."
        )


@pytest.fixture
def borg_e2e(_isolate_home: Path, tmp_path: Path) -> Iterator[BorgE2EFixture]:
    """Spin up a real local borg repo with a minimal borgadm config so
    tests can drive `borgadm` via subprocess against actual archives.

    Layout:
      <_isolate_home>/             # HOME for the subprocess
        .borgadm                   # config pointing at the local repo
        .borg_passphrase           # dummy, repo uses --encryption=none
      <tmp_path>/repo/             # the borg repo (encryption=none)
      <tmp_path>/src/<set>/...     # source dirs, one per backup set
    """
    _require_borg_or_fail()
    home = _isolate_home
    repo_path = tmp_path / "repo"
    backup_root = tmp_path / "src"
    backup_root.mkdir()

    sets: dict[str, list[Path]] = {}
    for set_name, paths in _E2E_SETS.items():
        absolute_paths: list[Path] = []
        for rel in paths:
            full = backup_root / rel.rstrip("/")
            full.mkdir(parents=True)
            (full / f"{set_name}-file.txt").write_text(f"{set_name} content\n")
            absolute_paths.append(full)
        sets[set_name] = absolute_paths

    passphrase_file = home / ".borg_passphrase"
    passphrase_file.write_text("e2e-test-passphrase\n")
    passphrase_file.chmod(0o600)

    subprocess.run(
        ["borg", "init", "--encryption=none", str(repo_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    backup_sets_cfg = {name: {"paths": _E2E_SETS[name]} for name in _E2E_SETS}
    config_path = home / ".borgadm"
    config_path.write_text(
        f"BORG_REPO = {repo_path}\n"
        f"BACKUP_NAME = test\n"
        f"BACKUP_ROOT = {backup_root}\n"
        f"BORG_PASSPHRASE_FILE = {passphrase_file}\n"
        f"BACKUP_SETS = {json.dumps(backup_sets_cfg)}\n"
    )

    yield BorgE2EFixture(
        repo_path=repo_path,
        backup_root=backup_root,
        home=home,
        config_path=config_path,
        sets=sets,
    )


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
        result = ba.rewrite_legacy_args(["borgadm", "check-age", "--verbose"])
        assert result == [
            "borgadm",
            "check",
            "age",
            "--verbose",
        ]

    def test_check_legacy_rewrite_ignores_non_legacy(self) -> None:
        """Test that non-legacy commands pass through unchanged."""
        argv = ["borgadm", "create", "--dry-run"]
        assert ba.rewrite_legacy_args(argv) == argv

    def test_repair_subcommand_parses(self) -> None:
        """Test repair delete-cache subcommand parses."""
        parser = ba.args_parser()
        args = parser.parse_command(["repair", "delete-cache"])
        assert args.command == "repair delete-cache"

    def test_repair_repo_yes_parses(self) -> None:
        """Test repair repo --yes parses."""
        parser = ba.args_parser()
        args = parser.parse_command(["repair", "repo", "--yes"])
        assert args.command == "repair repo"
        assert args.yes is True

    def test_repair_repo_no_yes_parses(self) -> None:
        """Test repair repo without --yes defaults to False."""
        parser = ba.args_parser()
        args = parser.parse_command(["repair", "repo"])
        assert args.command == "repair repo"
        assert args.yes is False

    def test_common_args_rejected_before_action(self) -> None:
        """Common args between subcommand and action should fail."""
        parser = ba.args_parser()
        cases = [
            ["check", "--verbose", "age"],
            ["check", "--config", "/tmp/c", "age"],
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
        args = parser.parse_args(["create", "--timestamp-messages"])
        assert args.timestamp_messages is True
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


class TestUnknownArgRoutedToSubparser(UnknownArgRoutedToSubparserBase):
    """Unknown args print the subcommand's usage, including nested."""

    PARSER_FUNC = staticmethod(ba.args_parser)
    CASES = [
        (["list", "--bogus"], "list"),
        (["check", "age", "--bogus"], "check age"),
        (["repair", "repo", "--bogus"], "repair repo"),
    ]


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
        """
        with (
            patch.object(ba, "Config", return_value=mock_cfg),
            patch.object(ba, "initialize_borg_environment"),
        ):
            yield mock_cfg


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

    @pytest.mark.usefixtures("mock_cfg")
    def test_repair_repo_without_yes_exits(self) -> None:
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
        args = parser.parse_command(["delete", "20250101_120000"])
        assert args.command == "delete"
        assert args.archive == "20250101_120000"
        assert args.latest is False

    def test_delete_parses_latest(self) -> None:
        """Test delete subcommand parses --latest flag."""
        parser = ba.args_parser()
        args = parser.parse_command(["delete", "--latest"])
        assert args.command == "delete"
        assert args.archive is None
        assert args.latest is True

    def test_delete_parses_archive_name(self) -> None:
        """Test delete subcommand parses a full archive name."""
        parser = ba.args_parser()
        args = parser.parse_command(["delete", "home-local-20250101_120000"])
        assert args.command == "delete"
        assert args.archive == "home-local-20250101_120000"

    def test_delete_latest_and_archive_errors(self) -> None:
        """Test that --latest with an archive is a parser error."""
        parser = ba.args_parser()
        args = parser.parse_command(["delete", "--latest", "20250101_120000"])
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
            **_kwargs: object,
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

    @pytest.mark.usefixtures("mock_cfg")
    def test_delete_latest_no_backups(self) -> None:
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
            **_kwargs: object,
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

    @pytest.mark.usefixtures("mock_cfg")
    def test_delete_by_timestamp_not_found(self) -> None:
        """Test deleting a nonexistent timestamp exits with error."""

        def list_backups_side_effect(
            **_kwargs: object,
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

    @pytest.mark.usefixtures("mock_cfg")
    def test_delete_by_archive_name_not_found(self) -> None:
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
            **_kwargs: object,
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
            **_kwargs: object,
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
        unknown: list[str] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        if full is None:
            full = self.FULL_BACKUPS
        if partial is None:
            partial = self.PARTIAL_BACKUPS
        if unknown is None:
            unknown = []
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
            patch.object(
                ba,
                "list_unknown_archives",
                autospec=True,
                return_value=unknown,
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

    @pytest.mark.usefixtures("mock_cfg")
    def test_list_defaults(self, caplog: Any) -> None:
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

    @pytest.mark.usefixtures("mock_cfg")
    def test_list_no_keep_tags(self, caplog: Any) -> None:
        """--no-keep-tags omits keep tags."""
        msgs = self._run_list(caplog, keep_tags=False)
        assert any("20250103_120000" in m for m in msgs)
        assert not any("(hour)" in m for m in msgs)
        assert not any("(prune)" in m for m in msgs)

    @pytest.mark.usefixtures("mock_cfg")
    def test_list_no_include_partial(self, caplog: Any) -> None:
        """--no-include-partial excludes partial backups."""
        msgs = self._run_list(caplog, include_partial=False)
        assert any("20250103_120000" in m for m in msgs)
        assert not any("20250104_060000" in m for m in msgs)

    @pytest.mark.usefixtures("mock_cfg")
    def test_list_only_partial(self, caplog: Any) -> None:
        """--only-partial shows only partial backups."""
        msgs = self._run_list(caplog, only_partial=True)
        assert any("20250104_060000" in m for m in msgs)
        assert not any("20250103_120000" in m for m in msgs)

    @pytest.mark.usefixtures("mock_cfg")
    def test_list_full_names(self, caplog: Any) -> None:
        """--full-names shows full archive names."""
        msgs = self._run_list(caplog, full_names=True)
        assert any("foobar::home-set1-20250103_120000" in m for m in msgs)

    @pytest.mark.usefixtures("mock_cfg")
    def test_list_latest(self, caplog: Any) -> None:
        """--latest shows only the most recent backup."""
        msgs = self._run_list(caplog, latest=True)
        assert any("20250103_120000" in m for m in msgs)
        assert not any("20250102_120000" in m for m in msgs)
        assert not any("20250101_120000" in m for m in msgs)

    @pytest.mark.usefixtures("mock_cfg")
    def test_list_warns_per_unknown_archive(self, caplog: Any) -> None:
        """Each archive whose name starts with BACKUP_NAME- but
        doesn't parse becomes one WARNING log record, so a future
        grep or summary can act on each name independently."""
        self._run_list(
            caplog,
            unknown=["home-garbage", "home-stale-archive"],
        )
        warnings = [
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert any("home-garbage" in m for m in warnings)
        assert any("home-stale-archive" in m for m in warnings)

    @pytest.mark.usefixtures("mock_cfg")
    def test_list_no_warning_when_no_unknowns(self, caplog: Any) -> None:
        """A clean repo (no unknown archives) emits no WARNING records
        from the unknown-archive surface."""
        self._run_list(caplog)
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == []


class TestListUnknownArchives:
    """Unit-level coverage of list_unknown_archives.

    Drives the classifier through a mocked list_backups_raw so the
    edge cases (foreign prefix, malformed shape, borg checkpoint
    archives) can be exercised without standing up a real borg repo.
    """

    @staticmethod
    def _make_raw(lines: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["borg", "list"],
            returncode=0,
            stdout="\n".join(lines) + "\n" if lines else "",
            stderr="",
        )

    def _run(self, lines: list[str]) -> list[str]:
        # list_backups_raw is functools.cache-d, so clear before each
        # mock injection to avoid cross-test contamination.
        ba.list_backups_raw.cache_clear()
        with patch.object(
            ba,
            "list_backups_raw",
            autospec=True,
            return_value=self._make_raw(lines),
        ):
            result: list[str] = ba.list_unknown_archives()
            return result

    @pytest.mark.usefixtures("mock_cfg")
    def test_empty_repo_returns_empty(self) -> None:
        assert self._run([]) == []

    @pytest.mark.usefixtures("mock_cfg")
    def test_well_formed_archives_are_silent(self) -> None:
        """Archives that parse cleanly are not unknown."""
        result = self._run(
            [
                "home-set1-20260101_120000_1of2",
                "home-set1-20260101_120000_2of2",
            ],
        )
        assert result == []

    @pytest.mark.usefixtures("mock_cfg")
    def test_foreign_prefix_is_silent(self) -> None:
        """A name that doesn't start with BACKUP_NAME- isn't ours
        and stays out of the unknown list."""
        assert self._run(["manual-backup-keep-this"]) == []

    @pytest.mark.usefixtures("mock_cfg")
    def test_home_prefix_malformed_is_unknown(self) -> None:
        """Anything that looks like ours but doesn't parse surfaces."""
        result = self._run(["home-garbage", "home-set1-not-a-timestamp"])
        assert result == ["home-garbage", "home-set1-not-a-timestamp"]

    @pytest.mark.usefixtures("mock_cfg")
    def test_borg_checkpoint_is_silent(self) -> None:
        """`.checkpoint` and `.checkpoint.N` on a full archive shape
        (timestamp + NofM) are borg-managed intermediate state and
        are filtered out before the unknown-archive check so borg's
        own cleanup remains in charge."""
        result = self._run(
            [
                "home-set1-20260101_120000_1of2.checkpoint",
                "home-set1-20260101_120000_1of2.checkpoint.42",
            ],
        )
        assert result == []

    @pytest.mark.usefixtures("mock_cfg")
    def test_checkpoint_filter_requires_full_archive_shape(self) -> None:
        """A `.checkpoint` not preceded by the full archive shape
        (timestamp + NofM) is not a real borg checkpoint -- it's a
        malformed artifact and surfaces as unknown. Covers both a
        name with no timestamp and a checkpoint of a suffix-less
        archive (which is itself an unknown shape per the same
        rule)."""
        result = self._run(
            [
                "home-stale.checkpoint",
                "home-set1-20260101_120000.checkpoint",
            ],
        )
        assert result == [
            "home-set1-20260101_120000.checkpoint",
            "home-stale.checkpoint",
        ]

    @pytest.mark.usefixtures("mock_cfg")
    def test_sorted_output(self) -> None:
        """Order is deterministic regardless of borg's emission order."""
        result = self._run(
            ["home-zzz", "home-aaa", "home-mmm"],
        )
        assert result == ["home-aaa", "home-mmm", "home-zzz"]


class TestAutomate:
    """Test the automate subcommand (crony-backed)."""

    JOB_OPS = [
        "create",
        "check-age",
        "check-prune",
        "check-repo",
        "check-archives",
    ]

    @pytest.fixture
    def automate_env(
        self, tmp_path: Path, monkeypatch: Any
    ) -> Iterator[tuple[Path, Any, Any]]:
        """Darwin + a temp crony drop-in dir + mocked run_cmd (so crony
        is never really invoked) + no wrapper rebuild.
        """
        dropin = tmp_path / "crony-config"
        monkeypatch.setenv("CRONY_CONFIG_DROPIN_DIR", str(dropin))
        with (
            patch.object(ba.platform, "system", return_value="Darwin"),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(
                ba,
                "_wrapper_needs_rebuild",
                autospec=True,
                return_value=(False, ""),
            ),
            patch.object(ba, "_build_wrapper", autospec=True) as mock_build,
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            yield dropin, mock_run, mock_build

    @staticmethod
    def _crony_calls(mock_run: Any) -> list[list[str]]:
        """The crony argv (after the crony path) of each run_cmd call."""
        return [list(call.args[0][1:]) for call in mock_run.call_args_list]

    def test_enable_writes_bundle_and_applies(self, automate_env: Any) -> None:
        dropin, mock_run, _ = automate_env
        ba.do_automate_enable()
        bundle = dropin / "borgadm.toml"
        assert bundle.exists()
        text = bundle.read_text()
        for op in self.JOB_OPS:
            assert f"[job.{op}]" in text
        assert ["apply", "-b", "borgadm"] in self._crony_calls(mock_run)

    def test_enable_builds_wrapper_when_needed(self, automate_env: Any) -> None:
        _dropin, _mock_run, mock_build = automate_env
        # The fixture already patched _wrapper_needs_rebuild (so no
        # autospec here -- can't spec an existing Mock); flip it to
        # signal a rebuild is needed.
        with patch.object(
            ba,
            "_wrapper_needs_rebuild",
            return_value=(True, "stale source"),
        ):
            ba.do_automate_enable()
        mock_build.assert_called_once()

    def test_disable_destroys_and_removes_bundle(
        self, automate_env: Any
    ) -> None:
        dropin, mock_run, _ = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        ba.do_automate_disable()
        assert ["destroy", "-b", "borgadm"] in self._crony_calls(mock_run)
        assert not bundle.exists()

    def test_disable_is_noop_when_not_enabled(self, automate_env: Any) -> None:
        # No bundle file: crony has nothing addressable and exits nonzero,
        # but disable must treat that as a clean no-op, not an error.
        dropin, mock_run, _ = automate_env
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="unknown bundle"
        )
        ba.do_automate_disable()
        assert not (dropin / "borgadm.toml").exists()

    def test_disable_surfaces_failure_when_bundle_present(
        self, automate_env: Any
    ) -> None:
        # The bundle is installed (file present) but destroy fails for a
        # real reason (a running job holds the lock); that must surface
        # rather than silently leaving the file in place.
        dropin, mock_run, _ = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=7, stdout="", stderr="lock held"
        )
        with pytest.raises(ba.BorgadmError, match="crony destroy failed"):
            ba.do_automate_disable()
        assert bundle.exists()

    def test_status_shells_out_to_crony(self, automate_env: Any) -> None:
        dropin, mock_run, _ = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        ba.do_automate_status()
        assert ["status", "-b", "borgadm"] in self._crony_calls(mock_run)

    def test_status_clean_when_not_enabled(
        self, automate_env: Any, caplog: Any
    ) -> None:
        # No bundle file: report the not-enabled state directly instead
        # of shelling out to crony (which would just error).
        _dropin, mock_run, _ = automate_env
        with caplog.at_level(logging.INFO):
            ba.do_automate_status()
        assert ["status", "-b", "borgadm"] not in self._crony_calls(mock_run)
        assert any("not enabled" in r.message for r in caplog.records)

    def test_enable_requires_darwin(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(ba.platform, "system", lambda: "Linux")
        with pytest.raises(ba.BorgadmError, match="only supported on osx"):
            ba.do_automate_enable()

    def test_deterministic_uuids(self) -> None:
        assert ba._crony_job_uuid("create") == ba._crony_job_uuid("create")
        assert ba._crony_job_uuid("create") != ba._crony_job_uuid("check-age")
        # canonical lowercase UUID form (crony requires it)
        parsed = uuid.UUID(ba._crony_job_uuid("create"))
        assert str(parsed) == ba._crony_job_uuid("create")

    def test_create_silenced_checks_inherit(self) -> None:
        doc = tomllib.loads(ba._render_crony_bundle())
        # Bundle default: inherit the user's default channels AND pop a
        # desktop dialog on failure.
        assert doc["defaults"]["notify_channels"] == [
            "default",
            "dialog-popup",
        ]
        # create overrides to silent; the checks omit notify_channels,
        # inheriting the bundle default above.
        assert doc["job"]["create"]["notify_channels"] == []
        assert "notify_channels" not in doc["job"]["check-age"]
        # create treats borg's transient-warning exit 1 as success; the
        # checks keep the default (a check warning is a real signal).
        assert doc["job"]["create"]["success_exit_codes"] == [1]
        assert "success_exit_codes" not in doc["job"]["check-age"]
        # priority, keep_awake, env, and the disabled wallclock cap are
        # the same for every job, so they live in [defaults] and no job
        # overrides them. (borgadm caps each borg command itself, so
        # job_timeout_sec = 0 leaves that timeout the sole authority.)
        assert doc["defaults"]["priority"] == "high"
        assert doc["defaults"]["keep_awake"] is True
        assert doc["defaults"]["job_timeout_sec"] == 0
        assert doc["defaults"]["env"] == {"PATH": "$HOME/.local/bin:$PATH"}
        for op in self.JOB_OPS:
            assert "priority" not in doc["job"][op]
            assert "keep_awake" not in doc["job"][op]
            assert "job_timeout_sec" not in doc["job"][op]
            assert "env" not in doc["job"][op]
        # Target keys on this host.
        host = ba._current_host()
        assert "darwin" not in doc["target"]
        assert doc["target"]["host"][host]["jobs"] == self.JOB_OPS

    def test_generated_bundle_validates_against_crony(
        self, tmp_path: Path
    ) -> None:
        # The bundle borgadm generates must be accepted by the real
        # crony's validator, so a future crony schema change that breaks
        # borgadm fails here rather than at install time.
        f = tmp_path / "borgadm.toml"
        f.write_text(ba._render_crony_bundle())
        crony = ba._crony_path()
        proc = subprocess.run(
            [crony, "config", "validate", "--file", str(f)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestLogFiles:
    """Test the log-files subcommand (crony-backed)."""

    def test_shows_default_logfile_only_without_bundle(
        self, monkeypatch: Any, caplog: Any
    ) -> None:
        monkeypatch.setattr(ba.platform, "system", lambda: "Darwin")
        with patch.object(
            ba,
            "_crony_bundle_path",
            autospec=True,
            return_value=Path("/nonexistent/borgadm.toml"),
        ):
            with caplog.at_level(logging.INFO):
                ba.do_log_files()
        messages = [r.message for r in caplog.records]
        assert messages == [str(ba.LOGFILE)]

    def test_no_crony_paths_off_darwin(
        self, monkeypatch: Any, caplog: Any
    ) -> None:
        monkeypatch.setattr(ba.platform, "system", lambda: "Linux")
        with caplog.at_level(logging.INFO):
            ba.do_log_files()
        messages = [r.message for r in caplog.records]
        assert messages == [str(ba.LOGFILE)]

    def test_shows_crony_log_paths_when_bundle_present(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        monkeypatch.setattr(ba.platform, "system", lambda: "Darwin")
        bundle = tmp_path / "borgadm.toml"
        bundle.write_text("# stub\n")
        log_paths = {
            op: f"/state/crony/borgadm/u-{op}/run.log"
            for op in TestAutomate.JOB_OPS
        }

        def fake_run(
            cmd: list[str], *_args: object, **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            # cmd == [crony, "logs", "borgadm.<job>", "-p"]
            job = cmd[2].split(".", 1)[1]
            return subprocess.CompletedProcess(
                cmd, 0, log_paths[job] + "\n", ""
            )

        with (
            patch.object(
                ba, "_crony_bundle_path", autospec=True, return_value=bundle
            ),
            patch.object(ba, "run_cmd", autospec=True, side_effect=fake_run),
        ):
            with caplog.at_level(logging.INFO):
                ba.do_log_files()
        messages = [r.message for r in caplog.records]
        assert messages[0] == str(ba.LOGFILE)
        for lp in log_paths.values():
            assert lp in messages


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
        """Verify all check subcommands accept the common args."""
        parser = ba.args_parser()
        for action in self._check_subcommands():
            # Should parse without error
            args = parser.parse_args(["check", action, "--verbose"])
            assert args.verbose is True, (
                f"check {action} did not accept --verbose"
            )

    @pytest.mark.usefixtures("mock_cfg")
    def test_check_all_runs_all_checks(self) -> None:
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

    @pytest.mark.usefixtures("mock_cfg")
    def test_check_age_no_backups(self) -> None:
        """Test check age raises CheckNoBackupsError."""
        with (
            patch.object(ba, "list_backups", autospec=True, return_value={}),
            pytest.raises(ba.CheckNoBackupsError),
        ):
            ba.do_check_age()

    @pytest.mark.usefixtures("mock_cfg")
    def test_check_age_too_old(self) -> None:
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

    @pytest.mark.usefixtures("mock_cfg")
    def test_check_age_ok(self) -> None:
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

    @pytest.mark.usefixtures("mock_cfg")
    def test_check_archives_no_backups(self) -> None:
        """Test check archives raises CheckNoBackupsError."""
        with (
            patch.object(ba, "list_backups", autospec=True, return_value={}),
            pytest.raises(ba.CheckNoBackupsError),
        ):
            ba.do_check_archives(progress=False)

    @pytest.mark.usefixtures("mock_cfg")
    def test_check_prune_partial_archives(self) -> None:
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


class TestIsRemoteRepo:
    """Test the BORG_REPO local-vs-remote classifier."""

    def test_ssh_scheme(self) -> None:
        assert ba._is_remote_repo("ssh://user@host/srv/borg") is True
        assert ba._is_remote_repo("ssh://user@host:2222/srv/borg") is True

    def test_user_at_host_colon_path(self) -> None:
        assert ba._is_remote_repo("user@host:/srv/borg") is True
        assert ba._is_remote_repo("host:/srv/borg") is True
        assert ba._is_remote_repo("backup-host:repos/main") is True

    def test_local_absolute_path(self) -> None:
        assert ba._is_remote_repo("/var/backups/borg") is False

    def test_local_relative_path(self) -> None:
        assert ba._is_remote_repo("backups/borg") is False
        assert ba._is_remote_repo("~/backups/borg") is False

    def test_empty_string(self) -> None:
        assert ba._is_remote_repo("") is False


class TestInitializeBorgEnvironmentSshGating:
    """Ssh-agent and BORG_RSH setup skipped for local repos."""

    @pytest.fixture(autouse=True)
    def _reset_env_and_state(
        self, monkeypatch: Any, mock_cfg: Any
    ) -> Iterator[Any]:
        """Reset the once-per-process init flag plus the env vars the
        function touches so each test sees a clean slate."""
        monkeypatch.setattr(ba, "_borg_env_initialized", False)
        for var in ("BORG_RSH", "BORG_PASSPHRASE", "BORG_REMOTE_PATH"):
            monkeypatch.delenv(var, raising=False)
        yield mock_cfg
        monkeypatch.setattr(ba, "_borg_env_initialized", False)

    @contextlib.contextmanager
    def _patch_externals(self, repo: str) -> Iterator[Any]:
        """Patch the side-effecting helpers and yield the ssh-agent
        mock so tests can assert against its call status."""
        with (
            patch.object(ba, "load_passphrase", return_value="pw"),
            patch.object(ba, "start_ssh_agent", autospec=True) as mock_agent,
            patch.object(ba.CFG, "BORG_REPO", repo),
            patch.object(ba.CFG, "BORG_REPO_HOSTKEY", "fake-hostkey"),
        ):
            yield mock_agent

    def test_local_path_skips_ssh_setup(self) -> None:
        with self._patch_externals("/var/backups/borg") as mock_agent:
            ba.initialize_borg_environment()
            assert mock_agent.called is False
        assert "BORG_RSH" not in os.environ
        assert os.environ.get("BORG_PASSPHRASE") == "pw"

    def test_ssh_scheme_runs_ssh_setup(self) -> None:
        with self._patch_externals("ssh://user@host/srv/borg") as mock_agent:
            ba.initialize_borg_environment()
            assert mock_agent.called is True
        assert "BORG_RSH" in os.environ
        assert "ssh -F /dev/null" in os.environ["BORG_RSH"]

    def test_user_at_host_runs_ssh_setup(self) -> None:
        with self._patch_externals("user@host:/srv/borg") as mock_agent:
            ba.initialize_borg_environment()
            assert mock_agent.called is True
        assert "BORG_RSH" in os.environ


class TestDoEnvironment:
    """do_environment prints ssh-add only for remote BORG_REPO."""

    @pytest.mark.usefixtures("mock_cfg")
    def test_local_repo_omits_ssh_add(self, caplog: Any) -> None:
        with patch.object(ba.CFG, "BORG_REPO", "/var/backups/borg"):
            with caplog.at_level(logging.INFO):
                ba.do_environment()
        messages = [r.message for r in caplog.records]
        assert not any("ssh-add" in m for m in messages)
        assert any("export BORG_PASSPHRASE=" in m for m in messages)
        assert any("export BORG_REPO=" in m for m in messages)

    @pytest.mark.usefixtures("mock_cfg")
    def test_remote_repo_emits_ssh_add(self, caplog: Any) -> None:
        with patch.object(ba.CFG, "BORG_REPO", "user@host:/srv/borg"):
            with caplog.at_level(logging.INFO):
                ba.do_environment()
        messages = [r.message for r in caplog.records]
        assert any("ssh-add -q" in m for m in messages)


class TestRequireBorgOrFail:
    """The borg_e2e fixture's preflight check.

    Hard-fails (rather than silently skipping) when borg is missing
    so that an explicit --e2e request on a borg-less machine
    surfaces as a real test failure.
    """

    def test_passes_when_borg_present(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/borg")
        _require_borg_or_fail()

    def test_fails_when_borg_missing(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: None)
        with pytest.raises(pytest.fail.Exception) as exc_info:
            _require_borg_or_fail()
        assert "borg must be installed" in str(exc_info.value)


class TestDoSelfTest:
    """Test do_self_test argv assembly."""

    def _captured_argv(self, **kwargs: Any) -> list[str]:
        with patch.object(ba.subprocess, "run", autospec=True) as run:
            run.return_value = Mock(returncode=0)
            ba.do_self_test(**kwargs)
            return list(run.call_args.args[0])

    def test_default_omits_flags(self) -> None:
        argv = self._captured_argv()
        assert "--verbose" not in argv
        assert "--coverage" not in argv
        assert "--e2e" not in argv

    def test_e2e_flag_forwarded(self) -> None:
        """--e2e is the canonical flag for verifying borgadm changes."""
        argv = self._captured_argv(e2e=True)
        assert "--e2e" in argv

    def test_verbose_and_coverage_forwarded(self) -> None:
        argv = self._captured_argv(verbose=True, coverage=True)
        assert "--verbose" in argv
        assert "--coverage" in argv


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

    def test_initialize_logger_is_idempotent(self, tmp_path: Path) -> None:
        """Repeated initialize_logger calls don't accumulate handlers.

        Each cli() invocation in a long-running process (test session,
        embedded use) calls initialize_logger. Without cleanup, every
        call would attach four more handlers (memory / file / stdout /
        stderr) to the root logger, each holding open a file descriptor
        and writing every log line one extra time -- driving session
        time and log-file size up roughly linearly per cli() call.
        """
        root = logging.getLogger()
        old_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            ba.initialize_logger(str(tmp_path / "a.log"))
            after_first = len(root.handlers)
            for i in range(5):
                ba.initialize_logger(str(tmp_path / f"{i}.log"))
            assert len(root.handlers) == after_first

            for h in root.handlers:
                if isinstance(h, logging.FileHandler):
                    assert h.baseFilename == str(tmp_path / "4.log")
        finally:
            for h in list(root.handlers):
                root.removeHandler(h)
                h.close()
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
                command="check age",
                config=str(ba.CONFIG),
                verbose=False,
                timestamp_messages=False,
                args_dict={},
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
            caplog.at_level(logging.INFO),
            pytest.raises(ba.BorgadmError),
        ):
            ba.main(
                command="compact",
                config=str(ba.CONFIG),
                verbose=False,
                timestamp_messages=False,
                args_dict={},
            )

        assert any(
            "borgadm compact: finished (elapsed:" in r.message
            for r in caplog.records
        )


class TestAutomateTimestampFlag:
    """Every automated job passes --timestamp-messages."""

    def test_bundle_commands_include_timestamp_messages(self) -> None:
        for _name, command, _interval in ba._crony_jobs():
            assert "--timestamp-messages" in command, command


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


class TestCli:
    """Test cli() entry point."""

    @pytest.mark.usefixtures("mock_cfg")
    def test_cli_returns_warning(self) -> None:
        """cli() returns WARNING when _warning_occurred is set."""
        original = ba._warning_occurred
        try:
            with (
                patch("sys.argv", ["borgadm", "environment"]),
                patch.object(ba, "main", autospec=True),
            ):
                # ba is a dynamically loaded module typed as ModuleType;
                # a direct attribute write fails mypy --strict
                # (attr-defined), so setattr stays.
                setattr(ba, "_warning_occurred", True)  # noqa: B010
                assert ba.cli() == ba.ExitCode.WARNING
        finally:
            setattr(ba, "_warning_occurred", original)  # noqa: B010

    @pytest.mark.parametrize(
        "sub, sample_action",
        [
            ("automate", "enable"),
            ("check", "age"),
            ("repair", "delete-cache"),
        ],
    )
    def test_subcommand_without_action_prints_help(
        self, sub: str, sample_action: str, capsys: Any
    ) -> None:
        # No action -> print the subcommand's own help (stdout) and exit
        # USAGE, not argparse's terse "required" error.
        with (
            patch("sys.argv", ["borgadm", sub]),
            pytest.raises(SystemExit) as exc_info,
        ):
            ba.cli()
        assert exc_info.value.code == ba.ExitCode.USAGE
        out = capsys.readouterr().out
        assert f"usage: borgadm {sub}" in out
        # Full help lists the subcommand's available actions.
        assert sample_action in out

    def test_action_subcommand_still_dispatches(self) -> None:
        # With an action, cli dispatches to main rather than printing
        # help -- the _action_help default must not short-circuit it.
        with (
            patch("sys.argv", ["borgadm", "automate", "status"]),
            patch.object(ba, "main", autospec=True) as mock_main,
        ):
            result = ba.cli()
        assert result == ba.ExitCode.SUCCESS
        mock_main.assert_called_once()


class TestRepoRoot:
    """Test _repo_root() symlink resolution."""

    def test_repo_root_resolves_symlinked_launcher(
        self, tmp_path: Path
    ) -> None:
        """_repo_root() follows a symlinked launcher to the real repo.

        Mirrors the common install where ~/.local/bin/borgadm is a
        symlink to repo/bin/borgadm: without resolve(), repo_root
        would return ~/.local/ and the Applications/ tree wouldn't
        be found.
        """
        real_repo = tmp_path / "repo"
        (real_repo / "bin").mkdir(parents=True)
        real_script = real_repo / "bin" / "borgadm"
        real_script.write_text("#!/usr/bin/env python\n")

        launcher_root = tmp_path / "launcher"
        (launcher_root / "bin").mkdir(parents=True)
        launcher_script = launcher_root / "bin" / "borgadm"
        launcher_script.symlink_to(real_script)

        with patch.object(ba, "__file__", str(launcher_script)):
            assert ba._repo_root() == real_repo.resolve()


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
        # args.txt records the forwarded args; calls.txt appends the
        # disclaim marker once per invocation, so tests can count runs
        # (re-spawn must not loop) and confirm the disclaimed instance
        # is the one that reaches borgadm.
        mock_borgadm.write_text(
            "#!/bin/bash\n"
            'echo "$@" > "$(dirname "$0")/../args.txt"\n'
            'echo "${BORGADM_FDA_DISCLAIMED:-unset}" >> '
            '"$(dirname "$0")/../calls.txt"\n'
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

    def test_disclaim_respawn_runs_borgadm_once(
        self, wrapper_tree: tuple[Path, Path, Path]
    ) -> None:
        """Wrapper re-spawns itself once, disclaimed, before exec.

        The disclaimed instance carries the DISCLAIM marker, so the
        single borgadm invocation must see it set -- proving borgadm
        runs inside the re-spawn -- and the marker must break the loop,
        so borgadm runs exactly once.
        """
        binary, _, fake_home = wrapper_tree
        calls_file = binary.parent.parent.parent.parent.parent / "calls.txt"
        result = subprocess.run(
            [str(binary)],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(fake_home)},
        )
        assert result.returncode == 0, result.stderr
        runs = calls_file.read_text().splitlines()
        assert runs == ["1"], f"expected one disclaimed run, got {runs}"

    def test_disclaim_marker_skips_respawn(
        self, wrapper_tree: tuple[Path, Path, Path]
    ) -> None:
        """An already-disclaimed instance does not re-spawn again.

        With the marker pre-set (as the re-spawned instance sees it),
        the wrapper skips the re-spawn and execs borgadm directly --
        still exactly once.
        """
        binary, _, fake_home = wrapper_tree
        calls_file = binary.parent.parent.parent.parent.parent / "calls.txt"
        result = subprocess.run(
            [str(binary)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HOME": str(fake_home),
                "BORGADM_FDA_DISCLAIMED": "1",
            },
        )
        assert result.returncode == 0, result.stderr
        runs = calls_file.read_text().splitlines()
        assert runs == ["1"], f"expected one direct run, got {runs}"

    def test_forwards_termination_signal_to_borgadm(
        self, wrapper_tree: tuple[Path, Path, Path]
    ) -> None:
        """SIGTERM to the wrapper reaches borgadm through the re-spawn.

        A scheduler timeout sends a single-PID SIGTERM; borgadm now runs
        one process below this instance, so the wrapper must forward the
        signal or borgadm/borg would outlive the timeout. The mock traps
        SIGTERM and exits 42, so a forwarded signal shows up as both the
        marker file and the propagated exit code.
        """
        binary, mock_borgadm, fake_home = wrapper_tree
        tmp_root = mock_borgadm.parent.parent
        started = tmp_root / "borgadm_started"
        got_term = tmp_root / "borgadm_got_term"
        # Background the sleep and wait on it: a bare foreground sleep
        # would defer the trap until it finished, masking forwarding.
        mock_borgadm.write_text(
            "#!/bin/bash\n"
            f'trap \'echo term > "{got_term}"; kill "$SP" 2>/dev/null;'
            " exit 42' TERM\n"
            f'touch "{started}"\n'
            "sleep 30 & SP=$!\n"
            'wait "$SP"\n'
        )
        mock_borgadm.chmod(0o755)
        proc = subprocess.Popen(
            [str(binary)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "HOME": str(fake_home)},
        )
        try:
            deadline = time.monotonic() + 5
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert started.exists(), "mock borgadm never started"
            proc.terminate()
            rc = proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        assert got_term.exists(), "SIGTERM did not reach borgadm (orphaned)"
        assert rc == 42, f"wrapper did not propagate borgadm exit: {rc}"

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


@pytest.mark.e2e
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
        subprocess invocation path -- config parsing, env init -- works
        end-to-end)."""
        result = borg_e2e.run("list")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


class TestArchiveCompleteness:
    """Unit-level coverage of _archive_entries_are_complete and the
    _ArchiveName struct. The E2E classes above exercise the
    integration path; these tests cover edge cases that are hard to
    reach from real archives (M=0, duplicate Ns, parse failures, and
    the str() round-trip)."""

    _TS = "20260101_120000"

    def _entry(
        self,
        set_name: str,
        n: int,
        m: int,
        timestamp: str | None = None,
    ) -> Any:
        return ba._ArchiveName(
            backup_name="test",
            set_name=set_name,
            timestamp=timestamp or self._TS,
            n=n,
            m=m,
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

    def test_from_str_returns_none_for_unrecognized_shape(
        self,
    ) -> None:
        """A string that doesn't match the expected shape returns
        None rather than raising. list_backups uses this to silently
        skip non-borgadm archives in the same repo, and
        list_unknown_archives uses it to spot home-prefixed but
        malformed names."""
        assert ba._ArchiveName.from_str("test", "manual-archive") is None
        assert ba._ArchiveName.from_str("test", "other-set-a-no-stamp") is None
        # Suffix-less name fails: the NofM suffix is now required.
        assert (
            ba._ArchiveName.from_str("test", "test-set-a-20260101_120000")
            is None
        )

    def test_from_str_returns_none_for_other_backup_name(self) -> None:
        """An archive whose backup_name prefix doesn't match the
        configured BACKUP_NAME is rejected so two borgadm-managed
        repositories sharing a borg target stay isolated."""
        name = "other-set-a-20260101_120000_1of2"
        assert ba._ArchiveName.from_str("test", name) is None

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
        ascending N, then set_name. sorted() needs no key function."""
        a = self._entry("set-a", 1, 2, timestamp="20260101_120000")
        b = self._entry("set-b", 2, 2, timestamp="20260101_120000")
        c = self._entry("set-a", 1, 1, timestamp="20260102_120000")
        assert sorted([c, b, a]) == [a, b, c]


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


@pytest.mark.e2e
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
            borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
            borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        assert _list_borgadm(borg_e2e) == list(reversed(timestamps))

    def test_archive_names_outside_pattern_are_ignored(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Archives whose names don't share the BACKUP_NAME- prefix
        are not borgadm's to manage: filtered out of every list mode
        (default, --no-include-partial, --only-partial)."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        borg_e2e.make_archive("manual-backup-keep-this")
        assert _list_borgadm(borg_e2e) == [ts]
        assert _list_borgadm(borg_e2e, "--no-include-partial") == [ts]
        assert _list_borgadm(borg_e2e, "--only-partial") == []

    def test_foreign_prefix_archives_do_not_warn(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """The unknown-archive warning surface is gated on the
        BACKUP_NAME- prefix. A foreign-prefix archive triggers neither
        the warning nor the non-zero exit-status side effect."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        borg_e2e.make_archive("manual-backup-keep-this")
        result = borg_e2e.run("list")
        assert "manual-backup-keep-this" not in result.stderr
        assert "WARNING" not in result.stderr

    def test_list_warns_on_home_prefixed_unknown_archive(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """An archive that shares the BACKUP_NAME- prefix but does
        not parse as {BACKUP_NAME}-{set}-{ts}_NofM is surfaced as a
        WARNING on stderr, so a malformed leftover doesn't quietly
        hide in the repo."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        borg_e2e.make_archive("test-stale-leftover")
        result = borg_e2e.run("list")
        assert "test-stale-leftover" in result.stderr
        assert "WARNING" in result.stderr

    def test_list_surfaces_suffixless_archive_as_unknown(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """A suffix-less `{BACKUP_NAME}-set-ts` archive (the shape
        produced by pre-_NofM borgadm) now fails to parse and
        surfaces via the unknown-archive warning rather than being
        silently absorbed as a complete set."""
        ts = "20260101_120000"
        borg_e2e.make_archive(f"test-set-a-{ts}")
        result = borg_e2e.run("list")
        assert f"test-set-a-{ts}" in result.stderr
        assert "WARNING" in result.stderr
        assert _list_borgadm(borg_e2e) == []

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


@pytest.mark.e2e
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


@pytest.mark.e2e
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
            borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
            borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        borg_e2e.make_archive(_archive_name("set-a", "20260101_140000", 1, 2))
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

    def test_prune_warns_on_unknown_without_flag(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """Without --cleanup-unknown, prune surfaces the unknown
        archive as a stderr WARNING but leaves it in place. The
        partial sweep and retention stages still run as usual."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        borg_e2e.make_archive("test-stale-leftover")
        result = borg_e2e.run("prune")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "test-stale-leftover" in result.stderr
        assert "WARNING" in result.stderr
        assert "test-stale-leftover" in borg_e2e.archives()

    def test_prune_cleanup_unknown_dry_run_does_not_delete(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """--cleanup-unknown with --dry-run names the archive in
        the output but leaves the repo untouched."""
        ts = "20260101_120000"
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        borg_e2e.make_archive("test-stale-leftover")
        before = sorted(borg_e2e.archives())
        result = borg_e2e.run("prune", "--cleanup-unknown", "--dry-run")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "test-stale-leftover" in result.stdout
        assert sorted(borg_e2e.archives()) == before

    def test_prune_cleanup_unknown_deletes(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """--cleanup-unknown removes the unknown archive while leaving
        the full set in place."""
        ts = "20260101_120000"
        full_a = _archive_name("set-a", ts, 1, 2)
        full_b = _archive_name("set-b", ts, 2, 2)
        borg_e2e.make_archive(full_a)
        borg_e2e.make_archive(full_b)
        borg_e2e.make_archive("test-stale-leftover")
        result = borg_e2e.run("prune", "--cleanup-unknown")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert sorted(borg_e2e.archives()) == [full_a, full_b]

    def test_prune_cleanup_unknown_deletes_suffixless_archive(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """A suffix-less `{BACKUP_NAME}-set-ts` archive (the shape
        produced by pre-_NofM borgadm) is classified as unknown and
        --cleanup-unknown removes it. This is the upgrade-cleanup
        path for any leftover legacy archive that escaped a backfill
        rename."""
        ts = "20260101_120000"
        full_a = _archive_name("set-a", ts, 1, 2)
        full_b = _archive_name("set-b", ts, 2, 2)
        borg_e2e.make_archive(full_a)
        borg_e2e.make_archive(full_b)
        borg_e2e.make_archive(f"test-set-a-{ts}")
        result = borg_e2e.run("prune", "--cleanup-unknown")
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert sorted(borg_e2e.archives()) == [full_a, full_b]


@pytest.mark.e2e
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


@pytest.mark.e2e
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
            borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
            borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
        result = borg_e2e.run("delete", delete_ts)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert sorted(borg_e2e.archives()) == [
            _archive_name("set-a", keep_ts, 1, 2),
            _archive_name("set-b", keep_ts, 2, 2),
        ]

    def test_delete_by_timestamp_includes_partial_archives(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """A timestamp with only some configured sets present is still
        addressable by `borgadm delete TIMESTAMP` -- include_partial is
        on for the timestamp resolution path."""
        ts = "20260101_120000"
        partial = _archive_name("set-a", ts, 1, 2)  # partial: missing 2of2
        borg_e2e.make_archive(partial)
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
        archive_a = _archive_name("set-a", ts, 1, 2)
        archive_b = _archive_name("set-b", ts, 2, 2)
        borg_e2e.make_archive(archive_a)
        borg_e2e.make_archive(archive_b)
        result = borg_e2e.run("delete", archive_a)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert borg_e2e.archives() == [archive_b]

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
        borg_e2e.make_archive(_archive_name("set-a", ts, 1, 2))
        borg_e2e.make_archive(_archive_name("set-b", ts, 2, 2))
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


@pytest.mark.e2e
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
        list_backups, which is archive-driven, so an emptied
        BACKUP_SETS does not block deletion of archives in the repo."""
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


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
