#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov"]
# ///
# This is AI generated code

"""
Comprehensive unit tests for dotfiles
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest  # type: ignore[import-not-found]

# Repository root directory (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent

# Import dotfiles module from bin/ (works with or without .py extension)
_script_path = REPO_ROOT / "bin" / "dotfiles"
if not _script_path.exists():
    _script_path = REPO_ROOT / "bin" / "dotfiles.py"
_loader = importlib.machinery.SourceFileLoader("dotfiles", str(_script_path))
_spec = importlib.util.spec_from_loader("dotfiles", _loader)
assert _spec and _spec.loader
df = importlib.util.module_from_spec(_spec)
sys.modules["dotfiles"] = df
_spec.loader.exec_module(df)


class TestArgumentParser:
    """Test argument parser structure."""

    def test_parser_builds_successfully(self) -> None:
        """Verify parser can be built without errors."""
        parser = df.build_parser()
        assert parser is not None

    def test_all_subcommands_have_help(self) -> None:
        """Smoke test: verify subcommands can show help."""
        parser = df.build_parser()
        # If this doesn't raise SystemExit, the subcommand is broken
        with pytest.raises(SystemExit):
            parser.parse_args(["install", "--help"])
        with pytest.raises(SystemExit):
            parser.parse_args(["remove", "--help"])
        with pytest.raises(SystemExit):
            parser.parse_args(["audit", "--help"])
        with pytest.raises(SystemExit):
            parser.parse_args(["self-test", "--help"])

    def test_install_parses_directory(self) -> None:
        """Test install subcommand parses directory argument."""
        parser = df.build_parser()
        args = parser.parse_args(["install", "/path/to/dotfiles"])
        assert args.command == "install"
        assert args.directory == Path("/path/to/dotfiles")

    def test_install_parses_flags(self) -> None:
        """Test install subcommand parses flags."""
        parser = df.build_parser()
        args = parser.parse_args(["install", "--dry-run", "-f", "/path"])
        assert args.dry_run is True
        assert args.force is True

    def test_install_without_directory(self) -> None:
        """Test install subcommand without directory."""
        parser = df.build_parser()
        args = parser.parse_args(["install"])
        assert args.command == "install"
        assert args.directory is None

    def test_remove_parses_directory(self) -> None:
        """Test remove subcommand parses directory argument."""
        parser = df.build_parser()
        args = parser.parse_args(["remove", "/path/to/dotfiles"])
        assert args.command == "remove"
        assert args.directory == Path("/path/to/dotfiles")

    def test_audit_parses_directory(self) -> None:
        """Test audit subcommand parses directory argument."""
        parser = df.build_parser()
        args = parser.parse_args(["audit", "/path/to/dotfiles"])
        assert args.command == "audit"
        assert args.directory == Path("/path/to/dotfiles")

    def test_self_test_parses_flags(self) -> None:
        """Test self-test subcommand parses flags."""
        parser = df.build_parser()
        args = parser.parse_args(["self-test", "-v", "--coverage"])
        assert args.command == "self-test"
        assert args.verbose is True
        assert args.coverage is True


class TestDotfileEntry:
    """Test DotfileEntry dataclass."""

    def test_source_path(self) -> None:
        """Test source_path property."""
        entry = df.DotfileEntry(
            relative_path=Path("vimrc"),
            dotfile_dir=Path("/home/user/dotfiles"),
        )
        assert entry.source_path == Path("/home/user/dotfiles/vimrc")

    def test_target_path_simple(self, monkeypatch: Any) -> None:
        """Test target_path property for simple file."""
        monkeypatch.setattr(Path, "home", lambda: Path("/home/user"))
        entry = df.DotfileEntry(
            relative_path=Path("vimrc"),
            dotfile_dir=Path("/home/user/dotfiles"),
        )
        assert entry.target_path == Path("/home/user/.vimrc")

    def test_target_path_nested(self, monkeypatch: Any) -> None:
        """Test target_path property for nested file."""
        monkeypatch.setattr(Path, "home", lambda: Path("/home/user"))
        entry = df.DotfileEntry(
            relative_path=Path("config/nvim/init.vim"),
            dotfile_dir=Path("/home/user/dotfiles"),
        )
        assert entry.target_path == Path("/home/user/.config/nvim/init.vim")

    def test_compute_relative_symlink(self, tmp_path: Path) -> None:
        """Test computing relative symlink path."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        # Simulate home at tmp_path
        home = tmp_path / "home"
        home.mkdir()

        # Manually set up for testing
        with patch.object(Path, "home", return_value=home):
            entry_with_home = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            relative = entry_with_home.compute_relative_symlink()
            # Should be a relative path
            assert not relative.is_absolute()


class TestDotfileStatus:
    """Test DotfileStatus enum."""

    def test_all_statuses_have_values(self) -> None:
        """Verify all statuses have string values."""
        assert df.DotfileStatus.FILE_CONFLICT.value == "file-conflict"
        assert df.DotfileStatus.LINK_CONFLICT.value == "link-conflict"
        assert df.DotfileStatus.OK.value == "ok"
        assert df.DotfileStatus.MISSING.value == "missing"
        assert df.DotfileStatus.INSTALLED.value == "installed"
        assert df.DotfileStatus.REMOVED.value == "removed"


class TestInstalledDirectories:
    """Test installed directories management."""

    def test_load_installed_directories_empty(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test loading when file doesn't exist."""
        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)
        result = df.load_installed_directories()
        assert result == []

    def test_load_installed_directories_with_content(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test loading with existing content."""
        installed_file = tmp_path / ".dotfiles.installed"
        installed_file.write_text("/path/one\n/path/two\n")
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        result = df.load_installed_directories()

        assert len(result) == 2
        assert Path("/path/one") in result
        assert Path("/path/two") in result

    def test_save_installed_directories(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test saving directories."""
        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        df.save_installed_directories([Path("/path/one"), Path("/path/two")])

        content = installed_file.read_text()
        assert "/path/one" in content
        assert "/path/two" in content

    def test_add_installed_directory(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test adding a directory."""
        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        test_dir = tmp_path / "dotfiles"
        test_dir.mkdir()

        df.add_installed_directory(test_dir)

        result = df.load_installed_directories()
        assert test_dir.resolve() in result

    def test_add_installed_directory_no_duplicate(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test that adding same directory twice doesn't duplicate."""
        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        test_dir = tmp_path / "dotfiles"
        test_dir.mkdir()

        df.add_installed_directory(test_dir)
        df.add_installed_directory(test_dir)

        result = df.load_installed_directories()
        assert len(result) == 1

    def test_remove_installed_directory(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test removing a directory."""
        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        test_dir = tmp_path / "dotfiles"
        test_dir.mkdir()

        df.add_installed_directory(test_dir)
        df.remove_installed_directory(test_dir)

        result = df.load_installed_directories()
        assert test_dir.resolve() not in result


class TestDotfileDiscovery:
    """Test dotfile discovery functionality."""

    def test_discover_dotfiles_simple(self, tmp_path: Path) -> None:
        """Test discovering simple dotfiles."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")
        (dotfile_dir / "bashrc").write_text("content")

        entries = df.discover_dotfiles(dotfile_dir)

        assert len(entries) == 2
        paths = {e.relative_path for e in entries}
        assert Path("vimrc") in paths
        assert Path("bashrc") in paths

    def test_discover_dotfiles_nested(self, tmp_path: Path) -> None:
        """Test discovering nested dotfiles."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "config").mkdir()
        (dotfile_dir / "config" / "nvim").mkdir()
        (dotfile_dir / "config" / "nvim" / "init.vim").write_text("content")

        entries = df.discover_dotfiles(dotfile_dir)

        assert len(entries) == 1
        assert entries[0].relative_path == Path("config/nvim/init.vim")

    def test_discover_dotfiles_includes_symlinks(self, tmp_path: Path) -> None:
        """Test that symlinks are discovered."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")
        (dotfile_dir / "vim_alias").symlink_to("vimrc")

        entries = df.discover_dotfiles(dotfile_dir)

        assert len(entries) == 2
        paths = {e.relative_path for e in entries}
        assert Path("vimrc") in paths
        assert Path("vim_alias") in paths

    def test_discover_dotfiles_empty_dir(self, tmp_path: Path) -> None:
        """Test discovering from empty directory."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()

        entries = df.discover_dotfiles(dotfile_dir)

        assert entries == []

    def test_discover_dotfiles_nonexistent_raises(self, tmp_path: Path) -> None:
        """Test that nonexistent directory raises MissingDotfilesDirectory."""
        nonexistent = tmp_path / "nonexistent"

        with pytest.raises(df.MissingDotfilesDirectory, match="does not exist"):
            df.discover_dotfiles(nonexistent)

    def test_discover_dotfiles_not_directory_raises(
        self, tmp_path: Path
    ) -> None:
        """Test that non-directory raises MissingDotfilesDirectory."""
        file_path = tmp_path / "file"
        file_path.write_text("content")

        with pytest.raises(
            df.MissingDotfilesDirectory, match="Not a directory"
        ):
            df.discover_dotfiles(file_path)

    def test_discover_dotfiles_sorted(self, tmp_path: Path) -> None:
        """Test that discovered dotfiles are sorted."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "zshrc").write_text("z")
        (dotfile_dir / "bashrc").write_text("b")
        (dotfile_dir / "profile").write_text("p")

        entries = df.discover_dotfiles(dotfile_dir)

        paths = [e.relative_path for e in entries]
        assert paths == sorted(paths)

    def test_discover_dotfiles_skips_vcs_directories(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test that VCS directories are skipped."""
        monkeypatch.setattr(df, "VCS_DIRS", {".testvcs"})

        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")
        vcs_dir = dotfile_dir / ".testvcs"
        vcs_dir.mkdir()
        (vcs_dir / "config").write_text("vcs config")
        (vcs_dir / "nested").mkdir()
        (vcs_dir / "nested" / "file").write_text("nested")

        entries = df.discover_dotfiles(dotfile_dir)

        paths = {e.relative_path for e in entries}
        assert Path("vimrc") in paths
        assert len(entries) == 1
        assert not any(".testvcs" in str(p) for p in paths)


class TestLoadIgnorePatterns:
    """Test load_ignore_patterns function."""

    def test_load_gitignore(self, tmp_path: Path) -> None:
        """Test loading patterns from .gitignore."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("*~\n*.bak\n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "*~" in patterns
        assert "*.bak" in patterns

    def test_load_hgignore(self, tmp_path: Path) -> None:
        """Test loading patterns from .hgignore."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".hgignore").write_text("*.tmp\n*.swp\n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "*.tmp" in patterns
        assert "*.swp" in patterns

    def test_load_both_ignore_files(self, tmp_path: Path) -> None:
        """Test loading patterns from both .gitignore and .hgignore."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("*~\n")
        (dotfile_dir / ".hgignore").write_text("*.tmp\n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "*~" in patterns
        assert "*.tmp" in patterns

    def test_skip_comments_and_empty_lines(self, tmp_path: Path) -> None:
        """Test that comments and empty lines are skipped."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("# Comment\n\n*~\n  \n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "*~" in patterns
        assert "# Comment" not in patterns
        assert "" not in patterns
        assert len(patterns) == 1

    def test_skip_negation_patterns(self, tmp_path: Path) -> None:
        """Test that negation patterns are skipped."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("*~\n!important~\n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "*~" in patterns
        assert "!important~" not in patterns

    def test_no_ignore_files(self, tmp_path: Path) -> None:
        """Test returns empty set when no ignore files exist."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert patterns == set()


class TestMatchesIgnorePattern:
    """Test matches_ignore_pattern function."""

    def test_matches_filename(self) -> None:
        """Test matching against filename."""
        patterns = {"*~", "*.bak"}

        assert df.matches_ignore_pattern(Path("file~"), patterns) is True
        assert df.matches_ignore_pattern(Path("file.bak"), patterns) is True
        assert df.matches_ignore_pattern(Path("file.txt"), patterns) is False

    def test_matches_nested_path(self) -> None:
        """Test matching against nested path filename."""
        patterns = {"*~"}

        assert df.matches_ignore_pattern(Path("dir/file~"), patterns) is True
        assert (
            df.matches_ignore_pattern(Path("dir/file.txt"), patterns) is False
        )

    def test_matches_directory_pattern(self) -> None:
        """Test matching against directory component."""
        patterns = {"__pycache__"}

        assert (
            df.matches_ignore_pattern(Path("__pycache__/module.pyc"), patterns)
            is True
        )
        assert df.matches_ignore_pattern(Path("src/main.py"), patterns) is False

    def test_matches_full_path(self) -> None:
        """Test matching against full path."""
        patterns = {"config/secret.txt"}

        assert (
            df.matches_ignore_pattern(Path("config/secret.txt"), patterns)
            is True
        )
        assert (
            df.matches_ignore_pattern(Path("other/secret.txt"), patterns)
            is False
        )

    def test_empty_patterns(self) -> None:
        """Test with empty patterns set."""
        patterns: set[str] = set()

        assert df.matches_ignore_pattern(Path("file~"), patterns) is False


class TestDiscoverDotfilesIgnoreFeatures:
    """Test discover_dotfiles ignore features."""

    def test_skips_root_dotfiles(self, tmp_path: Path) -> None:
        """Test that root-level dotfiles are skipped."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")
        (dotfile_dir / ".gitignore").write_text("*~")
        (dotfile_dir / ".hgignore").write_text("*.tmp")

        entries = df.discover_dotfiles(dotfile_dir)

        paths = {e.relative_path for e in entries}
        assert Path("vimrc") in paths
        assert Path(".gitignore") not in paths
        assert Path(".hgignore") not in paths

    def test_includes_nested_dotfiles(self, tmp_path: Path) -> None:
        """Test that nested dotfiles are NOT skipped."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "config").mkdir()
        (dotfile_dir / "config" / ".hidden").write_text("content")

        entries = df.discover_dotfiles(dotfile_dir)

        paths = {e.relative_path for e in entries}
        assert Path("config/.hidden") in paths

    def test_respects_gitignore_patterns(self, tmp_path: Path) -> None:
        """Test that .gitignore patterns are respected."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("*~\n*.bak\n")
        (dotfile_dir / "vimrc").write_text("content")
        (dotfile_dir / "vimrc~").write_text("backup")
        (dotfile_dir / "config.bak").write_text("backup")

        entries = df.discover_dotfiles(dotfile_dir)

        paths = {e.relative_path for e in entries}
        assert Path("vimrc") in paths
        assert Path("vimrc~") not in paths
        assert Path("config.bak") not in paths

    def test_respects_hgignore_patterns(self, tmp_path: Path) -> None:
        """Test that .hgignore patterns are respected."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".hgignore").write_text("*.tmp\n")
        (dotfile_dir / "vimrc").write_text("content")
        (dotfile_dir / "cache.tmp").write_text("temp")

        entries = df.discover_dotfiles(dotfile_dir)

        paths = {e.relative_path for e in entries}
        assert Path("vimrc") in paths
        assert Path("cache.tmp") not in paths

    def test_respects_ignore_patterns_in_subdirs(self, tmp_path: Path) -> None:
        """Test that ignore patterns work for files in subdirectories."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("*~\n")
        (dotfile_dir / "config").mkdir()
        (dotfile_dir / "config" / "settings").write_text("content")
        (dotfile_dir / "config" / "settings~").write_text("backup")

        entries = df.discover_dotfiles(dotfile_dir)

        paths = {e.relative_path for e in entries}
        assert Path("config/settings") in paths
        assert Path("config/settings~") not in paths


class TestCheckDotfileStatus:
    """Test check_dotfile_status function."""

    def test_status_missing(self, tmp_path: Path) -> None:
        """Test status when target doesn't exist."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            status = df.check_dotfile_status(entry)

        assert status == df.DotfileStatus.MISSING

    def test_status_ok(self, tmp_path: Path) -> None:
        """Test status when correctly linked."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            # Create correct symlink
            target = home / ".vimrc"
            target.symlink_to(os.path.relpath(dotfile_dir / "vimrc", home))

            status = df.check_dotfile_status(entry)

        assert status == df.DotfileStatus.OK

    def test_status_file_conflict(self, tmp_path: Path) -> None:
        """Test status when target is a regular file."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            # Create conflicting file
            (home / ".vimrc").write_text("conflict")

            status = df.check_dotfile_status(entry)

        assert status == df.DotfileStatus.FILE_CONFLICT

    def test_status_link_conflict(self, tmp_path: Path) -> None:
        """Test status when target is a symlink to wrong location."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            # Create conflicting symlink
            wrong_target = tmp_path / "wrong"
            wrong_target.write_text("wrong")
            (home / ".vimrc").symlink_to(wrong_target)

            status = df.check_dotfile_status(entry)

        assert status == df.DotfileStatus.LINK_CONFLICT


class TestInstallDotfile:
    """Test install_dotfile function."""

    def test_install_creates_symlink(self, tmp_path: Path) -> None:
        """Test that install creates a symlink."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            result = df.install_dotfile(entry)

        assert result.status == df.DotfileStatus.INSTALLED
        assert (home / ".vimrc").is_symlink()
        assert (home / ".vimrc").resolve() == (dotfile_dir / "vimrc").resolve()

    def test_install_creates_parent_directories(self, tmp_path: Path) -> None:
        """Test that install creates parent directories."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "config").mkdir()
        (dotfile_dir / "config" / "nvim").mkdir()
        (dotfile_dir / "config" / "nvim" / "init.vim").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("config/nvim/init.vim"),
                dotfile_dir=dotfile_dir,
            )
            result = df.install_dotfile(entry)

        assert result.status == df.DotfileStatus.INSTALLED
        assert (home / ".config" / "nvim" / "init.vim").is_symlink()

    def test_install_already_ok(self, tmp_path: Path) -> None:
        """Test install when already correctly linked."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            # Create correct symlink
            (home / ".vimrc").symlink_to(
                os.path.relpath(dotfile_dir / "vimrc", home)
            )

            result = df.install_dotfile(entry)

        assert result.status == df.DotfileStatus.OK

    def test_install_file_conflict(self, tmp_path: Path) -> None:
        """Test install with file conflict."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            # Create conflicting file
            (home / ".vimrc").write_text("conflict")

            result = df.install_dotfile(entry)

        assert result.status == df.DotfileStatus.FILE_CONFLICT

    def test_install_link_conflict_without_force(self, tmp_path: Path) -> None:
        """Test install with link conflict without force."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            # Create conflicting symlink
            wrong_target = tmp_path / "wrong"
            wrong_target.write_text("wrong")
            (home / ".vimrc").symlink_to(wrong_target)

            result = df.install_dotfile(entry, force=False)

        assert result.status == df.DotfileStatus.LINK_CONFLICT

    def test_install_link_conflict_with_force(self, tmp_path: Path) -> None:
        """Test install with link conflict with force."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            # Create conflicting symlink
            wrong_target = tmp_path / "wrong"
            wrong_target.write_text("wrong")
            (home / ".vimrc").symlink_to(wrong_target)

            result = df.install_dotfile(entry, force=True)

        assert result.status == df.DotfileStatus.INSTALLED
        assert (home / ".vimrc").resolve() == (dotfile_dir / "vimrc").resolve()

    def test_install_dry_run(self, tmp_path: Path) -> None:
        """Test install with dry_run doesn't create symlink."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            result = df.install_dotfile(entry, dry_run=True)

        assert result.status == df.DotfileStatus.INSTALLED
        assert not (home / ".vimrc").exists()


class TestRemoveDotfile:
    """Test remove_dotfile function."""

    def test_remove_correctly_linked(self, tmp_path: Path) -> None:
        """Test removing a correctly linked dotfile."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            # Create correct symlink
            (home / ".vimrc").symlink_to(
                os.path.relpath(dotfile_dir / "vimrc", home)
            )

            result = df.remove_dotfile(entry)

        assert result.status == df.DotfileStatus.REMOVED
        assert not (home / ".vimrc").exists()

    def test_remove_missing(self, tmp_path: Path) -> None:
        """Test removing when already missing."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            result = df.remove_dotfile(entry)

        assert result.status == df.DotfileStatus.MISSING

    def test_remove_file_conflict(self, tmp_path: Path) -> None:
        """Test remove leaves file conflicts alone."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            # Create conflicting file
            (home / ".vimrc").write_text("conflict")

            result = df.remove_dotfile(entry)

        assert result.status == df.DotfileStatus.FILE_CONFLICT
        assert (home / ".vimrc").exists()

    def test_remove_link_conflict(self, tmp_path: Path) -> None:
        """Test remove leaves link conflicts alone."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            # Create conflicting symlink
            wrong_target = tmp_path / "wrong"
            wrong_target.write_text("wrong")
            (home / ".vimrc").symlink_to(wrong_target)

            result = df.remove_dotfile(entry)

        assert result.status == df.DotfileStatus.LINK_CONFLICT
        assert (home / ".vimrc").is_symlink()

    def test_remove_dry_run(self, tmp_path: Path) -> None:
        """Test remove with dry_run doesn't delete."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            # Create correct symlink
            (home / ".vimrc").symlink_to(
                os.path.relpath(dotfile_dir / "vimrc", home)
            )

            result = df.remove_dotfile(entry, dry_run=True)

        assert result.status == df.DotfileStatus.REMOVED
        assert (home / ".vimrc").exists()

    def test_remove_cleans_empty_parents(self, tmp_path: Path) -> None:
        """Test remove cleans up empty parent directories."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "config").mkdir()
        (dotfile_dir / "config" / "app").mkdir()
        (dotfile_dir / "config" / "app" / "settings").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("config/app/settings"),
                dotfile_dir=dotfile_dir,
            )
            # Create directory structure and symlink
            (home / ".config" / "app").mkdir(parents=True)
            (home / ".config" / "app" / "settings").symlink_to(
                os.path.relpath(
                    dotfile_dir / "config" / "app" / "settings",
                    home / ".config" / "app",
                )
            )

            result = df.remove_dotfile(entry)

        assert result.status == df.DotfileStatus.REMOVED
        # Empty directories should be cleaned up
        assert not (home / ".config" / "app").exists()
        assert not (home / ".config").exists()


class TestAuditDotfile:
    """Test audit_dotfile function."""

    def test_audit_returns_status(self, tmp_path: Path) -> None:
        """Test audit returns current status."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            result = df.audit_dotfile(entry)

        assert result.status == df.DotfileStatus.MISSING
        # Should not have created anything
        assert not (home / ".vimrc").exists()


class TestProcessDirectory:
    """Test process_directory function."""

    def test_process_directory_install(self, tmp_path: Path) -> None:
        """Test processing directory for install."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")
        (dotfile_dir / "bashrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            results = df.process_directory(dotfile_dir, "install")

        assert len(results) == 2
        assert all(r.status == df.DotfileStatus.INSTALLED for r in results)

    def test_process_directory_remove(self, tmp_path: Path) -> None:
        """Test processing directory for remove."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            # First install
            df.process_directory(dotfile_dir, "install")
            # Then remove
            results = df.process_directory(dotfile_dir, "remove")

        assert len(results) == 1
        assert results[0].status == df.DotfileStatus.REMOVED

    def test_process_directory_audit(self, tmp_path: Path) -> None:
        """Test processing directory for audit."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            results = df.process_directory(dotfile_dir, "audit")

        assert len(results) == 1
        assert results[0].status == df.DotfileStatus.MISSING

    def test_process_directory_invalid_operation(self, tmp_path: Path) -> None:
        """Test processing with invalid operation raises."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with pytest.raises(ValueError, match="Unknown operation"):
            df.process_directory(dotfile_dir, "invalid")


class TestHasConflicts:
    """Test has_conflicts helper function."""

    def test_has_conflicts_true(self, tmp_path: Path) -> None:
        """Test has_conflicts returns True with conflicts."""
        entry = df.DotfileEntry(
            relative_path=Path("vimrc"),
            dotfile_dir=tmp_path,
        )
        results = [
            df.OperationResult(entry, df.DotfileStatus.INSTALLED),
            df.OperationResult(entry, df.DotfileStatus.FILE_CONFLICT),
        ]
        assert df.has_conflicts(results) is True

    def test_has_conflicts_false(self, tmp_path: Path) -> None:
        """Test has_conflicts returns False without conflicts."""
        entry = df.DotfileEntry(
            relative_path=Path("vimrc"),
            dotfile_dir=tmp_path,
        )
        results = [
            df.OperationResult(entry, df.DotfileStatus.INSTALLED),
            df.OperationResult(entry, df.DotfileStatus.OK),
        ]
        assert df.has_conflicts(results) is False


class TestHasMissing:
    """Test has_missing helper function."""

    def test_has_missing_true(self, tmp_path: Path) -> None:
        """Test has_missing returns True with missing."""
        entry = df.DotfileEntry(
            relative_path=Path("vimrc"),
            dotfile_dir=tmp_path,
        )
        results = [
            df.OperationResult(entry, df.DotfileStatus.OK),
            df.OperationResult(entry, df.DotfileStatus.MISSING),
        ]
        assert df.has_missing(results) is True

    def test_has_missing_false(self, tmp_path: Path) -> None:
        """Test has_missing returns False without missing."""
        entry = df.DotfileEntry(
            relative_path=Path("vimrc"),
            dotfile_dir=tmp_path,
        )
        results = [
            df.OperationResult(entry, df.DotfileStatus.OK),
            df.OperationResult(entry, df.DotfileStatus.INSTALLED),
        ]
        assert df.has_missing(results) is False


class TestDoInstall:
    """Test do_install function."""

    def test_do_install_specific_directory(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test installing from specific directory."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            exit_code = df.do_install(dotfile_dir)

        assert exit_code == 0
        assert (home / ".vimrc").is_symlink()
        # Should have recorded the directory
        assert dotfile_dir.resolve() in df.load_installed_directories()

    def test_do_install_all_directories(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test installing from all installed directories."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir1 = tmp_path / "dotfiles1"
        dotfile_dir1.mkdir()
        (dotfile_dir1 / "vimrc").write_text("content")
        dotfile_dir2 = tmp_path / "dotfiles2"
        dotfile_dir2.mkdir()
        (dotfile_dir2 / "bashrc").write_text("content")

        installed_file = tmp_path / ".dotfiles.installed"
        installed_file.write_text(
            f"{dotfile_dir1.resolve()}\n{dotfile_dir2.resolve()}\n"
        )
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            exit_code = df.do_install(None)

        assert exit_code == 0
        assert (home / ".vimrc").is_symlink()
        assert (home / ".bashrc").is_symlink()

    def test_do_install_no_directories(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test installing with no directories."""
        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        exit_code = df.do_install(None)

        assert exit_code == 0

    def test_do_install_with_conflicts(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test installing with conflicts returns 1."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")
        # Create conflict
        (home / ".vimrc").write_text("conflict")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            exit_code = df.do_install(dotfile_dir)

        assert exit_code == 1

    def test_do_install_dry_run(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Test install dry run doesn't modify anything."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            exit_code = df.do_install(dotfile_dir, dry_run=True)

        assert exit_code == 0
        assert not (home / ".vimrc").exists()
        # Should NOT have recorded the directory
        assert df.load_installed_directories() == []


class TestDoRemove:
    """Test do_remove function."""

    def test_do_remove_specific_directory(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test removing specific directory."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            # First install
            df.do_install(dotfile_dir)
            assert (home / ".vimrc").is_symlink()

            # Then remove
            exit_code = df.do_remove(dotfile_dir)

        assert exit_code == 0
        assert not (home / ".vimrc").exists()
        # Should have removed from installed list
        assert dotfile_dir.resolve() not in df.load_installed_directories()

    def test_do_remove_all_directories(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test removing all installed directories."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            # First install
            df.do_install(dotfile_dir)

            # Then remove all
            exit_code = df.do_remove(None)

        assert exit_code == 0
        assert not (home / ".vimrc").exists()
        assert df.load_installed_directories() == []

    def test_do_remove_nonexistent_directory(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test removing when directory no longer exists raises error."""
        nonexistent = tmp_path / "nonexistent"
        installed_file = tmp_path / ".dotfiles.installed"
        installed_file.write_text(f"{nonexistent}\n")
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with pytest.raises(df.MissingDotfilesDirectory, match="does not exist"):
            df.do_remove(None)


class TestDoAudit:
    """Test do_audit function."""

    def test_do_audit_all_ok(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Test audit when all dotfiles are correctly installed."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_install(dotfile_dir)
            exit_code = df.do_audit(dotfile_dir)

        assert exit_code == 0

    def test_do_audit_missing(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Test audit when dotfiles are missing."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        installed_file = tmp_path / ".dotfiles.installed"
        installed_file.write_text(f"{dotfile_dir.resolve()}\n")
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            exit_code = df.do_audit(None)

        assert exit_code == 1

    def test_do_audit_conflicts(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Test audit when there are conflicts."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")
        # Create conflict
        (home / ".vimrc").write_text("conflict")

        installed_file = tmp_path / ".dotfiles.installed"
        installed_file.write_text(f"{dotfile_dir.resolve()}\n")
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            exit_code = df.do_audit(None)

        assert exit_code == 1

    def test_do_audit_no_installed_file(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test audit returns 0 silently when no installed file exists."""
        installed_file = tmp_path / ".dotfiles.installed"
        # Don't create the file - it should not exist
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        exit_code = df.do_audit(None)

        assert exit_code == 0

    def test_do_audit_skips_nonexistent_directories(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test audit silently skips non-existent directories."""
        home = tmp_path / "home"
        home.mkdir()
        nonexistent = tmp_path / "nonexistent"
        # Don't create nonexistent - it should not exist

        installed_file = tmp_path / ".dotfiles.installed"
        installed_file.write_text(f"{nonexistent}\n")
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            exit_code = df.do_audit(None)

        # Should return 0, not raise an exception
        assert exit_code == 0

    def test_do_audit_mixed_existing_nonexistent(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test audit processes existing dirs and skips non-existent ones."""
        home = tmp_path / "home"
        home.mkdir()
        existing_dir = tmp_path / "existing"
        existing_dir.mkdir()
        (existing_dir / "vimrc").write_text("content")
        nonexistent = tmp_path / "nonexistent"
        # Don't create nonexistent

        installed_file = tmp_path / ".dotfiles.installed"
        installed_file.write_text(f"{nonexistent}\n{existing_dir.resolve()}\n")
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            # Install the existing one first
            (home / ".vimrc").symlink_to(
                os.path.relpath(existing_dir / "vimrc", home)
            )
            exit_code = df.do_audit(None)

        # Should return 0 (existing dir is OK, nonexistent is skipped)
        assert exit_code == 0


class TestDoSelfTest:
    """Test do_self_test function."""

    @patch("subprocess.run", autospec=True)
    def test_do_self_test_basic(self, mock_run: MagicMock) -> None:
        """Test do_self_test invokes the test file."""
        mock_run.return_value = MagicMock(returncode=0)

        df.do_self_test(verbose=False, coverage=False)

        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert cmd[0].endswith("test_dotfiles.py")

    @patch("subprocess.run", autospec=True)
    def test_do_self_test_with_verbose(self, mock_run: MagicMock) -> None:
        """Test do_self_test passes --verbose flag."""
        mock_run.return_value = MagicMock(returncode=0)

        df.do_self_test(verbose=True, coverage=False)

        cmd = mock_run.call_args[0][0]
        assert "--verbose" in cmd

    @patch("subprocess.run", autospec=True)
    def test_do_self_test_with_coverage(self, mock_run: MagicMock) -> None:
        """Test do_self_test passes --coverage flag."""
        mock_run.return_value = MagicMock(returncode=0)

        df.do_self_test(verbose=False, coverage=True)

        cmd = mock_run.call_args[0][0]
        assert "--coverage" in cmd

    @patch("subprocess.run", autospec=True)
    def test_do_self_test_raises_on_failure(self, mock_run: MagicMock) -> None:
        """Test do_self_test raises TestError on failure."""
        mock_run.return_value = MagicMock(returncode=1)

        with pytest.raises(df.TestError, match="Tests failed"):
            df.do_self_test(verbose=False, coverage=False)


class TestMain:
    """Test main function and command dispatch."""

    @patch.object(df, "do_self_test")
    def test_main_self_test_command(self, mock_do_self_test: MagicMock) -> None:
        """Test main dispatches to do_self_test."""
        args = argparse.Namespace(
            command="self-test", verbose=False, coverage=False
        )

        df.main(args)

        assert mock_do_self_test.called

    @patch.object(df, "do_install")
    def test_main_install_command(self, mock_do_install: MagicMock) -> None:
        """Test main dispatches to do_install."""
        mock_do_install.return_value = 0
        args = argparse.Namespace(
            command="install",
            directory=Path("/path"),
            dry_run=False,
            force=False,
        )

        df.main(args)

        assert mock_do_install.called

    @patch.object(df, "do_remove")
    def test_main_remove_command(self, mock_do_remove: MagicMock) -> None:
        """Test main dispatches to do_remove."""
        mock_do_remove.return_value = 0
        args = argparse.Namespace(
            command="remove", directory=Path("/path"), dry_run=False
        )

        df.main(args)

        assert mock_do_remove.called

    @patch.object(df, "do_audit")
    def test_main_audit_command(self, mock_do_audit: MagicMock) -> None:
        """Test main dispatches to do_audit."""
        mock_do_audit.return_value = 0
        args = argparse.Namespace(command="audit", directory=Path("/path"))

        df.main(args)

        assert mock_do_audit.called

    def test_main_no_command(self) -> None:
        """Test main raises UsageError if no subcommand."""
        args = argparse.Namespace(command=None)

        with pytest.raises(df.UsageError, match="No subcommand"):
            df.main(args)


class TestCli:
    """Test cli() function."""

    @patch.object(df, "main")
    def test_cli_returns_zero_on_success(self, mock_main: MagicMock) -> None:
        """Test cli() returns 0 when main() succeeds."""
        mock_main.return_value = 0
        with patch("sys.argv", ["prog", "audit"]):
            result = df.cli()
        assert result == 0

    @patch.object(df, "main")
    def test_cli_returns_main_exit_code(self, mock_main: MagicMock) -> None:
        """Test cli() returns main's exit code."""
        mock_main.return_value = 1
        with patch("sys.argv", ["prog", "audit"]):
            result = df.cli()
        assert result == 1

    @patch.object(df, "main")
    def test_cli_handles_usage_error(self, mock_main: MagicMock) -> None:
        """Test cli() catches UsageError and returns 2."""
        mock_main.side_effect = df.UsageError("test")
        with patch("sys.argv", ["prog", "audit"]):
            result = df.cli()
        assert result == 2

    @patch.object(df, "main")
    def test_cli_handles_missing_directory_error(
        self, mock_main: MagicMock
    ) -> None:
        """Test cli() catches MissingDotfilesDirectory and returns 3."""
        mock_main.side_effect = df.MissingDotfilesDirectory("test")
        with patch("sys.argv", ["prog", "audit"]):
            result = df.cli()
        assert result == 3

    @patch.object(df, "main")
    def test_cli_handles_test_error(self, mock_main: MagicMock) -> None:
        """Test cli() catches TestError and returns 4."""
        mock_main.side_effect = df.TestError("test")
        with patch("sys.argv", ["prog", "self-test"]):
            result = df.cli()
        assert result == 4

    def test_cli_help_returns_zero(self) -> None:
        """Test cli() returns 0 for --help."""
        with patch("sys.argv", ["prog", "--help"]):
            result = df.cli()
        assert result == 0


class TestMultipleDirectories:
    """Test scenarios with multiple dotfile directories."""

    def test_conflict_between_directories(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test that second directory conflicts if same file exists."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir1 = tmp_path / "dotfiles1"
        dotfile_dir1.mkdir()
        (dotfile_dir1 / "vimrc").write_text("content1")
        dotfile_dir2 = tmp_path / "dotfiles2"
        dotfile_dir2.mkdir()
        (dotfile_dir2 / "vimrc").write_text("content2")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            # Install first directory
            exit_code1 = df.do_install(dotfile_dir1)
            assert exit_code1 == 0

            # Install second directory - should conflict
            exit_code2 = df.do_install(dotfile_dir2)
            assert exit_code2 == 1

            # Original symlink should still point to first directory
            assert (home / ".vimrc").resolve() == (
                dotfile_dir1 / "vimrc"
            ).resolve()


class TestRelativeSymlinks:
    """Test that symlinks are created as relative paths."""

    def test_symlink_is_relative(self, tmp_path: Path) -> None:
        """Test that created symlink is relative."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        with patch.object(Path, "home", return_value=home):
            entry = df.DotfileEntry(
                relative_path=Path("vimrc"),
                dotfile_dir=dotfile_dir,
            )
            df.install_dotfile(entry)

            # Read the symlink target
            link_target = os.readlink(home / ".vimrc")

        # Should be relative, not absolute
        assert not Path(link_target).is_absolute()


class TestCodeQuality:
    """Test code quality with black, flake8, and mypy."""

    def test_black_compliance(self) -> None:
        """Test that code is formatted with black."""
        import subprocess

        result = subprocess.run(
            ["uvx", "black", "-l80", "--check", str(_script_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"black check failed:\n{result.stderr}"

    def test_black_compliance_tests(self) -> None:
        """Test that tests are formatted with black."""
        import subprocess

        result = subprocess.run(
            [
                "uvx",
                "black",
                "-l80",
                "--check",
                str(REPO_ROOT / "tests" / "test_dotfiles.py"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"black check failed:\n{result.stderr}"

    def test_flake8_compliance(self) -> None:
        """Test that code passes flake8."""
        import subprocess

        result = subprocess.run(
            ["uvx", "flake8", "--max-line-length=80", str(_script_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"flake8 check failed:\n{result.stdout}"

    def test_flake8_compliance_tests(self) -> None:
        """Test that tests pass flake8."""
        import subprocess

        result = subprocess.run(
            [
                "uvx",
                "flake8",
                "--max-line-length=80",
                str(REPO_ROOT / "tests" / "test_dotfiles.py"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"flake8 check failed:\n{result.stdout}"

    def test_mypy_compliance(self, tmp_path: Path) -> None:
        """Test that code passes mypy."""
        import subprocess

        cache_dir = tmp_path / "mypy_cache"
        result = subprocess.run(
            ["uvx", "mypy", "--cache-dir", str(cache_dir), str(_script_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"mypy check failed:\n{result.stdout}"

    def test_mypy_compliance_tests(self, tmp_path: Path) -> None:
        """Test that tests pass mypy."""
        import subprocess

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
                str(REPO_ROOT / "tests" / "test_dotfiles.py"),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"mypy check failed:\n{result.stdout}"


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
