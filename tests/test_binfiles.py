#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov"]
# ///
# This is AI generated code

"""
Unit tests for the binfiles profile.

The binfiles utility is a symlink to bin/dotfiles. Profile dispatch in
cli() picks BINFILES_PROFILE from sys.argv[0]; behavioral coverage of
the shared codepaths lives in test_dotfiles.py. This file focuses on
binfiles-specific differences (no dot-prefix, flat discovery,
executable-only filter, ~/.local/bin target root) plus a smoke test of
each subcommand under the binfiles profile.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from conftest import (
    CmdCallbacksBase,
    ExceptionHierarchyBase,
    IsolateHomeFixtureBase,
    isolate_home,
)

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# binfiles is the same source as dotfiles (bin/binfiles symlinks to
# bin/dotfiles), imported under its own module name so this test gets a
# module object with ACTIVE_PROFILE state distinct from test_dotfiles.
import binfiles as bf  # noqa: E402

# The bin script under test, for run_tests' coverage module name.
_script_path = REPO_ROOT / "bin" / "binfiles"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: Any) -> None:
    isolate_home(bf, ".binfiles.installed", tmp_path, monkeypatch)


def _make_executable(path: Path) -> None:
    """Write a minimal executable script to path."""
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)


class TestIsolateHomeFixture(IsolateHomeFixtureBase):
    MODULE: ClassVar[Any] = bf
    SOURCE_NAME = "tool"
    PROFILE_ATTR = "BINFILES_PROFILE"

    @staticmethod
    def _make_source(path: Path) -> None:
        _make_executable(path)


class TestSelectProfile:
    """select_profile picks the right Profile from argv[0]."""

    def test_binfiles_name_returns_binfiles_profile(self) -> None:
        assert bf.select_profile("binfiles") is bf.BINFILES_PROFILE

    def test_binfiles_path_returns_binfiles_profile(self) -> None:
        assert (
            bf.select_profile("/usr/local/bin/binfiles") is bf.BINFILES_PROFILE
        )

    def test_dotfiles_name_returns_dotfiles_profile(self) -> None:
        assert bf.select_profile("dotfiles") is bf.DOTFILES_PROFILE

    def test_unknown_name_defaults_to_dotfiles(self) -> None:
        assert bf.select_profile("anything-else") is bf.DOTFILES_PROFILE


class TestBinfilesProfileFields:
    """BINFILES_PROFILE has the expected target root, transform, etc."""

    def test_target_root_is_local_bin(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(Path, "home", lambda: Path("/home/u"))
        assert bf.BINFILES_PROFILE.target_root() == Path("/home/u/.local/bin")

    def test_transform_segment_is_identity(self) -> None:
        assert bf.BINFILES_PROFILE.transform_segment("tool") == "tool"

    def test_installed_file_path(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(Path, "home", lambda: Path("/home/u"))
        assert bf.BINFILES_PROFILE.installed_file() == Path(
            "/home/u/.binfiles.installed"
        )

    def test_flat_is_true(self) -> None:
        assert bf.BINFILES_PROFILE.flat is True

    def test_executable_only_is_true(self) -> None:
        assert bf.BINFILES_PROFILE.executable_only is True


class TestBinfilesEntryTargetPath:
    """DotfileEntry under the binfiles profile uses the no-dot transform."""

    def test_target_path_no_dot_prefix(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(Path, "home", lambda: Path("/home/u"))
        entry = bf.DotfileEntry(
            relative_path=Path("mytool"),
            dotfile_dir=Path("/home/u/binfiles"),
            profile=bf.BINFILES_PROFILE,
        )
        assert entry.target_path == Path("/home/u/.local/bin/mytool")


class TestIsExecutable:
    """_is_executable returns True only for files with the +x bit."""

    def test_executable_file(self, tmp_path: Path) -> None:
        f = tmp_path / "tool"
        _make_executable(f)
        assert bf._is_executable(f) is True

    def test_non_executable_file(self, tmp_path: Path) -> None:
        f = tmp_path / "readme"
        f.write_text("docs")
        f.chmod(0o644)
        assert bf._is_executable(f) is False

    def test_broken_symlink_is_not_executable(self, tmp_path: Path) -> None:
        link = tmp_path / "broken"
        link.symlink_to(tmp_path / "missing")
        assert bf._is_executable(link) is False

    def test_executable_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "real"
        _make_executable(target)
        link = tmp_path / "link"
        link.symlink_to(target)
        assert bf._is_executable(link) is True


class TestBinfilesDiscovery:
    """discover_dotfiles under BINFILES_PROFILE applies flat /
    executable-only rules."""

    def test_flat_skips_subdirectories(self, tmp_path: Path) -> None:
        src = tmp_path / "binfiles"
        src.mkdir()
        _make_executable(src / "tool1")
        sub = src / "subdir"
        sub.mkdir()
        _make_executable(sub / "tool2")

        entries = bf.discover_dotfiles(src, profile=bf.BINFILES_PROFILE)
        names = {e.relative_path for e in entries}
        assert Path("tool1") in names
        assert Path("subdir/tool2") not in names
        assert len(entries) == 1

    def test_executable_only_skips_non_executable(
        self, tmp_path: Path, caplog: Any
    ) -> None:
        src = tmp_path / "binfiles"
        src.mkdir()
        _make_executable(src / "tool")
        readme = src / "readme"
        readme.write_text("docs\n")
        readme.chmod(0o644)

        with caplog.at_level(logging.WARNING, logger=bf.logger.name):
            entries = bf.discover_dotfiles(src, profile=bf.BINFILES_PROFILE)

        names = {e.relative_path for e in entries}
        assert Path("tool") in names
        assert Path("readme") not in names
        assert "skipping non-executable" in caplog.text

    def test_includes_executable_symlinks(self, tmp_path: Path) -> None:
        src = tmp_path / "binfiles"
        src.mkdir()
        _make_executable(src / "real")
        (src / "alias").symlink_to("real")

        entries = bf.discover_dotfiles(src, profile=bf.BINFILES_PROFILE)
        names = {e.relative_path for e in entries}
        assert Path("alias") in names

    def test_skips_root_dotfiles(self, tmp_path: Path) -> None:
        """Root-level dotfiles are skipped under binfiles too."""
        src = tmp_path / "binfiles"
        src.mkdir()
        _make_executable(src / "tool")
        _make_executable(src / ".hidden")  # dotfile in root

        entries = bf.discover_dotfiles(src, profile=bf.BINFILES_PROFILE)
        names = {e.relative_path for e in entries}
        assert Path("tool") in names
        assert Path(".hidden") not in names

    def test_respects_gitignore(self, tmp_path: Path) -> None:
        src = tmp_path / "binfiles"
        src.mkdir()
        (src / ".gitignore").write_text("*.bak\n")
        _make_executable(src / "tool")
        _make_executable(src / "old.bak")

        entries = bf.discover_dotfiles(src, profile=bf.BINFILES_PROFILE)
        names = {e.relative_path for e in entries}
        assert Path("tool") in names
        assert Path("old.bak") not in names

    def test_respects_dotfilesignore(self, tmp_path: Path) -> None:
        """A binfiles repo can keep tests/ tracked but unlinked via
        .dotfilesignore (same file the dotfiles profile reads)."""
        src = tmp_path / "binfiles"
        src.mkdir()
        (src / ".dotfilesignore").write_text("tests/\n")
        _make_executable(src / "tool")
        (src / "tests").mkdir()
        _make_executable(src / "tests" / "test_format")

        entries = bf.discover_dotfiles(src, profile=bf.BINFILES_PROFILE)
        names = {e.relative_path for e in entries}
        assert Path("tool") in names
        assert Path("tests/test_format") not in names


class TestBinfilesSubcommands:
    """Smoke-test install/remove/audit/cleanup under BINFILES_PROFILE."""

    def _setup(self, tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path]:
        home = tmp_path / "home"
        home.mkdir()
        (home / ".local" / "bin").mkdir(parents=True)
        installed_file = tmp_path / ".binfiles.installed"
        monkeypatch.setattr(bf, "INSTALLED_FILE", installed_file)
        monkeypatch.setattr(bf, "ACTIVE_PROFILE", bf.BINFILES_PROFILE)
        return home, installed_file

    def test_install_creates_link_in_local_bin(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        home, _ = self._setup(tmp_path, monkeypatch)
        src = tmp_path / "binfiles"
        src.mkdir()
        _make_executable(src / "tool")

        with patch.object(Path, "home", return_value=home):
            bf.do_install(src, dry_run=False, force=False, verbose=False)

        target = home / ".local" / "bin" / "tool"
        assert target.is_symlink()
        assert target.resolve() == (src / "tool").resolve()

    def test_remove_unlinks(self, tmp_path: Path, monkeypatch: Any) -> None:
        home, _ = self._setup(tmp_path, monkeypatch)
        src = tmp_path / "binfiles"
        src.mkdir()
        _make_executable(src / "tool")

        with patch.object(Path, "home", return_value=home):
            bf.do_install(src, dry_run=False, force=False, verbose=False)
            bf.do_remove(src, dry_run=False, verbose=False)

        assert not (home / ".local" / "bin" / "tool").exists()

    def test_audit_clean(self, tmp_path: Path, monkeypatch: Any) -> None:
        home, _ = self._setup(tmp_path, monkeypatch)
        src = tmp_path / "binfiles"
        src.mkdir()
        _make_executable(src / "tool")

        with patch.object(Path, "home", return_value=home):
            bf.do_install(src, dry_run=False, force=False, verbose=False)
            bf.do_audit(src, verbose=False)

    def test_cleanup_removes_dangling_link(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        home, _ = self._setup(tmp_path, monkeypatch)
        target_root = home / ".local" / "bin"
        (target_root / "broken").symlink_to(tmp_path / "nonexistent")

        with patch.object(Path, "home", return_value=home):
            bf.do_cleanup(dry_run=False)

        assert not (target_root / "broken").is_symlink()

    def test_cleanup_dry_run_keeps_link(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        home, _ = self._setup(tmp_path, monkeypatch)
        target_root = home / ".local" / "bin"
        (target_root / "broken").symlink_to(tmp_path / "nonexistent")

        with patch.object(Path, "home", return_value=home):
            bf.do_cleanup(dry_run=True)

        assert (target_root / "broken").is_symlink()

    def test_cleanup_leaves_valid_link(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        home, _ = self._setup(tmp_path, monkeypatch)
        target_root = home / ".local" / "bin"
        real = tmp_path / "real"
        _make_executable(real)
        (target_root / "tool").symlink_to(real)

        with patch.object(Path, "home", return_value=home):
            bf.do_cleanup(dry_run=False)

        assert (target_root / "tool").is_symlink()

    def test_audit_flags_stale_managed_link(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Stale-link detection works under the binfiles profile."""
        home, _ = self._setup(tmp_path, monkeypatch)
        src = tmp_path / "binfiles"
        src.mkdir()
        _make_executable(src / "tool")
        _make_executable(src / "old_tool")
        with patch.object(Path, "home", return_value=home):
            bf.do_install(src, dry_run=False, force=False, verbose=False)
            (src / "old_tool").unlink()
            with pytest.raises(bf.ConflictsFound):
                bf.do_audit(src, verbose=False)

    def test_install_prunes_stale_link(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Re-install of a binfiles repo removes stale links for
        files that have left the repo."""
        home, _ = self._setup(tmp_path, monkeypatch)
        src = tmp_path / "binfiles"
        src.mkdir()
        _make_executable(src / "tool")
        _make_executable(src / "old_tool")
        target_root = home / ".local" / "bin"

        with patch.object(Path, "home", return_value=home):
            bf.do_install(src, dry_run=False, force=False, verbose=False)
            assert (target_root / "old_tool").is_symlink()
            (src / "old_tool").unlink()
            bf.do_install(src, dry_run=False, force=False, verbose=False)

        assert (target_root / "tool").is_symlink()
        assert not (target_root / "old_tool").is_symlink()


class TestBinfilesCli:
    """cli() activates BINFILES_PROFILE when invoked as 'binfiles'."""

    def test_cli_activates_binfiles_profile(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Snapshot the module-level globals via monkeypatch so that
        # cli()'s mutation is reverted at test teardown.
        monkeypatch.setattr(bf, "ACTIVE_PROFILE", bf.ACTIVE_PROFILE)
        monkeypatch.setattr(bf, "INSTALLED_FILE", bf.INSTALLED_FILE)
        home = tmp_path / "home"
        home.mkdir()
        with patch.object(Path, "home", return_value=home):
            with patch("sys.argv", ["binfiles", "audit"]):
                bf.cli()
            assert bf.ACTIVE_PROFILE is bf.BINFILES_PROFILE
            assert bf.INSTALLED_FILE == home / ".binfiles.installed"

    def test_cli_activates_dotfiles_profile_for_unknown_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(bf, "ACTIVE_PROFILE", bf.ACTIVE_PROFILE)
        monkeypatch.setattr(bf, "INSTALLED_FILE", bf.INSTALLED_FILE)
        home = tmp_path / "home"
        home.mkdir()
        with patch.object(Path, "home", return_value=home):
            with patch("sys.argv", ["prog", "audit"]):
                bf.cli()
            assert bf.ACTIVE_PROFILE is bf.DOTFILES_PROFILE


class TestCmdCallbacks(CmdCallbacksBase):
    """Reuse generic CLI dispatch tests against the binfiles module."""

    CALLBACKS = bf.COMMAND_CALLBACKS
    PARSER_FUNC = bf.build_parser
    CLI_FUNC = staticmethod(bf.cli)
    EXIT_CODE_USAGE = bf.ExitCode.USAGE
    TEST_SUBCOMMAND = "audit"
    EXCEPTION_EXIT_CODE_MAP = [
        (bf.ConflictsFound("t"), bf.ExitCode.CONFLICTS),
        (bf.UsageError("t"), bf.ExitCode.USAGE),
        (
            bf.MissingDotfilesDirectory("t"),
            bf.ExitCode.MISSING_DIR,
        ),
        (RuntimeError("t"), bf.ExitCode.CRASHED),
    ]


class TestExceptionHierarchy(ExceptionHierarchyBase):
    BASE_ERROR = bf.DotfilesError
    EXIT_CODE = bf.ExitCode
    EXCLUDED_CODES = {
        bf.ExitCode.SUCCESS,
        bf.ExitCode.WARNING,
        bf.ExitCode.CONFIG,
        bf.ExitCode.SUBPROCESS,
        bf.ExitCode.CRASHED,
    }


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
