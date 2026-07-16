#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "pytest",
#     "pytest-cov",
#     "pytest-xdist",
#     "tomlkit",
#     "pydantic>=2",
# ]
# ///
# This is human generated code that's been AI modified

"""
Unit tests for borgadm
"""

import argparse
import collections
import contextlib
import io
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest
from conftest import (
    CmdCallbacksBase,
    ExceptionHierarchyBase,
    HelpWidthBase,
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

sys.path.insert(0, str(REPO_ROOT / "src"))

import borgadm as ba  # noqa: E402
import crony.cli as crony_cli  # noqa: E402
import crony.unit  # noqa: E402

# The bin script under test, for run_tests' coverage module name and the
# e2e subprocess fixtures.
_script_path = REPO_ROOT / "bin" / "borgadm"


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

    def constructor(
        _config_path: str,
        args: dict[str, Any],
        require_backup_sets: bool = False,  # noqa: ARG001
    ) -> Any:
        # require_backup_sets mirrors the real Config signature so the
        # `config validate` path's keyword call binds; the mock ignores it.
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

    def test_repair_subcommand_parses(self) -> None:
        """Test repair delete-cache subcommand parses."""
        parser = ba.args_parser()
        args = parser.parse_command(["repair", "delete-cache"])
        assert args.command == "repair delete-cache"

    def test_repair_repo_yes_parses(self) -> None:
        """repair repo --yes parses; the validator consumes --yes."""
        parser = ba.args_parser()
        args = parser.parse_command(["repair", "repo", "--yes"])
        assert args.command == "repair repo"
        assert not hasattr(args, "yes")

    def test_repair_repo_no_yes_errors(self) -> None:
        """repair repo without --yes is rejected by the parser (exit 2)
        before any dispatch -- the confirmation gate lives there now."""
        parser = ba.args_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_command(["repair", "repo"])
        assert exc.value.code == 2

    @pytest.mark.parametrize("mode", ["repo", "archives", "full"])
    def test_repair_modes_parse(self, mode: str) -> None:
        """Each repair mode parses with --yes and consumes it."""
        parser = ba.args_parser()
        args = parser.parse_command(["repair", mode, "--yes"])
        assert args.command == f"repair {mode}"
        assert not hasattr(args, "yes")

    @pytest.mark.parametrize("mode", ["repo", "archives", "full"])
    def test_repair_modes_require_yes(self, mode: str) -> None:
        """Each repair mode requires --yes (exit 2 without it)."""
        parser = ba.args_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_command(["repair", mode])
        assert exc.value.code == 2

    @pytest.mark.parametrize("cmd", ["extract", "rsync"])
    def test_archive_option_defaults_to_none(self, cmd: str) -> None:
        """Without --archive, extract/rsync leave `archive` None so the
        handler falls back to the latest full backup set."""
        parser = ba.args_parser()
        args = parser.parse_command([cmd, "/target"])
        assert args.command == cmd
        assert args.archive is None

    @pytest.mark.parametrize("cmd", ["extract", "rsync"])
    def test_archive_option_captures_selector(self, cmd: str) -> None:
        """--archive carries its selector through to the handler."""
        parser = ba.args_parser()
        args = parser.parse_command(
            [cmd, "/target", "--archive", "20250101_120000"]
        )
        assert args.archive == "20250101_120000"

    def test_common_args_rejected_before_action(self) -> None:
        """Common args between subcommand and action should fail."""
        parser = ba.args_parser()
        cases = [
            ["check", "--verbose", "age"],
            ["check", "--config", "/tmp/c", "age"],
            ["automate", "--verbose", "apply"],
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
                ["automate", "apply", "--verbose"],
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

    def test_bypass_lock_defaults_off_and_opts_in(self) -> None:
        # Every read-only command respects the lock by default; only an
        # explicit --bypass-lock skips the wait. There is no negation
        # flag (--no-bypass-lock is not a valid option).
        parser = ba.args_parser()
        cmds = (
            ["extract", "/tmp/out"],
            ["rsync", "/tmp/out"],
            ["list"],
            ["check", "age"],
        )
        for cmd in cmds:
            assert parser.parse_args(cmd).bypass_lock is False, cmd
            assert parser.parse_args([*cmd, "--bypass-lock"]).bypass_lock
            # No negation flag on any read command, not just the two that
            # used to bypass by default.
            with pytest.raises(SystemExit) as exc:
                parser.parse_args([*cmd, "--no-bypass-lock"])
            assert exc.value.code == 2, cmd

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
        (["config", "init", "--bogus"], "config init"),
        (["config", "validate", "--bogus"], "config validate"),
    ]


class TestCmdCallbacks(CmdCallbacksBase):
    """Test COMMAND_CALLBACKS table."""

    CALLBACKS = ba.COMMAND_CALLBACKS
    PARSER_FUNC = ba.args_parser
    CLI_FUNC = staticmethod(ba.cli)
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
        (RuntimeError("t"), ba.ExitCode.CRASHED),
    ]
    POPPED_ARGS = {
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
        # Consumed by the repair-check parser validator:
        "yes",
    }
    # main() re-injects the --config path for the config subcommands (they
    # operate on the file itself); keep in sync with the dispatcher's
    # `if top_command == "config"` branch.
    INJECTED_GLOBALS = {
        "config init": {"config"},
        "config validate": {"config"},
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

    def _assert_repair_argv(
        self, mock_run_borg: Any, repo: str, expected_args: list[str]
    ) -> None:
        mock_run_borg.assert_called_once_with(
            ["borg", "check", "--repair", *expected_args, repo],
            repo_write=True,
            allow_output=True,
            env={
                **os.environ,
                "BORG_CHECK_I_KNOW_WHAT_I_AM_DOING": "YES",
            },
        )

    def test_repair_repo(self, mock_cfg: Any) -> None:
        """repair repo scopes the repair to --repository-only."""
        with (
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
        ):
            ba.do_repair_repo(progress=False)
        self._assert_repair_argv(
            mock_run_borg, mock_cfg.BORG_REPO, ["--repository-only"]
        )

    def test_repair_repo_progress(self, mock_cfg: Any) -> None:
        """repair repo passes --progress through to borg."""
        with (
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
        ):
            ba.do_repair_repo(progress=True)
        self._assert_repair_argv(
            mock_run_borg,
            mock_cfg.BORG_REPO,
            ["--repository-only", "--progress"],
        )

    def test_repair_archives(self, mock_cfg: Any) -> None:
        """repair archives scopes the repair to --archives-only."""
        with (
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
        ):
            ba.do_repair_archives(progress=False)
        self._assert_repair_argv(
            mock_run_borg, mock_cfg.BORG_REPO, ["--archives-only"]
        )

    def test_repair_full(self, mock_cfg: Any) -> None:
        """repair full repairs both phases (no --*-only flag)."""
        with (
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
        ):
            ba.do_repair_full(progress=False)
        self._assert_repair_argv(mock_run_borg, mock_cfg.BORG_REPO, [])


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
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_command(["delete", "--latest", "20250101_120000"])
        assert exc_info.value.code == 2

    def test_delete_no_args_errors(self) -> None:
        """Test that no archive and no --latest is a parser error."""
        parser = ba.args_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_command(["delete"])
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
            patch.object(ba, "run_borg", autospec=True) as mock_run,
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
                repo_write=True,
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
            patch.object(ba, "run_borg", autospec=True) as mock_run,
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
                repo_write=True,
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
            patch.object(ba, "run_borg", autospec=True) as mock_run,
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
                repo_write=True,
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
            patch.object(ba, "run_borg", autospec=True) as mock_run,
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
                repo_write=True,
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
            patch.object(ba, "run_borg", autospec=True) as mock_run,
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
                repo_write=True,
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
            patch.object(ba, "run_borg", autospec=True) as mock_run,
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
                repo_write=True,
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
            patch.object(ba, "run_borg", autospec=True) as mock_run,
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
                repo_write=True,
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
            latest: bool = False,
            partial: bool = False,
            **_kwargs: object,
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
                bypass_lock=kwargs.get("bypass_lock", False),
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
        "check-full",
    ]

    @staticmethod
    @contextlib.contextmanager
    def _automate_ctx(
        system: str, tmp_path: Path, monkeypatch: Any
    ) -> Iterator[tuple[Path, Any]]:
        """A given platform + a temp crony drop-in dir + mocked run_cmd
        (so crony is never really invoked).

        Resets the module warning flag (restored by monkeypatch at
        teardown) because `do_automate_status` sets it on a stale or
        missing bundle and it must not leak into later tests.
        """
        dropin = tmp_path / "crony-config"
        monkeypatch.setenv("CRONY_CONFIG_DROPIN_DIR", str(dropin))
        monkeypatch.setattr(ba, "_warning_occurred", False)
        with (
            patch.object(ba.platform, "system", return_value=system),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            yield dropin, mock_run

    @pytest.fixture
    def automate_env(
        self, tmp_path: Path, monkeypatch: Any
    ) -> Iterator[tuple[Path, Any]]:
        """Darwin automate environment (see _automate_ctx)."""
        with self._automate_ctx("Darwin", tmp_path, monkeypatch) as env:
            yield env

    @pytest.fixture
    def automate_env_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> Iterator[tuple[Path, Any]]:
        """Linux automate environment (see _automate_ctx)."""
        with self._automate_ctx("Linux", tmp_path, monkeypatch) as env:
            yield env

    @staticmethod
    def _crony_calls(mock_run: Any) -> list[list[str]]:
        """The crony argv (after the crony path) of each run_cmd call."""
        return [list(call.args[0][1:]) for call in mock_run.call_args_list]

    def test_apply_writes_bundle_and_runs_crony(
        self, automate_env: Any
    ) -> None:
        dropin, mock_run = automate_env
        ba.do_automate_apply(
            config_only=False,
            include=[],
            exclude=[],
            rsync_dir=None,
            rsync_interval=None,
        )
        bundle = dropin / "borgadm.toml"
        assert bundle.exists()
        text = bundle.read_text()
        for op in self.JOB_OPS:
            assert f"[job.{op}]" in text
        assert ["apply", "-b", "borgadm"] in self._crony_calls(mock_run)

    def test_destroy_removes_units_and_bundle(self, automate_env: Any) -> None:
        dropin, mock_run = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        ba.do_automate_destroy(config_only=False)
        assert ["destroy", "-b", "borgadm", "--all"] in self._crony_calls(
            mock_run
        )
        assert not bundle.exists()

    def test_destroy_is_noop_when_not_applied(self, automate_env: Any) -> None:
        # No bundle file: crony has nothing addressable and exits nonzero,
        # but destroy must treat that as a clean no-op, not an error.
        dropin, mock_run = automate_env
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="unknown bundle"
        )
        ba.do_automate_destroy(config_only=False)
        assert not (dropin / "borgadm.toml").exists()

    def test_destroy_surfaces_failure_when_bundle_present(
        self, automate_env: Any
    ) -> None:
        # The bundle is installed (file present) but destroy fails for a
        # real reason (a running job holds the lock); that must surface
        # rather than silently leaving the file in place.
        dropin, mock_run = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=7, stdout="", stderr="lock held"
        )
        with pytest.raises(ba.BorgadmError, match="crony destroy failed"):
            ba.do_automate_destroy(config_only=False)
        assert bundle.exists()

    def test_status_shells_out_to_crony(self, automate_env: Any) -> None:
        dropin, mock_run = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        ba.do_automate_status(config_only=False)
        assert ["status", "-b", "borgadm"] in self._crony_calls(mock_run)

    def test_apply_on_linux_omits_full_disk_access(
        self, automate_env_linux: Any
    ) -> None:
        # automate is cross-platform: on Linux apply still writes the
        # bundle and applies it, but the create job carries no
        # full-disk-access flag (a user systemd job already has the
        # access, so no Crony.app grant is needed).
        dropin, mock_run = automate_env_linux
        ba.do_automate_apply(
            config_only=False,
            include=[],
            exclude=[],
            rsync_dir=None,
            rsync_interval=None,
        )
        bundle = dropin / "borgadm.toml"
        assert bundle.exists()
        assert ["apply", "-b", "borgadm"] in self._crony_calls(mock_run)
        doc = tomllib.loads(bundle.read_text())
        assert "flags" not in doc["job"]["create"]

    @pytest.mark.parametrize(
        ("system", "expect_flag"),
        [("Darwin", True), ("Linux", False)],
    )
    def test_create_full_disk_access_gated_on_macos(
        self, system: str, expect_flag: bool, monkeypatch: Any
    ) -> None:
        # The create job reads protected user files, but only macOS gates
        # that behind Full Disk Access, so the full-disk-access flag is
        # rendered on Darwin and omitted everywhere else.
        monkeypatch.setattr(ba.platform, "system", lambda: system)
        doc = tomllib.loads(ba._render_crony_bundle())
        if expect_flag:
            assert doc["job"]["create"]["flags"] == ["full-disk-access"]
        else:
            assert "flags" not in doc["job"]["create"]

    @pytest.mark.parametrize(
        ("system", "expect_dialog"),
        [("Darwin", True), ("Linux", False)],
    )
    def test_dialog_popup_default_gated_on_macos(
        self, system: str, expect_dialog: bool, monkeypatch: Any
    ) -> None:
        # dialog-popup is a macOS desktop-dialog channel, undeliverable
        # on Linux, so the bundle default notify-channels carry it only
        # on Darwin; everywhere else the default is just ["default"].
        monkeypatch.setattr(ba.platform, "system", lambda: system)
        doc = tomllib.loads(ba._render_crony_bundle())
        channels = doc["defaults"]["notify-channels"]
        if expect_dialog:
            assert channels == ["default", "dialog-popup"]
        else:
            assert channels == ["default"]

    def test_deterministic_uuids(self) -> None:
        assert ba._crony_job_uuid("create") == ba._crony_job_uuid("create")
        assert ba._crony_job_uuid("create") != ba._crony_job_uuid("check-age")
        # canonical lowercase UUID form (crony requires it)
        parsed = uuid.UUID(ba._crony_job_uuid("create"))
        assert str(parsed) == ba._crony_job_uuid("create")

    def test_create_silenced_checks_inherit(self, monkeypatch: Any) -> None:
        # Pin to Darwin so the create job's macOS-only full-disk-access
        # flag is present for the assertion below.
        monkeypatch.setattr(ba.platform, "system", lambda: "Darwin")
        doc = tomllib.loads(ba._render_crony_bundle())
        # Bundle default: inherit the user's default channels AND pop a
        # desktop dialog on failure.
        assert doc["defaults"]["notify-channels"] == [
            "default",
            "dialog-popup",
        ]
        # create overrides to silent; the checks omit notify-channels,
        # inheriting the bundle default above.
        assert doc["job"]["create"]["notify-channels"] == []
        assert "notify-channels" not in doc["job"]["check-age"]
        # create treats borg's transient-warning exit 1 as success; the
        # checks keep the default (a check warning is a real signal).
        assert doc["job"]["create"]["success-exit-codes"] == [1]
        assert "success-exit-codes" not in doc["job"]["check-age"]
        # priority, the keep-awake flag, env, and the disabled wallclock
        # cap are the same for every job, so they live in [defaults] and
        # no job overrides them. (borgadm caps each borg command itself,
        # so job-timeout-sec = 0 leaves that timeout the sole authority.)
        assert doc["defaults"]["priority"] == "high"
        assert doc["defaults"]["flags"] == ["keep-awake"]
        assert doc["defaults"]["job-timeout-sec"] == 0
        assert doc["defaults"]["env"] == {"PATH": "$HOME/.local/bin:$PATH"}
        # Only create reads protected files, so it alone adds the
        # full-disk-access flag (composing with the inherited keep-awake);
        # the checks carry no own flags and inherit keep-awake.
        assert doc["job"]["create"]["flags"] == ["full-disk-access"]
        for op in self.JOB_OPS:
            assert "priority" not in doc["job"][op]
            assert "job-timeout-sec" not in doc["job"][op]
            assert "env" not in doc["job"][op]
            if op != "create":
                assert "flags" not in doc["job"][op]
        # The bundle deploys on any host that applies it.
        assert set(doc["target"]) == {"all"}
        assert doc["target"]["all"]["jobs"] == self.JOB_OPS

    def test_include_keeps_only_named_jobs(self, automate_env: Any) -> None:
        dropin, _mock_run = automate_env
        ba.do_automate_apply(
            config_only=True,
            include=["create", "check-age"],
            exclude=[],
            rsync_dir=None,
            rsync_interval=None,
        )
        text = (dropin / "borgadm.toml").read_text()
        doc = tomllib.loads(text)
        assert set(doc["job"]) == {"create", "check-age"}
        assert doc["target"]["all"]["jobs"] == ["create", "check-age"]
        # The marker records the selection sorted, for stable re-renders.
        assert "# include: check-age create" in text

    def test_exclude_drops_jobs_and_records_sorted_marker(
        self, automate_env: Any
    ) -> None:
        dropin, _mock_run = automate_env
        ba.do_automate_apply(
            config_only=True,
            include=[],
            exclude=["check-full", "check-age"],
            rsync_dir=None,
            rsync_interval=None,
        )
        text = (dropin / "borgadm.toml").read_text()
        doc = tomllib.loads(text)
        assert set(doc["job"]) == {"create", "check-prune"}
        assert "# exclude: check-age check-full" in text

    def test_marker_round_trips_through_render(self, automate_env: Any) -> None:
        # A rendered bundle's own marker must reproduce the identical
        # bundle -- the invariant status relies on to detect real drift
        # without flagging a deliberate selection.
        del automate_env
        for include_arg, exclude_arg in (
            (None, ["check-full"]),
            (["create", "check-prune"], []),
        ):
            text = ba._render_crony_bundle(
                include=include_arg, exclude=exclude_arg
            )
            sel = ba._selection_from(text)
            regenerated = ba._render_crony_bundle(
                include=sel.include,
                exclude=sel.exclude,
                rsync_dir=sel.rsync_dir,
                rsync_interval=sel.rsync_interval,
            )
            assert regenerated == text

    def test_bare_apply_reuses_recorded_selection(
        self, automate_env: Any
    ) -> None:
        dropin, _mock_run = automate_env
        ba.do_automate_apply(
            config_only=True,
            include=[],
            exclude=["check-full"],
            rsync_dir=None,
            rsync_interval=None,
        )
        first = (dropin / "borgadm.toml").read_text()
        ba.do_automate_apply(
            config_only=True,
            include=[],
            exclude=[],
            rsync_dir=None,
            rsync_interval=None,
        )
        assert (dropin / "borgadm.toml").read_text() == first

    @pytest.mark.parametrize(
        "argv",
        [
            ["automate", "apply", "--include", "bogus"],
            [
                "automate",
                "apply",
                "--include",
                "create",
                "--exclude",
                "check-age",
            ],
        ],
    )
    def test_apply_rejects_bad_selection_at_parse_time(
        self, argv: list[str]
    ) -> None:
        # An unknown job name and a mixed --include/--exclude are both
        # decidable from argv alone, so the parser rejects them (exit 2
        # with the usage line) before any dispatch.
        parser = ba.args_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_command(argv)
        assert exc.value.code == 2

    def test_unknown_job_error_lists_valid_names(self, capsys: Any) -> None:
        parser = ba.args_parser()
        with pytest.raises(SystemExit):
            parser.parse_command(["automate", "apply", "--include", "bogus"])
        err = capsys.readouterr().err
        assert "unknown job(s): bogus" in err
        for name in sorted(ba._CRONY_JOB_NAMES):
            assert name in err

    def test_marker_dedupes_repeated_names(self, automate_env: Any) -> None:
        dropin, _mock_run = automate_env
        ba.do_automate_apply(
            config_only=True,
            include=["create", "create"],
            exclude=[],
            rsync_dir=None,
            rsync_interval=None,
        )
        assert "# include: create\n" in (dropin / "borgadm.toml").read_text()

    def test_apply_config_only_skips_crony(self, automate_env: Any) -> None:
        # config-only must neither require nor invoke crony -- it works
        # even where crony is missing entirely.
        dropin, mock_run = automate_env
        with patch.object(
            ba,
            "_crony_path",
            autospec=True,
            side_effect=ba.BorgadmError("crony not found"),
        ):
            ba.do_automate_apply(
                config_only=True,
                include=[],
                exclude=[],
                rsync_dir=None,
                rsync_interval=None,
            )
        assert (dropin / "borgadm.toml").exists()
        mock_run.assert_not_called()

    def test_apply_leaves_no_bundle_when_crony_missing(
        self, automate_env: Any
    ) -> None:
        # crony resolves before the bundle is written, so a missing crony
        # fails cleanly instead of leaving an orphan bundle behind.
        dropin, _mock_run = automate_env
        with (
            patch.object(
                ba,
                "_crony_path",
                autospec=True,
                side_effect=ba.BorgadmError("crony not found"),
            ),
            pytest.raises(ba.BorgadmError, match="crony not found"),
        ):
            ba.do_automate_apply(
                config_only=False,
                include=[],
                exclude=[],
                rsync_dir=None,
                rsync_interval=None,
            )
        assert not (dropin / "borgadm.toml").exists()

    def test_status_up_to_date(self, automate_env: Any, caplog: Any) -> None:
        dropin, mock_run = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(ba._render_crony_bundle())
        with caplog.at_level(logging.INFO):
            ba.do_automate_status(config_only=False)
        assert not ba._warning_occurred
        assert any("up to date" in r.message for r in caplog.records)
        assert ["status", "-b", "borgadm"] in self._crony_calls(mock_run)

    def test_status_out_of_date_sets_warning(
        self, automate_env: Any, caplog: Any
    ) -> None:
        dropin, mock_run = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        with caplog.at_level(logging.WARNING):
            ba.do_automate_status(config_only=False)
        assert ba._warning_occurred
        assert any("out of date" in r.message for r in caplog.records)
        # The drift report does not preempt crony's deployed-state table.
        assert ["status", "-b", "borgadm"] in self._crony_calls(mock_run)

    def test_status_not_written_sets_warning(
        self, automate_env: Any, caplog: Any
    ) -> None:
        _dropin, _mock_run = automate_env
        with caplog.at_level(logging.WARNING):
            ba.do_automate_status(config_only=False)
        assert ba._warning_occurred
        assert any("not written yet" in r.message for r in caplog.records)

    def test_status_honors_exclude_marker(self, automate_env: Any) -> None:
        # A deliberately dropped job (recorded by the marker) is not
        # mistaken for drift.
        dropin, _mock_run = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(ba._render_crony_bundle(exclude=["check-full"]))
        ba.do_automate_status(config_only=False)
        assert not ba._warning_occurred

    def test_status_config_only_skips_crony(self, automate_env: Any) -> None:
        dropin, mock_run = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(ba._render_crony_bundle())
        with patch.object(
            ba,
            "_crony_path",
            autospec=True,
            side_effect=ba.BorgadmError("crony not found"),
        ):
            ba.do_automate_status(config_only=True)
        mock_run.assert_not_called()

    def test_destroy_config_only_unlinks_bundle_only(
        self, automate_env: Any
    ) -> None:
        dropin, mock_run = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        with patch.object(
            ba,
            "_crony_path",
            autospec=True,
            side_effect=ba.BorgadmError("crony not found"),
        ):
            ba.do_automate_destroy(config_only=True)
        assert not bundle.exists()
        mock_run.assert_not_called()

    def test_rsync_absent_from_default_and_exclude_renders(self) -> None:
        # rsync is non-default, so a bare render and an --exclude render
        # (which filters the defaults) never include it.
        for text in (
            ba._render_crony_bundle(),
            ba._render_crony_bundle(exclude=["check-full"]),
        ):
            doc = tomllib.loads(text)
            assert "rsync" not in doc["job"]

    def test_include_rsync_renders_job_with_dir_and_markers(
        self, automate_env: Any
    ) -> None:
        dropin, _mock_run = automate_env
        ba.do_automate_apply(
            config_only=True,
            include=["rsync"],
            exclude=[],
            rsync_dir=Path("/srv/my backups"),
            rsync_interval=None,
        )
        text = (dropin / "borgadm.toml").read_text()
        doc = tomllib.loads(text)
        assert set(doc["job"]) == {"rsync"}
        # Default interval is 1d; the space in the dir is shell-quoted.
        assert doc["job"]["rsync"]["interval"] == "1d"
        assert doc["job"]["rsync"]["command"] == (
            f"{shlex.quote(ba._borgadm_script_path())} rsync --delete "
            "--timestamp-messages '/srv/my backups'"
        )
        assert "# include: rsync" in text
        assert "# rsync-dir: /srv/my backups" in text
        assert "# rsync-interval: 1d" in text

    def test_rsync_interval_override_recorded(self, automate_env: Any) -> None:
        dropin, _mock_run = automate_env
        ba.do_automate_apply(
            config_only=True,
            include=["rsync"],
            exclude=[],
            rsync_dir=Path("/srv/verify"),
            rsync_interval=crony.unit.Interval.from_str("12h"),
        )
        text = (dropin / "borgadm.toml").read_text()
        doc = tomllib.loads(text)
        assert doc["job"]["rsync"]["interval"] == "12h"
        assert "# rsync-interval: 12h" in text

    def test_rsync_markers_round_trip(self) -> None:
        # A rendered rsync bundle's own markers must reproduce the
        # identical bundle, so status does not flag a custom dir/interval
        # as drift. The dir contains a space to exercise raw storage.
        text = ba._render_crony_bundle(
            include=["rsync"],
            rsync_dir=Path("/srv/my backups"),
            rsync_interval=crony.unit.Interval.from_str("12h"),
        )
        sel = ba._selection_from(text)
        assert sel.include == ["rsync"]
        assert sel.rsync_dir == Path("/srv/my backups")
        assert sel.rsync_interval == crony.unit.Interval.from_str("12h")
        regenerated = ba._render_crony_bundle(
            include=sel.include,
            exclude=sel.exclude,
            rsync_dir=sel.rsync_dir,
            rsync_interval=sel.rsync_interval,
        )
        assert regenerated == text

    def test_bare_apply_reuses_rsync_settings(self, automate_env: Any) -> None:
        dropin, _mock_run = automate_env
        ba.do_automate_apply(
            config_only=True,
            include=["rsync"],
            exclude=[],
            rsync_dir=Path("/srv/verify"),
            rsync_interval=crony.unit.Interval.from_str("12h"),
        )
        first = (dropin / "borgadm.toml").read_text()
        ba.do_automate_apply(
            config_only=True,
            include=[],
            exclude=[],
            rsync_dir=None,
            rsync_interval=None,
        )
        assert (dropin / "borgadm.toml").read_text() == first

    def test_status_up_to_date_with_rsync_markers(
        self, automate_env: Any
    ) -> None:
        dropin, _mock_run = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(
            ba._render_crony_bundle(
                include=["rsync"],
                rsync_dir=Path("/srv/verify"),
                rsync_interval=crony.unit.Interval.from_str("12h"),
            )
        )
        ba.do_automate_status(config_only=True)
        assert not ba._warning_occurred

    @pytest.mark.parametrize(
        "argv",
        [
            ["automate", "apply", "--include", "rsync"],
            ["automate", "apply", "--rsync-dir", "/x"],
            ["automate", "apply", "--rsync-interval", "12h"],
        ],
    )
    def test_apply_rejects_bad_rsync_options_at_parse_time(
        self, argv: list[str]
    ) -> None:
        # rsync is non-default and needs a dir, so selecting it without a
        # dir, or passing a rsync option without selecting rsync, is a
        # parse-time usage error (exit 2) before any dispatch.
        parser = ba.args_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_command(argv)
        assert exc.value.code == 2

    def test_apply_rejects_rsync_on_non_linux(self, monkeypatch: Any) -> None:
        # `borgadm rsync` is Linux only, so selecting the rsync job on
        # any other platform is a parse-time usage error rather than a
        # scheduled job that fails on every run.
        monkeypatch.setattr(ba.platform, "system", lambda: "Darwin")
        parser = ba.args_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_command(
                ["automate", "apply", "--include", "rsync", "--rsync-dir", "/x"]
            )
        assert exc.value.code == 2

    def test_apply_accepts_rsync_on_linux(self, monkeypatch: Any) -> None:
        # The Linux guard must not block the supported platform.
        monkeypatch.setattr(ba.platform, "system", lambda: "Linux")
        parser = ba.args_parser()
        args = parser.parse_command(
            ["automate", "apply", "--include", "rsync", "--rsync-dir", "/x"]
        )
        assert args.command == "automate apply"
        assert args.include == ["rsync"]
        assert args.rsync_dir == Path("/x")
        assert args.rsync_interval is None

    def test_apply_rejects_malformed_rsync_interval(
        self, monkeypatch: Any
    ) -> None:
        # --rsync-interval parses through crony's own Interval grammar, so
        # a malformed value is a parse-time usage error (exit 2) rather
        # than a bundle that only crony rejects at apply time.
        monkeypatch.setattr(ba.platform, "system", lambda: "Linux")
        parser = ba.args_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_command(
                [
                    "automate",
                    "apply",
                    "--include",
                    "rsync",
                    "--rsync-dir",
                    "/x",
                    "--rsync-interval",
                    "garbage",
                ]
            )
        assert exc.value.code == 2

    def test_apply_help_documents_jobs(self) -> None:
        # The apply --help epilog carries the job reference; every job
        # name and description must appear so --include/--exclude values
        # are discoverable from the help alone.
        parser = ba.args_parser()
        sub = next(
            a
            for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        automate = sub.choices["automate"]
        asub = next(
            a
            for a in automate._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        help_text = asub.choices["apply"].format_help()
        assert "Automation Jobs:" in help_text
        for job in ba._CRONY_JOBS:
            assert job.name in help_text
        # The rsync job's options are discoverable from the same help.
        assert "--rsync-dir" in help_text
        assert "--rsync-interval" in help_text

    @pytest.mark.parametrize(
        ("include", "exclude", "rsync_dir", "rsync_interval"),
        [
            (None, (), None, None),
            (None, ("check-full",), None, None),
            (("create",), (), None, None),
            (
                None,
                ("create", "check-age", "check-prune", "check-full"),
                None,
                None,
            ),
            (
                ("rsync",),
                (),
                Path("/srv/verify"),
                crony.unit.Interval.from_str("12h"),
            ),
            (("create", "rsync"), (), Path("/srv/verify"), None),
        ],
    )
    def test_generated_bundle_validates_against_crony(
        self,
        tmp_path: Path,
        include: tuple[str, ...] | None,
        exclude: tuple[str, ...],
        rsync_dir: Path | None,
        rsync_interval: crony.unit.Interval | None,
    ) -> None:
        # The bundle borgadm generates -- including ones carrying a
        # selection marker or the rsync job -- must be accepted by the
        # real crony's validator, so a future crony schema change that
        # breaks borgadm fails here rather than at install time.
        f = tmp_path / "borgadm.toml"
        f.write_text(
            ba._render_crony_bundle(
                include=include,
                exclude=exclude,
                rsync_dir=rsync_dir,
                rsync_interval=rsync_interval,
            )
        )
        crony = ba._crony_path()
        proc = subprocess.run(
            [crony, "config", "validate", "--file", str(f)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_emitted_crony_argv_parses_against_crony(
        self, automate_env: Any
    ) -> None:
        # Contract test: every crony invocation borgadm emits must be
        # accepted by crony's own parser (including its validate
        # callbacks, e.g. destroy's required target). borgadm shells out
        # to crony, so a change to crony's CLI contract otherwise breaks
        # automate silently at runtime with no failing test.
        dropin, mock_run = automate_env
        bundle = dropin / "borgadm.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(ba._render_crony_bundle())
        # Drive every handler that shells out to crony. do_logs
        # needs the bundle present, so destroy (which unlinks it) runs
        # last. A new crony shell-out added to borgadm must be driven
        # here (and its verb added below) or it goes unchecked.
        ba.do_automate_apply(
            config_only=False,
            include=[],
            exclude=[],
            rsync_dir=None,
            rsync_interval=None,
        )
        ba.do_automate_status(config_only=False)
        ba.do_logs()
        ba.do_automate_destroy(config_only=False)
        # _crony_calls drops the crony path, leaving the argv crony's
        # own parser sees.
        argvs = self._crony_calls(mock_run)
        verbs = {argv[0] for argv in argvs}
        assert verbs == {"apply", "status", "logs", "destroy"}
        for argv in argvs:
            # parse_command runs the verb's validate callback; a broken
            # contract exits 2 (SystemExit), failing the test.
            args = crony_cli._build_parser().parse_command(argv)
            assert args.command == argv[0]


class TestLogs:
    """Test the logs subcommand (crony-backed)."""

    @pytest.mark.parametrize("system", ["Darwin", "Linux"])
    def test_shows_default_logfile_only_without_bundle(
        self, system: str, monkeypatch: Any, caplog: Any
    ) -> None:
        monkeypatch.setattr(ba.platform, "system", lambda: system)
        with patch.object(
            ba,
            "_crony_bundle_path",
            autospec=True,
            return_value=Path("/nonexistent/borgadm.toml"),
        ):
            with caplog.at_level(logging.INFO):
                ba.do_logs()
        messages = [r.message for r in caplog.records]
        assert messages == [str(ba.LOGFILE)]

    @pytest.mark.parametrize("system", ["Darwin", "Linux"])
    def test_shows_existing_selected_job_logs(
        self, system: str, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # A bare bundle deploys the default jobs; each job's run-log shows
        # only when the file exists (a job that has not run is skipped).
        monkeypatch.setattr(ba.platform, "system", lambda: system)
        bundle = tmp_path / "borgadm.toml"
        bundle.write_text(ba._render_crony_bundle())
        never_ran = ba._DEFAULT_JOBS[0].name
        log_of = {j.name: tmp_path / f"{j.name}.log" for j in ba._DEFAULT_JOBS}
        for name, path in log_of.items():
            if name != never_ran:
                path.write_text("log\n")
        queried: list[str] = []

        def fake_run(
            cmd: list[str], *_args: object, **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            # cmd == [crony, "logs", "borgadm.<job>", "-p"]; crony prints a
            # structural path whether or not the file exists yet.
            job = cmd[2].split(".", 1)[1]
            queried.append(job)
            return subprocess.CompletedProcess(cmd, 0, f"{log_of[job]}\n", "")

        with (
            patch.object(
                ba, "_crony_bundle_path", autospec=True, return_value=bundle
            ),
            patch.object(ba, "run_cmd", autospec=True, side_effect=fake_run),
        ):
            with caplog.at_level(logging.INFO):
                ba.do_logs()
        messages = [r.message for r in caplog.records]
        assert messages[0] == str(ba.LOGFILE)
        # Only the bundle's jobs are queried (rsync is not in a bare bundle).
        assert set(queried) == {j.name for j in ba._DEFAULT_JOBS}
        assert str(log_of[never_ran]) not in messages
        for name, path in log_of.items():
            if name != never_ran:
                assert str(path) in messages

    def test_queries_only_bundle_selected_jobs(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # A host may install a subset (here `--include rsync`). Jobs absent
        # from the bundle must not be queried -- otherwise crony errors for
        # each with "no applied state" and borgadm logs the noise.
        monkeypatch.setattr(ba.platform, "system", lambda: "Linux")
        bundle = tmp_path / "borgadm.toml"
        bundle.write_text(
            ba._render_crony_bundle(
                include=["rsync"], rsync_dir=Path("/backups/verify")
            )
        )
        rsync_log = tmp_path / "rsync.log"
        rsync_log.write_text("log\n")
        queried: list[str] = []

        def fake_run(
            cmd: list[str], *_args: object, **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            queried.append(cmd[2].split(".", 1)[1])
            return subprocess.CompletedProcess(cmd, 0, f"{rsync_log}\n", "")

        with (
            patch.object(
                ba, "_crony_bundle_path", autospec=True, return_value=bundle
            ),
            patch.object(ba, "run_cmd", autospec=True, side_effect=fake_run),
        ):
            with caplog.at_level(logging.INFO):
                ba.do_logs()
        assert queried == ["rsync"]
        assert str(rsync_log) in [r.message for r in caplog.records]

    def test_unapplied_bundle_yields_no_crony_paths(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # Bundle written but never applied: crony resolves a structural
        # path but no log file exists there, so only borgadm's own log
        # shows -- no path to a missing file.
        monkeypatch.setattr(ba.platform, "system", lambda: "Linux")
        bundle = tmp_path / "borgadm.toml"
        bundle.write_text(ba._render_crony_bundle())

        def fake_run(
            cmd: list[str], *_args: object, **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            job = cmd[2].split(".", 1)[1]
            return subprocess.CompletedProcess(
                cmd, 0, f"{tmp_path}/unapplied/{job}.log\n", ""
            )

        with (
            patch.object(
                ba, "_crony_bundle_path", autospec=True, return_value=bundle
            ),
            patch.object(ba, "run_cmd", autospec=True, side_effect=fake_run),
        ):
            with caplog.at_level(logging.INFO):
                ba.do_logs()
        assert [r.message for r in caplog.records] == [str(ba.LOGFILE)]


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
    def test_check_age_no_backups(self) -> None:
        """Test check age raises CheckNoBackupsError."""
        with (
            patch.object(ba, "list_backups", autospec=True, return_value={}),
            pytest.raises(ba.CheckNoBackupsError),
        ):
            ba.do_check_age(bypass_lock=False)

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
            ba.do_check_age(bypass_lock=False)

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
            ba.do_check_age(bypass_lock=False)  # Should not raise

    @pytest.mark.usefixtures("mock_cfg")
    def test_check_archives_latest_no_backups(self) -> None:
        """check archives --latest raises when no full backups exist."""
        with (
            patch.object(ba, "list_backups", autospec=True, return_value={}),
            pytest.raises(ba.CheckNoBackupsError),
        ):
            ba.do_check_archives(
                progress=False, latest=True, archive=[], bypass_lock=False
            )

    def test_check_repo_argv(self, mock_cfg: Any) -> None:
        """check repo runs borg check --repository-only."""
        with (
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
        ):
            ba.do_check_repo(progress=False, bypass_lock=False)
        mock_run_borg.assert_called_once_with(
            ["borg", "check", "--repository-only", mock_cfg.BORG_REPO],
            repo_write=False,
            bypass_lock=False,
            allow_output=False,
        )

    def test_check_full_argv(self, mock_cfg: Any) -> None:
        """check full runs borg check with no --*-only flag."""
        with (
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
        ):
            ba.do_check_full(progress=True, bypass_lock=False)
        mock_run_borg.assert_called_once_with(
            ["borg", "check", "--progress", mock_cfg.BORG_REPO],
            repo_write=False,
            bypass_lock=False,
            allow_output=True,
        )

    def test_check_full_failure_raises(self, mock_cfg: Any) -> None:
        """A failing borg check surfaces as CheckFullError."""
        err = ba.SubprocessError(2, ["borg", "check", mock_cfg.BORG_REPO])
        with (
            patch.object(ba, "run_borg", autospec=True, side_effect=err),
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            pytest.raises(ba.CheckFullError),
        ):
            ba.do_check_full(progress=False, bypass_lock=False)

    def test_check_archives_whole_repo_argv(self, mock_cfg: Any) -> None:
        """check archives with no target runs --archives-only."""
        with (
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
        ):
            ba.do_check_archives(
                progress=False, latest=False, archive=[], bypass_lock=False
            )
        mock_run_borg.assert_called_once_with(
            ["borg", "check", "--archives-only", mock_cfg.BORG_REPO],
            repo_write=False,
            bypass_lock=False,
            allow_output=False,
        )

    def test_check_archives_latest_checks_latest_set(
        self, mock_cfg: Any
    ) -> None:
        """check archives --latest checks each archive in the newest
        full set by name, skipping partials via list_backups."""
        repo = mock_cfg.BORG_REPO
        latest = {
            "20250101_120000": [
                f"{repo}::home-fuse-20250101_120000",
                f"{repo}::home-local-20250101_120000",
            ]
        }
        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                return_value=latest,
            ) as mock_list,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
        ):
            ba.do_check_archives(
                progress=False, latest=True, archive=[], bypass_lock=False
            )
        mock_list.assert_called_once_with(latest=True, bypass_lock=False)
        assert [c.args[0] for c in mock_run_borg.call_args_list] == [
            ["borg", "check", f"{repo}::home-fuse-20250101_120000"],
            ["borg", "check", f"{repo}::home-local-20250101_120000"],
        ]

    def test_check_archives_by_name(self, mock_cfg: Any) -> None:
        """check archives <names> verifies existence then checks each."""
        repo = mock_cfg.BORG_REPO
        raw = Mock()
        raw.stdout = "home-fuse-20250101_120000\nhome-local-20250101_120000\n"
        with (
            patch.object(
                ba, "list_backups_raw", autospec=True, return_value=raw
            ),
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
        ):
            ba.do_check_archives(
                progress=False,
                latest=False,
                archive=["home-fuse-20250101_120000"],
                bypass_lock=False,
            )
        mock_run_borg.assert_called_once_with(
            ["borg", "check", f"{repo}::home-fuse-20250101_120000"],
            repo_write=False,
            bypass_lock=False,
            allow_output=False,
        )

    def test_check_archives_by_timestamp(self, mock_cfg: Any) -> None:
        """check archives <timestamp> expands to every archive at that
        time and checks each, the same way delete does."""
        repo = mock_cfg.BORG_REPO

        def list_backups_side_effect(
            partial: bool = False, **_kwargs: object
        ) -> dict[str, list[str]]:
            if partial:
                return {}
            return {
                "20250101_120000": [
                    f"{repo}::home-fuse-20250101_120000",
                    f"{repo}::home-local-20250101_120000",
                ]
            }

        with (
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                side_effect=list_backups_side_effect,
            ),
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
        ):
            ba.do_check_archives(
                progress=False,
                latest=False,
                archive=["20250101_120000"],
                bypass_lock=False,
            )
        checked = {c.args[0][-1] for c in mock_run_borg.call_args_list}
        assert checked == {
            f"{repo}::home-fuse-20250101_120000",
            f"{repo}::home-local-20250101_120000",
        }

    @pytest.mark.usefixtures("mock_cfg")
    def test_check_archives_timestamp_not_found(self) -> None:
        """An unknown timestamp fails before any borg check runs."""
        with (
            patch.object(ba, "list_backups", autospec=True, return_value={}),
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
            pytest.raises(ba.BorgadmError, match="timestamp"),
        ):
            ba.do_check_archives(
                progress=False,
                latest=False,
                archive=["20250101_120000"],
                bypass_lock=False,
            )
        mock_run_borg.assert_not_called()

    @pytest.mark.usefixtures("mock_cfg")
    def test_check_archives_unknown_name_errors(self) -> None:
        """An unknown archive name fails before any borg check runs."""
        raw = Mock()
        raw.stdout = "home-fuse-20250101_120000\n"
        with (
            patch.object(
                ba, "list_backups_raw", autospec=True, return_value=raw
            ),
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
            pytest.raises(ba.BorgadmError),
        ):
            ba.do_check_archives(
                progress=False,
                latest=False,
                archive=["nope"],
                bypass_lock=False,
            )
        mock_run_borg.assert_not_called()

    def test_check_archives_latest_and_names_parse_error(self) -> None:
        """--latest with archive name(s) is a parser error."""
        parser = ba.args_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_command(
                ["check", "archives", "--latest", "home-fuse-20250101_120000"]
            )
        assert exc_info.value.code == 2

    def test_check_archives_no_args_parses(self) -> None:
        """check archives with neither target nor --latest is valid."""
        parser = ba.args_parser()
        args = parser.parse_command(["check", "archives"])
        assert args.latest is False
        assert args.archive == []

    @pytest.mark.parametrize("mode", ["repo", "archives", "full"])
    def test_check_modes_parse(self, mode: str) -> None:
        """Each check mode parses to its command path."""
        parser = ba.args_parser()
        args = parser.parse_command(["check", mode])
        assert args.command == f"check {mode}"

    @pytest.mark.usefixtures("mock_cfg")
    def test_check_prune_partial_archives(self) -> None:
        """Test check prune fails on partial archives."""

        def list_backups_side_effect(
            partial: bool = False,
            **_kwargs: object,
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
            ba.do_check_prune(bypass_lock=False)

    def test_check_prune_unpruned_backups(self, mock_cfg: Any) -> None:
        """Test check prune fails when old backups need pruning."""
        mock_cfg.PRUNE_KEEP_HOURLY = 1
        mock_cfg.PRUNE_KEEP_DAILY = 0
        mock_cfg.PRUNE_KEEP_WEEKLY = 0
        mock_cfg.PRUNE_KEEP_MONTHLY = 0
        mock_cfg.PRUNE_KEEP_YEARLY = 0

        def list_backups_side_effect(
            partial: bool = False,
            **_kwargs: object,
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
            ba.do_check_prune(bypass_lock=False)

    def test_check_prune_ok(self, mock_cfg: Any) -> None:
        """Test check prune succeeds when no pruning needed."""
        mock_cfg.PRUNE_KEEP_HOURLY = 24
        mock_cfg.PRUNE_KEEP_DAILY = 7
        mock_cfg.PRUNE_KEEP_WEEKLY = 4
        mock_cfg.PRUNE_KEEP_MONTHLY = 12
        mock_cfg.PRUNE_KEEP_YEARLY = 2

        def list_backups_side_effect(
            partial: bool = False,
            **_kwargs: object,
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
            ba.do_check_prune(bypass_lock=False)  # Should not raise


@pytest.mark.usefixtures("mock_cfg")
class TestSelectedBackup:
    """_selected_backup resolves which backup extract/rsync operate on:
    the latest full set by default, else a name or a YYYYMMDD_HHMMSS
    timestamp (via the shared _resolve_archives)."""

    def test_none_resolves_latest_full_set(self) -> None:
        """No selector returns the latest full backup set and its
        timestamp."""
        latest = {
            "20250102_120000": [
                "foobar::home-set1-20250102_120000_1of1",
            ]
        }
        with patch.object(
            ba, "list_backups", autospec=True, return_value=latest
        ) as mock_list:
            ts, archives = ba._selected_backup(None, bypass_lock=False)
        assert ts == "20250102_120000"
        assert archives == latest["20250102_120000"]
        mock_list.assert_called_once_with(latest=True, bypass_lock=False)

    def test_none_on_empty_repo_errors(self) -> None:
        """No selector against a repo with no full backups fails."""
        with (
            patch.object(ba, "list_backups", autospec=True, return_value={}),
            pytest.raises(ba.BorgadmError, match="No full backups"),
        ):
            ba._selected_backup(None, bypass_lock=False)

    def test_timestamp_selects_that_set(self) -> None:
        """A timestamp selector resolves to every archive at that time,
        returning that shared timestamp."""

        def list_backups_side_effect(
            partial: bool = False, **_kwargs: object
        ) -> dict[str, list[str]]:
            if partial:
                return {}
            return {
                "20250101_120000": [
                    "foobar::home-set1-20250101_120000_1of2",
                    "foobar::home-set2-20250101_120000_2of2",
                ]
            }

        with patch.object(
            ba,
            "list_backups",
            autospec=True,
            side_effect=list_backups_side_effect,
        ):
            ts, archives = ba._selected_backup(
                "20250101_120000", bypass_lock=False
            )
        assert ts == "20250101_120000"
        assert archives == [
            "foobar::home-set1-20250101_120000_1of2",
            "foobar::home-set2-20250101_120000_2of2",
        ]

    def test_archive_name_selects_single_archive(self) -> None:
        """A full archive name resolves to just that archive, with the
        timestamp parsed from its name."""
        raw = Mock()
        raw.stdout = (
            "home-set1-20250101_120000_1of2\nhome-set2-20250101_120000_2of2\n"
        )
        with patch.object(
            ba, "list_backups_raw", autospec=True, return_value=raw
        ):
            ts, archives = ba._selected_backup(
                "home-set1-20250101_120000_1of2", bypass_lock=False
            )
        assert ts == "20250101_120000"
        assert archives == ["foobar::home-set1-20250101_120000_1of2"]

    def test_foreign_name_is_rejected(self) -> None:
        """A name that exists but is not a borgadm archive (no derivable
        timestamp) is rejected -- extract/rsync reconstruct a
        borgadm-managed backup."""
        raw = Mock()
        raw.stdout = "someone-elses-archive\n"
        with (
            patch.object(
                ba, "list_backups_raw", autospec=True, return_value=raw
            ),
            pytest.raises(ba.BorgadmError, match="borgadm-managed"),
        ):
            ba._selected_backup("someone-elses-archive", bypass_lock=False)


# borg 1.4's LockTimeout stderr, verified against a real held lock.
_LOCK_TIMEOUT_STDERR = (
    "Failed to create/acquire the lock /repo/lock.exclusive (timeout).\n"
)


class TestIsLockTimeout:
    """Test the borg LockTimeout stderr classifier."""

    def test_matches_real_lock_timeout(self) -> None:
        assert ba._is_lock_timeout(_LOCK_TIMEOUT_STDERR) is True

    def test_case_insensitive(self) -> None:
        assert ba._is_lock_timeout(_LOCK_TIMEOUT_STDERR.upper()) is True

    def test_non_lock_errors_are_not_lock_timeout(self) -> None:
        assert ba._is_lock_timeout("Connection closed by remote host") is False
        assert ba._is_lock_timeout("Repository has no manifest.") is False
        assert ba._is_lock_timeout("") is False


class TestBorgLocksHeld:
    """Test the lock-held probe."""

    def _probe(self, returncode: int, stderr: str) -> tuple[bool, Any]:
        proc = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout="", stderr=stderr
        )
        with (
            patch.object(
                ba, "run_cmd", autospec=True, return_value=proc
            ) as mock_run,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
        ):
            result = ba.borg_locks_held()
        return result, mock_run

    def test_free_lock_returns_false(self, mock_cfg: Any) -> None:
        result, mock_run = self._probe(0, "")
        assert result is False
        # The probe waits only LOCK_CHECK_TIMEOUT and never logs its own
        # (expected) failure as an error.
        mock_run.assert_called_once_with(
            [
                "borg",
                "list",
                "--short",
                "--lock-wait",
                str(mock_cfg.LOCK_CHECK_TIMEOUT),
                mock_cfg.BORG_REPO,
            ],
            errok=True,
            log_error=False,
            track_warning=False,
        )

    @pytest.mark.usefixtures("mock_cfg")
    def test_held_lock_returns_true(self) -> None:
        result, _ = self._probe(2, _LOCK_TIMEOUT_STDERR)
        assert result is True

    @pytest.mark.usefixtures("mock_cfg")
    def test_non_lock_failure_returns_false(self) -> None:
        # A real failure (network, etc.) is reported as not-held so the
        # blocking operation surfaces it rather than the probe masking it.
        result, _ = self._probe(2, "Connection closed by remote host")
        assert result is False


class TestRunBorg:
    """Test the lock-aware borg runner."""

    def _assert_throwaway_cache_env(self, kwargs: Mapping[str, Any]) -> None:
        """The bypass path points borg's cache and security dirs at a
        shared throwaway location so the read contends on neither the
        repository nor the cache lock. The dirs sit under one
        `borgadm-bypass-` parent and inherit the ambient environment.
        The parent is removed once run_borg returns."""
        env = kwargs["env"]
        assert env["PATH"] == os.environ["PATH"]
        cache = Path(env["BORG_CACHE_DIR"])
        security = Path(env["BORG_SECURITY_DIR"])
        assert cache.name == "cache"
        assert security.name == "security"
        assert cache.parent == security.parent
        assert cache.parent.name.startswith("borgadm-bypass-")
        assert not cache.parent.exists()

    def test_bypass_inserts_bypass_lock_after_verb(self, mock_cfg: Any) -> None:
        with (
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(ba, "borg_locks_held", autospec=True) as mock_probe,
        ):
            ba.run_borg(
                ["borg", "list", mock_cfg.BORG_REPO],
                repo_write=False,
                bypass_lock=True,
            )
        mock_probe.assert_not_called()
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == ["borg", "list", "--bypass-lock", mock_cfg.BORG_REPO]
        self._assert_throwaway_cache_env(kwargs)

    def test_bypass_position_with_multitoken_borg_cmd(
        self, mock_cfg: Any
    ) -> None:
        launcher = ["uvx", "-q", "--from", "borgbackup", "borg"]
        with (
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(ba, "borg_cmd", autospec=True, return_value=launcher),
            patch.object(
                ba, "borg_locks_held", autospec=True, return_value=False
            ),
        ):
            ba.run_borg(
                [*launcher, "list", mock_cfg.BORG_REPO],
                repo_write=False,
                bypass_lock=True,
            )
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == [
            *launcher,
            "list",
            "--bypass-lock",
            mock_cfg.BORG_REPO,
        ]
        self._assert_throwaway_cache_env(kwargs)

    def test_bypass_sudo_clears_root_owned_dirs(self, mock_cfg: Any) -> None:
        """A sudo'd borg creates the throwaway cache/security dirs
        root-owned, so the bypass path clears them with sudo before the
        unprivileged tempdir cleanup runs."""
        with (
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
        ):
            ba.run_borg(
                ["borg", "mount", mock_cfg.BORG_REPO, "/mnt"],
                repo_write=False,
                bypass_lock=True,
                sudo=True,
            )
        assert mock_run.call_count == 2
        borg_call, rm_call = mock_run.call_args_list
        assert borg_call.kwargs["sudo"] is True
        env = borg_call.kwargs["env"]
        assert rm_call.args[0] == [
            "rm",
            "-rf",
            env["BORG_CACHE_DIR"],
            env["BORG_SECURITY_DIR"],
        ]
        assert rm_call.kwargs == {"sudo": True, "errok": True}
        self._assert_throwaway_cache_env(borg_call.kwargs)

    def test_bypass_sudo_clears_dirs_when_borg_fails(
        self, mock_cfg: Any
    ) -> None:
        """The sudo cleanup runs even when the borg command raises."""
        err = ba.SubprocessError(2, ["borg"], output="", stderr="boom")
        with (
            patch.object(
                ba, "run_cmd", autospec=True, side_effect=[err, None]
            ) as mock_run,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            pytest.raises(ba.SubprocessError),
        ):
            ba.run_borg(
                ["borg", "mount", mock_cfg.BORG_REPO, "/mnt"],
                repo_write=False,
                bypass_lock=True,
                sudo=True,
            )
        assert mock_run.call_count == 2
        rm_call = mock_run.call_args_list[1]
        assert rm_call.args[0][:2] == ["rm", "-rf"]
        assert rm_call.kwargs == {"sudo": True, "errok": True}

    def test_blocking_free_lock_no_message(
        self, mock_cfg: Any, caplog: Any
    ) -> None:
        caplog.set_level(logging.INFO)
        with (
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(
                ba, "borg_locks_held", autospec=True, return_value=False
            ),
        ):
            ba.run_borg(["borg", "list", mock_cfg.BORG_REPO], repo_write=False)
        mock_run.assert_called_once_with(
            [
                "borg",
                "list",
                "--lock-wait",
                str(mock_cfg.BORG_CMD_TIMEOUT),
                mock_cfg.BORG_REPO,
            ]
        )
        assert "lock is held" not in caplog.text.lower()

    def test_blocking_held_lock_logs_waiting_message(
        self, mock_cfg: Any, caplog: Any
    ) -> None:
        caplog.set_level(logging.INFO)
        with (
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(
                ba, "borg_locks_held", autospec=True, return_value=True
            ),
        ):
            ba.run_borg(["borg", "create", mock_cfg.BORG_REPO], repo_write=True)
        assert "A borg lock is held" in caplog.text
        mock_run.assert_called_once_with(
            [
                "borg",
                "create",
                "--lock-wait",
                str(mock_cfg.BORG_CMD_TIMEOUT),
                mock_cfg.BORG_REPO,
            ]
        )

    @pytest.mark.usefixtures("mock_cfg")
    def test_repo_write_cannot_bypass_lock(self) -> None:
        with pytest.raises(AssertionError):
            ba.run_borg(
                ["borg", "create", "repo"],
                repo_write=True,
                bypass_lock=True,
            )

    def test_kwargs_pass_through(self, mock_cfg: Any) -> None:
        with (
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(
                ba, "borg_locks_held", autospec=True, return_value=False
            ),
        ):
            ba.run_borg(
                ["borg", "extract", "repo::a"],
                repo_write=False,
                cwd=mock_cfg.BACKUP_ROOT,
                allow_output=True,
            )
        _, kwargs = mock_run.call_args
        assert kwargs["cwd"] == mock_cfg.BACKUP_ROOT
        assert kwargs["allow_output"] is True


class TestRunCmdLogError:
    """run_cmd's log_error / track_warning flags keep an advisory call
    (the lock probe) from logging or affecting borgadm's exit status."""

    @pytest.mark.usefixtures("mock_cfg")
    def test_log_error_false_suppresses_error_log(self, caplog: Any) -> None:
        caplog.set_level(logging.ERROR)
        ba.run_cmd(["false"], errok=True, log_error=False)
        assert caplog.text == ""

    @pytest.mark.usefixtures("mock_cfg")
    def test_log_error_default_logs_failure(self, caplog: Any) -> None:
        caplog.set_level(logging.ERROR)
        ba.run_cmd(["false"], errok=True)
        assert "failed with exit code" in caplog.text

    @pytest.mark.usefixtures("mock_cfg")
    def test_track_warning_false_does_not_flip_global_warning(self) -> None:
        # `false borg` exits 1 and is classified as a borg command (the
        # is_borg heuristic keys off "borg" anywhere in argv), so it
        # exercises the WARNING branch without a real borg repo.
        with patch.object(ba, "_warning_occurred", False):
            ba.run_cmd(["false", "borg"], errok=True, track_warning=False)
            assert ba._warning_occurred is False

    @pytest.mark.usefixtures("mock_cfg")
    def test_track_warning_default_flips_global_warning(self) -> None:
        with patch.object(ba, "_warning_occurred", False):
            ba.run_cmd(["false", "borg"], errok=True)
            assert ba._warning_occurred is True


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

    def test_borg_rsh_sets_ssh_keepalive(self) -> None:
        """Remote BORG_RSH carries ServerAlive keepalive so a long
        FUSE-backed rsync's ssh session survives idle gaps."""
        with self._patch_externals("user@host:/srv/borg"):
            ba.initialize_borg_environment()
        rsh = os.environ["BORG_RSH"]
        assert "-o ServerAliveInterval=15" in rsh
        assert "-o ServerAliveCountMax=30" in rsh


class TestRsyncMountCleanup:
    """Mount discovery, unmount ordering, and stale-run reclamation."""

    _MOUNT_OUTPUT = (
        "sysfs on /sys type sysfs (rw,nosuid,nodev)\n"
        "borgfs on /t/.borgadm_borg-rsync.tmp.aaa/home-fuse-1of2"
        " type fuse (ro,allow_other)\n"
        "borgfs on /t/.borgadm_borg-rsync.tmp.aaa/home-local-2of2"
        " type fuse (ro,allow_other)\n"
        "overlay on /t/.borgadm_borg-rsync.tmp.aaa/merged"
        " type overlay (rw,relatime)\n"
        "tmpfs on /run type tmpfs (rw)\n"
    )

    def test_mounts_under_filters_to_subtree(self) -> None:
        """Only mounts at or under the run dir are returned; unrelated
        system mounts are ignored."""
        with patch.object(
            ba.subprocess,
            "check_output",
            autospec=True,
            return_value=self._MOUNT_OUTPUT,
        ):
            found = ba._mounts_under(Path("/t/.borgadm_borg-rsync.tmp.aaa"))
        assert [(p.as_posix(), fs) for p, fs in found] == [
            ("/t/.borgadm_borg-rsync.tmp.aaa/home-fuse-1of2", "fuse"),
            ("/t/.borgadm_borg-rsync.tmp.aaa/home-local-2of2", "fuse"),
            ("/t/.borgadm_borg-rsync.tmp.aaa/merged", "overlay"),
        ]

    @pytest.mark.parametrize(
        "err",
        [
            FileNotFoundError("mount"),
            subprocess.CalledProcessError(1, ["mount"]),
        ],
    )
    def test_mounts_under_empty_when_mount_unusable(
        self, err: Exception
    ) -> None:
        """An unusable `mount` binary yields no mounts instead of an
        exception -- the helper runs as part of cleanup, where raising
        would mask the run's real error."""
        with patch.object(
            ba.subprocess, "check_output", autospec=True, side_effect=err
        ):
            assert ba._mounts_under(Path("/t/.borgadm_x.tmp.aaa")) == []

    def test_umount_tree_unmounts_overlay_first(self) -> None:
        """The overlay 'merged' mount must be torn down before the fuse
        mounts it stacks on."""
        with (
            patch.object(
                ba.subprocess,
                "check_output",
                autospec=True,
                return_value=self._MOUNT_OUTPUT,
            ),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
        ):
            ba._umount_tree(Path("/t/.borgadm_borg-rsync.tmp.aaa"))
        umounted = [c.args[0][2] for c in mock_run.call_args_list]
        assert len(umounted) == 3
        assert umounted[0].endswith("/merged")
        for c in mock_run.call_args_list:
            assert c.args[0][:2] == ["umount", "-f"]
            assert c.kwargs == {"sudo": True, "errok": True}

    def test_reclaim_removes_stale_keeps_live_and_other_targets(
        self, tmp_path: Path
    ) -> None:
        """Reclamation removes dead-run dirs for this target only,
        leaving the live dir, other targets' dirs, and stray files."""
        prefix = ".borgadm_borg-rsync.tmp."
        keep = tmp_path / (prefix + "live")
        stale1 = tmp_path / (prefix + "dead1")
        stale2 = tmp_path / (prefix + "dead2")
        other_target = tmp_path / ".borgadm_other.tmp.x"
        for d in (keep, stale1, stale2, other_target):
            d.mkdir()
        stray = tmp_path / (prefix + "stray")
        stray.write_text("")
        with (
            patch.object(ba, "_umount_tree", autospec=True) as mock_um,
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
        ):
            ba._reclaim_stale_run_dirs(tmp_path, prefix, keep)
        assert {c.args[0] for c in mock_um.call_args_list} == {
            stale1,
            stale2,
        }
        removed = {c.args[0][2] for c in mock_run.call_args_list}
        assert removed == {stale1.as_posix(), stale2.as_posix()}
        for c in mock_run.call_args_list:
            assert c.args[0][:2] == ["rm", "-rf"]
            assert c.kwargs == {"sudo": True, "errok": True}

    def test_do_rsync_unmounts_overlay_before_fuse(
        self, tmp_path: Path
    ) -> None:
        """The live cleanup tears the overlay 'merged' mount down before
        the per-archive fuse mounts it stacks on, and sudo-removes the
        root-owned overlay workdir."""
        target = tmp_path / "borg-rsync"
        target.mkdir()
        archives = ["repo::home-fuse-1of2", "repo::home-local-2of2"]

        def fake_mounts(root: Path) -> list[tuple[Path, str]]:
            return [
                (root / "home-fuse-1of2", "fuse"),
                (root / "home-local-2of2", "fuse"),
                (root / "merged", "overlay"),
            ]

        with (
            patch.object(ba.platform, "system", return_value="Linux"),
            patch.object(ba, "check_sudo", autospec=True, return_value=True),
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                return_value={"20260101_000000": archives},
            ),
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(ba.os, "listdir", return_value=["x"]),
            patch.object(ba, "run_borg", autospec=True),
            patch.object(
                ba, "_mounts_under", autospec=True, side_effect=fake_mounts
            ),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
        ):
            ba.do_rsync(
                target,
                dry_run=False,
                delete=True,
                progress=False,
                bypass_lock=True,
                archive=None,
            )
        umounts = [
            Path(c.args[0][2]).name
            for c in mock_run.call_args_list
            if c.args[0][0] == "umount"
        ]
        assert umounts == ["merged", "home-fuse-1of2", "home-local-2of2"]
        rms = [
            Path(c.args[0][2]).name
            for c in mock_run.call_args_list
            if c.args[0][:2] == ["rm", "-rf"]
        ]
        assert rms == ["workdir"]
        assert any(c.args[0][0] == "rsync" for c in mock_run.call_args_list)

    def test_do_rsync_archive_records_selected_timestamp(
        self, mock_cfg: Any, tmp_path: Path
    ) -> None:
        """`rsync --archive NAME` mirrors the selected archive and records
        its parsed timestamp in the .ts sidecar rather than the latest."""
        target = tmp_path / "borg-rsync"
        target.mkdir()
        raw = Mock()
        raw.stdout = "home-set1-20260101_000000_1of1\n"
        with (
            patch.object(ba.platform, "system", return_value="Linux"),
            patch.object(ba, "check_sudo", autospec=True, return_value=True),
            patch.object(
                ba, "list_backups_raw", autospec=True, return_value=raw
            ),
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(ba.os, "listdir", return_value=["x"]),
            patch.object(ba, "run_borg", autospec=True) as mock_run_borg,
            patch.object(ba, "_mounts_under", autospec=True, return_value=[]),
            patch.object(ba, "run_cmd", autospec=True),
        ):
            ba.do_rsync(
                target,
                dry_run=False,
                delete=False,
                progress=False,
                bypass_lock=True,
                archive="home-set1-20260101_000000_1of1",
            )
        sidecar = tmp_path / f".{ba.BASENAME}_borg-rsync.ts"
        assert sidecar.read_text() == "20260101_000000"
        mounted = [
            c.args[0][-2]
            for c in mock_run_borg.call_args_list
            if "mount" in c.args[0]
        ]
        assert mounted == [
            f"{mock_cfg.BORG_REPO}::home-set1-20260101_000000_1of1"
        ]

    def test_do_rsync_unmounts_mount_stranded_by_failure(
        self, tmp_path: Path
    ) -> None:
        """A failure after a successful mount still tears the mount down:
        the cleanup asks the mount table what is mounted rather than
        relying on having reached any later step, so the tmpdir removal
        never walks into a live mount."""
        target = tmp_path / "borg-rsync"
        target.mkdir()
        archives = ["repo::home-fuse-1of2"]
        err = ba.SubprocessError(2, ["borg"], output="", stderr="boom")

        def fake_mounts(root: Path) -> list[tuple[Path, str]]:
            return [(root / "home-fuse-1of2", "fuse")]

        with (
            patch.object(ba.platform, "system", return_value="Linux"),
            patch.object(ba, "check_sudo", autospec=True, return_value=True),
            patch.object(
                ba,
                "list_backups",
                autospec=True,
                return_value={"20260101_000000": archives},
            ),
            patch.object(ba, "borg_cmd", autospec=True, return_value=["borg"]),
            patch.object(ba, "run_borg", autospec=True, side_effect=err),
            patch.object(
                ba, "_mounts_under", autospec=True, side_effect=fake_mounts
            ),
            patch.object(ba, "run_cmd", autospec=True) as mock_run,
            pytest.raises(ba.SubprocessError),
        ):
            ba.do_rsync(
                target,
                dry_run=False,
                delete=True,
                progress=False,
                bypass_lock=True,
                archive=None,
            )
        umounts = [
            Path(c.args[0][2]).name
            for c in mock_run.call_args_list
            if c.args[0][0] == "umount"
        ]
        assert umounts == ["home-fuse-1of2"]
        assert not any(c.args[0][0] == "rsync" for c in mock_run.call_args_list)


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


class TestConfigCommands:
    """config init / config validate subcommands."""

    def test_init_writes_template(self) -> None:
        """config init writes the shipped template to the config path."""
        assert not ba.CONFIG.exists()
        ba.do_config_init(str(ba.CONFIG), force=False)
        assert ba.CONFIG.exists()
        text = ba.CONFIG.read_text(encoding="utf-8")
        assert "BORG_REPO" in text
        assert "BACKUP_SETS" in text

    def test_init_refuses_existing_without_force(self) -> None:
        """An existing config is left intact unless --force is given."""
        ba.CONFIG.write_text("KEEP ME", encoding="utf-8")
        with pytest.raises(ba.ConfigError, match="already exists"):
            ba.do_config_init(str(ba.CONFIG), force=False)
        assert ba.CONFIG.read_text(encoding="utf-8") == "KEEP ME"

    def test_init_force_overwrites(self) -> None:
        """--force replaces an existing config with the template."""
        ba.CONFIG.write_text("OLD", encoding="utf-8")
        ba.do_config_init(str(ba.CONFIG), force=True)
        text = ba.CONFIG.read_text(encoding="utf-8")
        assert "OLD" not in text
        assert "BACKUP_SETS" in text

    def test_init_honors_config_flag(self, tmp_path: Path) -> None:
        """config init writes to the --config path, not the default."""
        dest = tmp_path / "custom.borgadm"
        ba.do_config_init(str(dest), force=False)
        assert dest.exists()
        assert "BACKUP_SETS" in dest.read_text(encoding="utf-8")
        assert not ba.CONFIG.exists()

    def test_validate_reports_resolved_path(
        self, tmp_path: Path, caplog: Any
    ) -> None:
        """do_config_validate builds the Config and reports its path."""
        cfg_file = tmp_path / "ok"
        cfg_file.write_text(
            'BORG_REPO = /repo\nBACKUP_SETS = {"s": {"paths": ["x"]}}\n',
            encoding="utf-8",
        )
        with caplog.at_level(logging.INFO):
            ba.do_config_validate(str(cfg_file))
        assert any(
            f"config valid: {cfg_file}" in r.message for r in caplog.records
        )

    def test_init_cli_writes_then_refuses_then_forces(self) -> None:
        """`config init` writes, refuses a second run, and --force wins."""
        with patch("sys.argv", ["borgadm", "config", "init"]):
            assert ba.cli() == ba.ExitCode.SUCCESS
        assert ba.CONFIG.exists()
        with patch("sys.argv", ["borgadm", "config", "init"]):
            assert ba.cli() == ba.ExitCode.CONFIG
        with patch("sys.argv", ["borgadm", "config", "init", "--force"]):
            assert ba.cli() == ba.ExitCode.SUCCESS

    def test_init_cli_honors_config_flag(self, tmp_path: Path) -> None:
        """`config init --config PATH` writes PATH, not the default."""
        dest = tmp_path / "elsewhere.borgadm"
        with patch(
            "sys.argv",
            ["borgadm", "config", "init", "--config", str(dest)],
        ):
            assert ba.cli() == ba.ExitCode.SUCCESS
        assert dest.exists()
        assert not ba.CONFIG.exists()

    def test_validate_cli_passes_on_good_config(
        self, tmp_path: Path, caplog: Any
    ) -> None:
        cfg_file = tmp_path / "good"
        cfg_file.write_text(
            'BORG_REPO = /repo\nBACKUP_SETS = {"s": {"paths": ["x"]}}\n',
            encoding="utf-8",
        )
        with (
            patch(
                "sys.argv",
                ["borgadm", "config", "validate", "--config", str(cfg_file)],
            ),
            caplog.at_level(logging.INFO),
        ):
            assert ba.cli() == ba.ExitCode.SUCCESS
        assert any("config valid:" in r.message for r in caplog.records)

    def test_validate_cli_reports_errors(self, tmp_path: Path) -> None:
        """A config missing BORG_REPO fails validation with CONFIG."""
        cfg_file = tmp_path / "bad"
        cfg_file.write_text(
            'BACKUP_SETS = {"s": {"paths": ["x"]}}\n', encoding="utf-8"
        )
        with patch(
            "sys.argv",
            ["borgadm", "config", "validate", "--config", str(cfg_file)],
        ):
            assert ba.cli() == ba.ExitCode.CONFIG

    def test_validate_cli_flags_missing_backup_sets(
        self, tmp_path: Path
    ) -> None:
        """validate fails when BACKUP_SETS is absent, even with a repo set:
        create needs it, so the gap surfaces here, not at first backup."""
        cfg_file = tmp_path / "no_sets"
        cfg_file.write_text("BORG_REPO = /repo\n", encoding="utf-8")
        with patch(
            "sys.argv",
            ["borgadm", "config", "validate", "--config", str(cfg_file)],
        ):
            assert ba.cli() == ba.ExitCode.CONFIG

    def test_validate_cli_missing_file(self, tmp_path: Path) -> None:
        """Validating an absent config file fails with CONFIG."""
        with patch(
            "sys.argv",
            [
                "borgadm",
                "config",
                "validate",
                "--config",
                str(tmp_path / "nope"),
            ],
        ):
            assert ba.cli() == ba.ExitCode.CONFIG

    def test_generated_config_is_valid(self, tmp_path: Path) -> None:
        """The config `config init` generates is ASCII, parses as INI,
        carries valid-JSON BACKUP_SETS, and loads through Config."""
        cfg_file = tmp_path / "generated"
        ba.do_config_init(str(cfg_file), force=False)
        text = cfg_file.read_text(encoding="utf-8")
        text.encode("ascii")  # raises if non-ASCII slips in
        cfg = ba.Config(str(cfg_file), {})
        assert cfg.BORG_REPO
        assert set(cfg.BACKUP_SETS) == {"local", "fuse"}
        assert cfg.BACKUP_SETS["fuse"]["create_options"] == [
            "--noacls",
            "--noctime",
            "--noxattrs",
        ]
        assert "create_options" not in cfg.BACKUP_SETS["local"]

    def test_config_fields_match_config(self, tmp_path: Path) -> None:
        """CONFIG_FIELDS covers exactly the settings Config reads -- so a
        field added to one without the other (the historical drift) fails
        here rather than silently dropping from the docs or generated
        config."""
        cfg_file = tmp_path / "generated"
        ba.do_config_init(str(cfg_file), force=False)
        cfg = ba.Config(str(cfg_file), {})
        read_attrs = {k for k in vars(cfg) if k.isupper()}
        schema_names = {f.name for f in ba.CONFIG_FIELDS}
        assert read_attrs == schema_names

    def test_init_then_validate_roundtrip(self, tmp_path: Path) -> None:
        """A freshly generated config passes `config validate` unedited."""
        cfg_file = tmp_path / "generated"
        with patch(
            "sys.argv",
            ["borgadm", "config", "init", "--config", str(cfg_file)],
        ):
            assert ba.cli() == ba.ExitCode.SUCCESS
        with patch(
            "sys.argv",
            ["borgadm", "config", "validate", "--config", str(cfg_file)],
        ):
            assert ba.cli() == ba.ExitCode.SUCCESS


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
        for job in ba._CRONY_JOBS:
            assert "--timestamp-messages" in job.command, job.command


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
import logging
import sys

sys.path.insert(0, {src!r})
import borgadm

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
        setup = self._SETUP.format(src=str(REPO_ROOT / "src"))
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
        # The fake sink implements only write/flush, not the full
        # TextIO surface pump_stream's signature asks for.
        ba.pump_stream(src, cast(Any, sink), acc, allow_output=True)

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
        ba.pump_stream(src, cast(Any, ExplodingSink()), acc, allow_output=False)
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
            ("automate", "apply"),
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

    def test_extract_archive_timestamp_restores_that_backup(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """`extract --archive TIMESTAMP` restores the set at that time
        rather than the latest; the bare form still restores the latest."""
        old_ts = "20260101_120000"
        new_ts = "20260202_120000"
        old_src = tmp_path / "old-src"
        old_src.mkdir()
        (old_src / "data.txt").write_text("old-version")
        new_src = tmp_path / "new-src"
        new_src.mkdir()
        (new_src / "data.txt").write_text("new-version")
        borg_e2e.make_archive(
            _archive_name("set-a", old_ts, 1, 1), content_path=old_src
        )
        borg_e2e.make_archive(
            _archive_name("set-a", new_ts, 1, 1), content_path=new_src
        )

        picked = tmp_path / "picked"
        picked.mkdir()
        result = borg_e2e.run("extract", str(picked), "--archive", old_ts)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        restored = list(picked.rglob("data.txt"))
        assert len(restored) == 1
        assert restored[0].read_text() == "old-version"

        latest = tmp_path / "latest"
        latest.mkdir()
        result = borg_e2e.run("extract", str(latest))
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        restored = list(latest.rglob("data.txt"))
        assert len(restored) == 1
        assert restored[0].read_text() == "new-version"

    def test_extract_archive_name_restores_only_that_archive(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """`extract --archive NAME` restores just the named archive, not
        the sibling archives sharing its timestamp."""
        ts = "20260101_120000"
        src_a = tmp_path / "src-a"
        src_a.mkdir()
        (src_a / "a.txt").write_text("A")
        src_b = tmp_path / "src-b"
        src_b.mkdir()
        (src_b / "b.txt").write_text("B")
        name_a = _archive_name("set-a", ts, 1, 2)
        borg_e2e.make_archive(name_a, content_path=src_a)
        borg_e2e.make_archive(
            _archive_name("set-b", ts, 2, 2), content_path=src_b
        )

        target = tmp_path / "extract-target"
        target.mkdir()
        result = borg_e2e.run("extract", str(target), "--archive", name_a)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert len(list(target.rglob("a.txt"))) == 1
        assert list(target.rglob("b.txt")) == []

    def test_extract_archive_unknown_timestamp_fails(
        self, borg_e2e: BorgE2EFixture, tmp_path: Path
    ) -> None:
        """An unknown --archive selector fails rather than falling back
        to the latest backup."""
        borg_e2e.make_archive(_archive_name("set-a", "20260101_120000", 1, 1))
        target = tmp_path / "extract-target"
        target.mkdir()
        result = borg_e2e.run(
            "extract", str(target), "--archive", "20990101_000000", check=False
        )
        assert result.returncode != 0


@pytest.mark.e2e
class TestE2ECheckArchives:
    """E2E coverage for `borgadm check archives` argument resolution: it
    reads a name or a YYYYMMDD_HHMMSS timestamp the same way delete does
    (via the shared _resolve_archives)."""

    def test_check_archives_by_timestamp_checks_that_timestamp(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`borgadm check archives TIMESTAMP` checks every archive at that
        timestamp and leaves other timestamps unchecked."""
        check_ts = "20260101_120000"
        other_ts = "20260102_120000"
        checked = [
            _archive_name("set-a", check_ts, 1, 2),
            _archive_name("set-b", check_ts, 2, 2),
        ]
        other = _archive_name("set-a", other_ts, 1, 2)
        for name in (*checked, other):
            borg_e2e.make_archive(name)
        result = borg_e2e.run("check", "archives", check_ts)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        for name in checked:
            assert name in result.stdout
        assert other not in result.stdout

    def test_check_archives_unknown_timestamp_fails(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """An unknown timestamp fails rather than silently checking all."""
        borg_e2e.make_archive(_archive_name("set-a", "20260101_120000", 1, 2))
        result = borg_e2e.run(
            "check", "archives", "20990101_000000", check=False
        )
        assert result.returncode != 0


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
        ba.ExitCode.CRASHED,
    }


class TestHelpWidth(HelpWidthBase):
    PROG = "borgadm"
    PARSER_FUNC = staticmethod(ba.args_parser)


@pytest.mark.e2e
class TestLockAwareE2E:
    """End-to-end: real held locks (repository and local cache) vs.
    blocking / bypassing reads."""

    def _hold_lock(
        self, borg_e2e: BorgE2EFixture, seconds: int
    ) -> subprocess.Popen[bytes]:
        """Hold the repo's exclusive lock for `seconds` in a background
        `borg with-lock` process."""
        return subprocess.Popen(
            [
                "borg",
                "with-lock",
                str(borg_e2e.repo_path),
                "sleep",
                str(seconds),
            ],
            env=borg_e2e._subprocess_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _hold_cache_lock(
        self, borg_e2e: BorgE2EFixture, seconds: int
    ) -> subprocess.Popen[bytes]:
        """Hold the local cache lock for `seconds` in a background create.

        A `borg create` takes the cache lock exclusively for its whole
        run; blocking it on a slow `--content-from-command` source keeps
        that lock held. `borg with-lock` would not -- it takes only the
        repository lock -- so this is the holder that reproduces a create
        running concurrently with a read.
        """
        return subprocess.Popen(
            [
                "borg",
                "create",
                "--content-from-command",
                "--stdin-name",
                "held",
                f"{borg_e2e.repo_path}::cache-lock-holder",
                "--",
                "sh",
                "-c",
                f"sleep {seconds}",
            ],
            env=borg_e2e._subprocess_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _await_lock_held(self, borg_e2e: BorgE2EFixture) -> None:
        """Block until the holder has actually taken the lock."""
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            probe = subprocess.run(
                [
                    "borg",
                    "list",
                    "--short",
                    "--lock-wait",
                    "0",
                    str(borg_e2e.repo_path),
                ],
                env=borg_e2e._subprocess_env(),
                capture_output=True,
                text=True,
            )
            if probe.returncode != 0 and "lock" in probe.stderr.lower():
                return
            time.sleep(0.1)
        pytest.fail("background holder never acquired the lock")

    def test_bypass_lock_does_not_wait_for_held_lock(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`list --bypass-lock` returns while the lock is held; if it
        waited it would trip the 15s timeout against the 30s holder."""
        borg_e2e.run("create", "--no-prune")
        holder = self._hold_lock(borg_e2e, 30)
        try:
            self._await_lock_held(borg_e2e)
            result = borg_e2e.run("list", "--bypass-lock", timeout=15)
        finally:
            holder.terminate()
            holder.wait()
        assert result.returncode == 0
        assert result.stdout.strip(), "expected the held archive to be listed"
        assert "A borg lock is held" not in (result.stdout + result.stderr)

    def test_bypass_lock_reads_through_held_cache_lock(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """`list --bypass-lock` returns while a concurrent create holds
        the local cache lock. borg's --bypass-lock skips only the
        repository lock, so the bypass path also redirects borg's cache
        (and security) dir to a throwaway location -- without that the
        read would block on the held cache lock and trip the 15s timeout
        against the 30s holder."""
        borg_e2e.run("create", "--no-prune")
        holder = self._hold_cache_lock(borg_e2e, 30)
        try:
            self._await_lock_held(borg_e2e)
            result = borg_e2e.run("list", "--bypass-lock", timeout=15)
        finally:
            holder.terminate()
            holder.wait()
        assert result.returncode == 0
        assert result.stdout.strip(), "expected the held archive to be listed"
        assert "Failed to create/acquire the lock" not in (
            result.stdout + result.stderr
        )

    def test_default_list_waits_for_lock_then_succeeds(
        self, borg_e2e: BorgE2EFixture
    ) -> None:
        """A default (blocking) `list` announces the held lock, waits for
        it to release, then succeeds. The shortened lock timeouts keep
        borg's poll interval small so the test stays fast."""
        borg_e2e.config_path.write_text(
            borg_e2e.config_path.read_text()
            + "LOCK_CHECK_TIMEOUT = 2\n"
            + "BORG_CMD_TIMEOUT = 20\n"
        )
        borg_e2e.run("create", "--no-prune")
        # Hold well past borgadm's startup so the probe is guaranteed to
        # observe the lock held and emit the waiting message; only then
        # release it (via terminate) and let the blocked list proceed.
        holder = self._hold_lock(borg_e2e, 60)
        try:
            self._await_lock_held(borg_e2e)
            proc = subprocess.Popen(
                [str(borg_e2e.borgadm_bin), "list"],
                env=borg_e2e._subprocess_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            # Long enough for borgadm to start, probe (LOCK_CHECK_TIMEOUT),
            # log the waiting message, and begin blocking -- all while the
            # lock is still held.
            time.sleep(8)
        finally:
            holder.terminate()
            holder.wait()
        out, _ = proc.communicate(timeout=60)
        assert proc.returncode == 0, f"output:\n{out}"
        assert "A borg lock is held" in out
        assert out.strip(), "expected the archive to be listed"


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
