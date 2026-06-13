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

import logging
import os
import sys
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from conftest import (
    CmdCallbacksBase,
    ExceptionHierarchyBase,
    IsolateHomeFixtureBase,
    UnknownArgRoutedToSubparserBase,
    isolate_home,
)

# Repository root directory (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import dotfiles as df  # noqa: E402

# The bin script under test, for run_tests' coverage module name.
_script_path = REPO_ROOT / "bin" / "dotfiles"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: Any) -> None:
    isolate_home(df, ".dotfiles.installed", tmp_path, monkeypatch)


class TestIsolateHomeFixture(IsolateHomeFixtureBase):
    MODULE: ClassVar[Any] = df
    SOURCE_NAME = "vimrc"
    PROFILE_ATTR = "DOTFILES_PROFILE"

    @staticmethod
    def _make_source(path: Path) -> None:
        path.write_text("content")


class TestArgumentParser:
    """Test argument parser structure."""

    def test_install_parses_directory(self) -> None:
        """Test install subcommand parses directory argument."""
        parser = df.build_parser()
        args = parser.parse_command(["install", "/path/to/dotfiles"])
        assert args.command == "install"
        assert args.directory == Path("/path/to/dotfiles")

    def test_install_parses_flags(self) -> None:
        """Test install subcommand parses flags."""
        parser = df.build_parser()
        args = parser.parse_command(["install", "--dry-run", "-f", "/path"])
        assert args.dry_run is True
        assert args.force is True

    def test_install_without_directory(self) -> None:
        """Test install subcommand without directory."""
        parser = df.build_parser()
        args = parser.parse_command(["install"])
        assert args.command == "install"
        assert args.directory is None

    def test_remove_parses_directory(self) -> None:
        """Test remove subcommand parses directory argument."""
        parser = df.build_parser()
        args = parser.parse_command(["remove", "/path/to/dotfiles"])
        assert args.command == "remove"
        assert args.directory == Path("/path/to/dotfiles")

    def test_audit_parses_directory(self) -> None:
        """Test audit subcommand parses directory argument."""
        parser = df.build_parser()
        args = parser.parse_command(["audit", "/path/to/dotfiles"])
        assert args.command == "audit"
        assert args.directory == Path("/path/to/dotfiles")

    def test_cleanup_parses(self) -> None:
        """Test cleanup subcommand parses --dry-run."""
        parser = df.build_parser()
        args = parser.parse_command(["cleanup", "--dry-run"])
        assert args.command == "cleanup"
        assert args.dry_run is True


class TestUnknownArgRoutedToSubparser(UnknownArgRoutedToSubparserBase):
    """Unknown args print the subcommand's usage line."""

    PARSER_FUNC = staticmethod(df.build_parser)
    CASES = [
        (["install", "--bogus"], "install"),
        (["remove", "--bogus"], "remove"),
        (["audit", "--bogus"], "audit"),
        (["cleanup", "--bogus"], "cleanup"),
    ]


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

    def test_load_dotfilesignore(self, tmp_path: Path) -> None:
        """Test loading patterns from .dotfilesignore. Trailing
        directory slashes are stripped at load time so fnmatch's
        path-component check works."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".dotfilesignore").write_text("tests/\nREADME.md\n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "tests" in patterns
        assert "README.md" in patterns

    def test_load_all_ignore_files(self, tmp_path: Path) -> None:
        """Patterns are merged from .gitignore, .hgignore, and
        .dotfilesignore."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("*~\n")
        (dotfile_dir / ".hgignore").write_text("*.tmp\n")
        (dotfile_dir / ".dotfilesignore").write_text("tests/\n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "*~" in patterns
        assert "*.tmp" in patterns
        assert "tests" in patterns

    def test_strips_trailing_slash_from_directory_patterns(
        self, tmp_path: Path
    ) -> None:
        """gitignore-style 'foo/' is loaded as 'foo' so the
        path-component matcher works."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("__pycache__/\nbuild/\n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "__pycache__" in patterns
        assert "build" in patterns
        assert "__pycache__/" not in patterns

    def test_lone_slash_pattern_not_collapsed_to_empty(
        self, tmp_path: Path
    ) -> None:
        """A line containing only '/' is not stripped to the empty
        string (which would fnmatch-match nothing useful and pollute
        the pattern set)."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("/\n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "" not in patterns
        assert "/" in patterns

    def test_skip_comments_and_empty_lines(self, tmp_path: Path) -> None:
        """Test that comments and empty lines are skipped."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("# Comment\n\n*~\n  \n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "*~" in patterns
        assert "# Comment" not in patterns
        assert "" not in patterns

    def test_skip_negation_patterns(self, tmp_path: Path) -> None:
        """Test that negation patterns are skipped."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("*~\n!important~\n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "*~" in patterns
        assert "!important~" not in patterns

    def test_no_ignore_files_returns_hardcoded(self, tmp_path: Path) -> None:
        """With no ignore files, only the hardcoded fallback applies."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert patterns == df.HARDCODED_IGNORE_PATTERNS

    def test_hardcoded_patterns_always_present(self, tmp_path: Path) -> None:
        """Hardcoded editor / swap patterns are always included."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("custom.bak\n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        for hardcoded in {"*~", "*.swp", "*.swo", ".*.swp", "#*#"}:
            assert hardcoded in patterns
        assert "custom.bak" in patterns

    def test_walk_falls_back_to_home_when_no_repo_root(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """When no enclosing repo root exists below $HOME, the walk
        falls back to stopping at $HOME inclusive so ancestor patterns
        like the user's editor-backup globs still apply."""
        fake_home = tmp_path / "home"
        repo = fake_home / "utils"
        bin_dir = repo / "bin"
        bin_dir.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        (fake_home / ".gitignore").write_text("home_pattern\n")
        (repo / ".gitignore").write_text("repo_pattern\n")

        patterns = df.load_ignore_patterns(bin_dir)

        assert "home_pattern" in patterns
        assert "repo_pattern" in patterns

    def test_walk_stops_at_enclosing_repo_root(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """The walk stops at (and includes) the enclosing repo root,
        so ~/.gitignore-equivalent files above the repo are not
        consulted -- avoiding the gitignore-anchoring confusion that
        would come from flat-merging $HOME-level patterns into a
        dotfile_dir-relative matcher."""
        fake_home = tmp_path / "home"
        repo = fake_home / "utils"
        bin_dir = repo / "bin"
        bin_dir.mkdir(parents=True)
        (repo / ".git").mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        (fake_home / ".gitignore").write_text("home_pattern\n")
        (repo / ".gitignore").write_text("repo_pattern\n")

        patterns = df.load_ignore_patterns(bin_dir)

        assert "repo_pattern" in patterns
        assert "home_pattern" not in patterns

    def test_walk_stops_at_dotfile_dir_when_it_is_repo_root(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """If dotfile_dir is itself the repo root, no ancestor is
        consulted -- the typical `dotfiles install <repo>` shape."""
        fake_home = tmp_path / "home"
        repo = fake_home / "dotfiles"
        repo.mkdir(parents=True)
        (repo / ".git").mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        (fake_home / ".gitignore").write_text("home_pattern\n")
        (repo / ".gitignore").write_text("repo_pattern\n")

        patterns = df.load_ignore_patterns(repo)

        assert "repo_pattern" in patterns
        assert "home_pattern" not in patterns

    def test_walk_recognizes_hg_repo_root(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """An '.hg' marker bounds the walk the same way '.git' does."""
        fake_home = tmp_path / "home"
        repo = fake_home / "hg-utils"
        bin_dir = repo / "bin"
        bin_dir.mkdir(parents=True)
        (repo / ".hg").mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        (fake_home / ".hgignore").write_text("home_pattern\n")
        (repo / ".hgignore").write_text("repo_pattern\n")

        patterns = df.load_ignore_patterns(bin_dir)

        assert "repo_pattern" in patterns
        assert "home_pattern" not in patterns

    def test_walk_recognizes_git_file_marker(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """In a git worktree or submodule, '.git' is a regular file
        containing a 'gitdir:' pointer rather than a directory. The
        walk treats it as a repo-root marker the same way."""
        fake_home = tmp_path / "home"
        repo = fake_home / "worktree"
        bin_dir = repo / "bin"
        bin_dir.mkdir(parents=True)
        (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        (fake_home / ".gitignore").write_text("home_pattern\n")
        (repo / ".gitignore").write_text("repo_pattern\n")

        patterns = df.load_ignore_patterns(bin_dir)

        assert "repo_pattern" in patterns
        assert "home_pattern" not in patterns

    def test_does_not_walk_above_home(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Patterns above $HOME are not collected."""
        fake_home = tmp_path / "home"
        repo = fake_home / "utils"
        repo.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        (tmp_path / ".gitignore").write_text("above_home\n")

        patterns = df.load_ignore_patterns(repo)

        assert "above_home" not in patterns

    def test_unescapes_gitignore_hash_escape(self, tmp_path: Path) -> None:
        """Gitignore '\\#' / '\\!' escapes are translated for fnmatch."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".gitignore").write_text("\\#*\\#\n\\!literal\n")

        patterns = df.load_ignore_patterns(dotfile_dir)

        assert "#*#" in patterns
        assert "!literal" in patterns
        assert df.matches_ignore_pattern(Path("#main.c#"), patterns) is True

    def test_outside_home_does_not_walk(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """When dotfile_dir is outside $HOME, only dotfile_dir is checked."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        (tmp_path / ".gitignore").write_text("ancestor_pattern\n")
        (outside / ".gitignore").write_text("local_pattern\n")

        patterns = df.load_ignore_patterns(outside)

        assert "local_pattern" in patterns
        assert "ancestor_pattern" not in patterns


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

    def test_anchored_bare_name_matches_only_root(self) -> None:
        """A leading '/' anchors the pattern to dotfile_dir root, so
        '/CLAUDE.md' excludes the top-level file but not a nested
        'claude/CLAUDE.md'."""
        patterns = {"/CLAUDE.md"}

        assert df.matches_ignore_pattern(Path("CLAUDE.md"), patterns) is True
        assert (
            df.matches_ignore_pattern(Path("claude/CLAUDE.md"), patterns)
            is False
        )

    def test_anchored_directory_matches_only_root(self) -> None:
        """An anchored bare-name pattern also matches when the root
        entry is a directory: '/_repo_shared' excludes everything
        under the root-level _repo_shared/ but leaves a nested
        'sub/_repo_shared/...' alone."""
        patterns = {"/_repo_shared"}

        assert (
            df.matches_ignore_pattern(
                Path("_repo_shared/tests/foo.py"), patterns
            )
            is True
        )
        assert (
            df.matches_ignore_pattern(Path("sub/_repo_shared/foo.py"), patterns)
            is False
        )

    def test_anchored_multi_segment_matches_full_path(self) -> None:
        """An anchored multi-segment pattern matches the full
        relative path, not a same-named tail elsewhere."""
        patterns = {"/foo/bar"}

        assert df.matches_ignore_pattern(Path("foo/bar"), patterns) is True
        assert df.matches_ignore_pattern(Path("baz/foo/bar"), patterns) is False

    def test_anchored_multi_segment_excludes_directory_contents(
        self,
    ) -> None:
        """An anchored multi-segment pattern also excludes everything
        under the named path -- '/foo/bar' must skip 'foo/bar/baz' the
        same way the bare-name branch '/foo' would, otherwise a
        directory pattern like '/some/subdir/' silently leaves its
        contents linkable."""
        patterns = {"/foo/bar"}

        assert df.matches_ignore_pattern(Path("foo/bar/baz"), patterns) is True
        assert (
            df.matches_ignore_pattern(Path("foo/bar/baz/qux"), patterns) is True
        )

    def test_unanchored_still_matches_at_any_depth(self) -> None:
        """A bare 'CLAUDE.md' (no leading slash) keeps the gitignore
        default of matching at any depth."""
        patterns = {"CLAUDE.md"}

        assert df.matches_ignore_pattern(Path("CLAUDE.md"), patterns) is True
        assert (
            df.matches_ignore_pattern(Path("claude/CLAUDE.md"), patterns)
            is True
        )


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

    def test_respects_dotfilesignore_patterns(self, tmp_path: Path) -> None:
        """A repo can keep tests/ tracked in git but excluded from
        linking by listing it in .dotfilesignore."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".dotfilesignore").write_text(
            "tests/\nREADME.md\n.github\n"
        )
        (dotfile_dir / "vimrc").write_text("content")
        (dotfile_dir / "README.md").write_text("docs")
        (dotfile_dir / "tests").mkdir()
        (dotfile_dir / "tests" / "test_format.py").write_text("test")
        (dotfile_dir / ".github").mkdir()
        (dotfile_dir / ".github" / "workflow.yml").write_text("ci")

        entries = df.discover_dotfiles(dotfile_dir)

        paths = {e.relative_path for e in entries}
        assert Path("vimrc") in paths
        assert Path("README.md") not in paths
        assert Path("tests/test_format.py") not in paths
        assert Path(".github/workflow.yml") not in paths

    def test_anchored_dotfilesignore_excludes_root_only(
        self, tmp_path: Path
    ) -> None:
        """A leading '/' in .dotfilesignore anchors the pattern: the
        root CLAUDE.md is skipped but claude/CLAUDE.md is linked,
        which is how the dotfiles repo's own dev docs coexist with
        per-tool config under claude/."""
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / ".dotfilesignore").write_text("/CLAUDE.md\n")
        (dotfile_dir / "CLAUDE.md").write_text("repo dev doc")
        (dotfile_dir / "claude").mkdir()
        (dotfile_dir / "claude" / "CLAUDE.md").write_text("tool config")

        entries = df.discover_dotfiles(dotfile_dir)

        paths = {e.relative_path for e in entries}
        assert Path("CLAUDE.md") not in paths
        assert Path("claude/CLAUDE.md") in paths

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


class TestLogResults:
    """Test log_results output formatting (suppress-ok summary,
    verbose mode, color toggling)."""

    @pytest.fixture(autouse=True)
    def _no_color(self, monkeypatch: Any) -> None:
        """Force color off in this test class so assertions can
        compare against plain status text."""
        monkeypatch.setenv("NO_COLOR", "1")

    def test_default_all_ok_collapses_to_summary(self, caplog: Any) -> None:
        """When every entry is ok the dir gets a single ': ok' line
        and per-entry rows are suppressed."""
        dotfile_dir = Path("/dotfiles")
        results = [
            df.OperationResult(
                df.DotfileEntry(Path("vimrc"), dotfile_dir),
                df.DotfileStatus.OK,
            ),
            df.OperationResult(
                df.DotfileEntry(Path("zshrc"), dotfile_dir),
                df.DotfileStatus.OK,
            ),
        ]
        with caplog.at_level(logging.INFO, logger="dotfiles"):
            df.log_results(
                dotfile_dir,
                results,
                verbose=False,
                problem_statuses=df._AUDIT_PROBLEM_STATUSES,
            )
        assert [r.getMessage() for r in caplog.records] == ["/dotfiles: ok"]

    def test_default_suppresses_ok_lines_keeps_problems(
        self, caplog: Any
    ) -> None:
        """With some non-ok entries, only those entries are printed
        under the dir header -- ok lines are suppressed."""
        dotfile_dir = Path("/dotfiles")
        results = [
            df.OperationResult(
                df.DotfileEntry(Path("vimrc"), dotfile_dir),
                df.DotfileStatus.OK,
            ),
            df.OperationResult(
                df.DotfileEntry(Path("zshrc"), dotfile_dir),
                df.DotfileStatus.LINK_CONFLICT,
            ),
        ]
        with caplog.at_level(logging.INFO, logger="dotfiles"):
            df.log_results(
                dotfile_dir,
                results,
                verbose=False,
                problem_statuses=df._AUDIT_PROBLEM_STATUSES,
            )
        assert [r.getMessage() for r in caplog.records] == [
            "/dotfiles: error",
            "    zshrc: link-conflict",
        ]

    def test_install_action_lines_print_with_ok_header(
        self, caplog: Any
    ) -> None:
        """A successful install with newly-linked entries reports the
        actions per-line but the dir header stays 'ok' -- INSTALLED
        is a state change, not a problem the user needs to fix."""
        dotfile_dir = Path("/dotfiles")
        results = [
            df.OperationResult(
                df.DotfileEntry(Path("vimrc"), dotfile_dir),
                df.DotfileStatus.OK,
            ),
            df.OperationResult(
                df.DotfileEntry(Path("claude/X.md"), dotfile_dir),
                df.DotfileStatus.INSTALLED,
            ),
        ]
        with caplog.at_level(logging.INFO, logger="dotfiles"):
            df.log_results(
                dotfile_dir,
                results,
                verbose=False,
                problem_statuses=df._INSTALL_PROBLEM_STATUSES,
            )
        assert [r.getMessage() for r in caplog.records] == [
            "/dotfiles: ok",
            "    claude/X.md: installed",
        ]

    def test_install_problem_flips_header_to_error(self, caplog: Any) -> None:
        """A LINK_CONFLICT during install flips the header to error
        and lists both the conflict and any state-change actions."""
        dotfile_dir = Path("/dotfiles")
        results = [
            df.OperationResult(
                df.DotfileEntry(Path("a"), dotfile_dir),
                df.DotfileStatus.INSTALLED,
            ),
            df.OperationResult(
                df.DotfileEntry(Path("b"), dotfile_dir),
                df.DotfileStatus.LINK_CONFLICT,
            ),
        ]
        with caplog.at_level(logging.INFO, logger="dotfiles"):
            df.log_results(
                dotfile_dir,
                results,
                verbose=False,
                problem_statuses=df._INSTALL_PROBLEM_STATUSES,
            )
        assert [r.getMessage() for r in caplog.records] == [
            "/dotfiles: error",
            "    a: installed",
            "    b: link-conflict",
        ]

    def test_remove_missing_does_not_flip_header(self, caplog: Any) -> None:
        """remove of a target that's already gone reports MISSING
        but stays 'ok' at the header -- there's nothing to fix."""
        dotfile_dir = Path("/dotfiles")
        results = [
            df.OperationResult(
                df.DotfileEntry(Path("a"), dotfile_dir),
                df.DotfileStatus.MISSING,
            ),
        ]
        with caplog.at_level(logging.INFO, logger="dotfiles"):
            df.log_results(
                dotfile_dir,
                results,
                verbose=False,
                problem_statuses=df._REMOVE_PROBLEM_STATUSES,
            )
        assert [r.getMessage() for r in caplog.records] == [
            "/dotfiles: ok",
            "    a: missing",
        ]

    def test_audit_missing_flips_header_to_error(self, caplog: Any) -> None:
        """audit, by contrast, treats MISSING as a problem -- the
        target tree diverges from the discovered set and audit can't
        fix it."""
        dotfile_dir = Path("/dotfiles")
        results = [
            df.OperationResult(
                df.DotfileEntry(Path("a"), dotfile_dir),
                df.DotfileStatus.MISSING,
            ),
        ]
        with caplog.at_level(logging.INFO, logger="dotfiles"):
            df.log_results(
                dotfile_dir,
                results,
                verbose=False,
                problem_statuses=df._AUDIT_PROBLEM_STATUSES,
            )
        assert [r.getMessage() for r in caplog.records] == [
            "/dotfiles: error",
            "    a: missing",
        ]

    def test_verbose_prints_every_entry(self, caplog: Any) -> None:
        """verbose=True restores the per-entry view for every
        result, ok included."""
        dotfile_dir = Path("/dotfiles")
        results = [
            df.OperationResult(
                df.DotfileEntry(Path("vimrc"), dotfile_dir),
                df.DotfileStatus.OK,
            ),
            df.OperationResult(
                df.DotfileEntry(Path("zshrc"), dotfile_dir),
                df.DotfileStatus.LINK_CONFLICT,
            ),
        ]
        with caplog.at_level(logging.INFO, logger="dotfiles"):
            df.log_results(
                dotfile_dir,
                results,
                verbose=True,
                problem_statuses=df._AUDIT_PROBLEM_STATUSES,
            )
        assert [r.getMessage() for r in caplog.records] == [
            "/dotfiles: error",
            "    vimrc: ok",
            "    zshrc: link-conflict",
        ]

    def test_default_empty_results_collapses_to_summary(
        self, caplog: Any
    ) -> None:
        """A dir with no discovered entries also collapses to ': ok'."""
        with caplog.at_level(logging.INFO, logger="dotfiles"):
            df.log_results(
                Path("/dotfiles"),
                [],
                verbose=False,
                problem_statuses=df._AUDIT_PROBLEM_STATUSES,
            )
        assert [r.getMessage() for r in caplog.records] == ["/dotfiles: ok"]


class TestStatusColor:
    """Test the ANSI-color helpers used by log_results."""

    def test_color_off_when_not_tty(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        assert df._color_supported() is False

    def test_color_off_when_no_color_env_set(self, monkeypatch: Any) -> None:
        """NO_COLOR (https://no-color.org/) wins even on a TTY."""
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        assert df._color_supported() is False

    def test_color_on_when_tty_and_no_color_unset(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        assert df._color_supported() is True

    def test_format_status_non_problem_green_when_supported(
        self, monkeypatch: Any
    ) -> None:
        """A status outside problem_statuses prints green even if it
        isn't OK -- INSTALLED in the install context, for example."""
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        ok = df._format_status(
            df.DotfileStatus.OK, df._INSTALL_PROBLEM_STATUSES
        )
        installed = df._format_status(
            df.DotfileStatus.INSTALLED, df._INSTALL_PROBLEM_STATUSES
        )
        assert ok == "\033[32mok\033[0m"
        assert installed == "\033[32minstalled\033[0m"

    def test_format_status_problem_red_when_supported(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        text = df._format_status(
            df.DotfileStatus.LINK_CONFLICT, df._INSTALL_PROBLEM_STATUSES
        )
        assert text == "\033[31mlink-conflict\033[0m"

    def test_format_status_plain_when_color_off(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        assert (
            df._format_status(df.DotfileStatus.OK, df._INSTALL_PROBLEM_STATUSES)
            == "ok"
        )
        assert (
            df._format_status(
                df.DotfileStatus.LINK_CONFLICT, df._INSTALL_PROBLEM_STATUSES
            )
            == "link-conflict"
        )


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
            df.do_install(
                dotfile_dir, dry_run=False, force=False, verbose=False
            )

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
            df.do_install(None, dry_run=False, force=False, verbose=False)

        assert (home / ".vimrc").is_symlink()
        assert (home / ".bashrc").is_symlink()

    def test_do_install_no_directories(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test installing with no directories."""
        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        df.do_install(None, dry_run=False, force=False, verbose=False)

    def test_do_install_missing_directory_does_not_record(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """A failed install must not leave the bogus path in
        ~/.dotfiles.installed."""
        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with pytest.raises(df.MissingDotfilesDirectory):
            df.do_install(
                tmp_path / "does-not-exist",
                dry_run=False,
                force=False,
                verbose=False,
            )

        assert df.load_installed_directories() == []

    def test_do_install_file_as_directory_does_not_record(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """A path that exists but is a regular file (not a directory)
        is also rejected before being recorded -- discover_dotfiles
        would raise the same exception, and recording it would
        poison every subsequent 'install' run-without-args."""
        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)
        regular_file = tmp_path / "not-a-dir"
        regular_file.write_text("oops")

        with pytest.raises(df.MissingDotfilesDirectory):
            df.do_install(
                regular_file, dry_run=False, force=False, verbose=False
            )

        assert df.load_installed_directories() == []

    def test_do_install_with_conflicts(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test installing with conflicts raises ConflictsFound."""
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
            with pytest.raises(df.ConflictsFound):
                df.do_install(
                    dotfile_dir,
                    dry_run=False,
                    force=False,
                    verbose=False,
                )

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
            df.do_install(dotfile_dir, dry_run=True, force=False, verbose=False)

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
            df.do_install(
                dotfile_dir, dry_run=False, force=False, verbose=False
            )
            assert (home / ".vimrc").is_symlink()

            # Then remove
            df.do_remove(dotfile_dir, dry_run=False, verbose=False)

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
            df.do_install(
                dotfile_dir, dry_run=False, force=False, verbose=False
            )

            # Then remove all
            df.do_remove(None, dry_run=False, verbose=False)

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
            df.do_remove(None, dry_run=False, verbose=False)


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
            df.do_install(
                dotfile_dir, dry_run=False, force=False, verbose=False
            )
            df.do_audit(dotfile_dir, verbose=False)

    def test_do_audit_missing(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Test audit when dotfiles are missing raises ConflictsFound."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")

        installed_file = tmp_path / ".dotfiles.installed"
        installed_file.write_text(f"{dotfile_dir.resolve()}\n")
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            with pytest.raises(df.ConflictsFound):
                df.do_audit(None, verbose=False)

    def test_do_audit_conflicts(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Test audit when there are conflicts raises ConflictsFound."""
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
            with pytest.raises(df.ConflictsFound):
                df.do_audit(None, verbose=False)

    def test_do_audit_no_installed_file(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test audit succeeds silently when no installed file exists."""
        installed_file = tmp_path / ".dotfiles.installed"
        # Don't create the file - it should not exist
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        df.do_audit(None, verbose=False)

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
            df.do_audit(None, verbose=False)

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
            df.do_audit(None, verbose=False)


class TestDoCleanup:
    """Test do_cleanup function for the dotfiles profile."""

    def test_cleanup_removes_dangling_link_at_target_root(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Dangling symlinks directly under $HOME are removed."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".broken").symlink_to(tmp_path / "nonexistent")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_cleanup(dry_run=False)

        assert not (home / ".broken").is_symlink()

    def test_cleanup_removes_dangling_link_in_nested_target_dir(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Dangling links in dirs the source currently delivers into."""
        home = tmp_path / "home"
        home.mkdir()
        nested = home / ".config" / "app"
        nested.mkdir(parents=True)
        # Source delivers config/app/settings, so .config/app is a scan dir
        src = tmp_path / "dotfiles"
        src.mkdir()
        (src / "config" / "app").mkdir(parents=True)
        (src / "config" / "app" / "settings").write_text("content")
        (nested / "old_setting").symlink_to(src / "deleted")

        installed_file = tmp_path / ".dotfiles.installed"
        installed_file.write_text(f"{src.resolve()}\n")
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_cleanup(dry_run=False)

        assert not (nested / "old_setting").is_symlink()

    def test_cleanup_leaves_valid_link(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Non-dangling symlinks are not touched."""
        home = tmp_path / "home"
        home.mkdir()
        target = tmp_path / "real"
        target.write_text("content")
        (home / ".vimrc").symlink_to(target)

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_cleanup(dry_run=False)

        assert (home / ".vimrc").is_symlink()
        assert (home / ".vimrc").resolve() == target.resolve()

    def test_cleanup_leaves_regular_files(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Regular files are not touched."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".bashrc").write_text("content")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_cleanup(dry_run=False)

        assert (home / ".bashrc").exists()
        assert not (home / ".bashrc").is_symlink()

    def test_cleanup_dry_run_keeps_dangling_link(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Dry-run reports but does not remove."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".broken").symlink_to(tmp_path / "nonexistent")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_cleanup(dry_run=True)

        assert (home / ".broken").is_symlink()

    def test_cleanup_skips_nonexistent_source_dir(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Cleanup does not raise when an installed source dir is gone."""
        home = tmp_path / "home"
        home.mkdir()
        nonexistent = tmp_path / "nonexistent"

        installed_file = tmp_path / ".dotfiles.installed"
        installed_file.write_text(f"{nonexistent}\n")
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_cleanup(dry_run=False)

    def test_cleanup_removes_dangling_link_unrelated_to_dotfiles(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Any dangling symlink in a scan dir is removed, even if it
        was not created by dotfiles."""
        home = tmp_path / "home"
        home.mkdir()
        # Dangling, points outside the source dir entirely
        (home / ".unrelated").symlink_to("/some/random/missing/path")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_cleanup(dry_run=False)

        assert not (home / ".unrelated").is_symlink()


class TestStaleLinkDetection:
    """audit / cleanup / install identify and (where appropriate)
    remove managed symlinks whose source path is no longer in the
    discovered set -- e.g. the file was removed from the repo or now
    matches .gitignore / .hgignore / .dotfilesignore."""

    @staticmethod
    def _setup_repo_with_stale_link(
        tmp_path: Path, monkeypatch: Any, ignore_via: str = "delete"
    ) -> tuple[Path, Path]:
        """Install a repo with two files, then either delete one or
        add it to .dotfilesignore. Returns (home, dotfile_dir)."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")
        (dotfile_dir / "bashrc").write_text("content")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_install(
                dotfile_dir, dry_run=False, force=False, verbose=False
            )

        assert (home / ".vimrc").is_symlink()
        assert (home / ".bashrc").is_symlink()

        if ignore_via == "delete":
            (dotfile_dir / "bashrc").unlink()
        elif ignore_via == "dotfilesignore":
            (dotfile_dir / ".dotfilesignore").write_text("bashrc\n")
        elif ignore_via == "gitignore":
            (dotfile_dir / ".gitignore").write_text("bashrc\n")
        else:
            raise ValueError(ignore_via)
        return home, dotfile_dir

    def test_audit_flags_stale_when_source_deleted(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """audit reports STALE for a managed symlink whose source
        file was removed from the repo."""
        home, dotfile_dir = self._setup_repo_with_stale_link(
            tmp_path, monkeypatch, ignore_via="delete"
        )
        with patch.object(Path, "home", return_value=home):
            with pytest.raises(df.ConflictsFound):
                df.do_audit(dotfile_dir, verbose=False)
        # Audit reports but does not act -- the stale link is still there.
        assert (home / ".bashrc").is_symlink()

    def test_audit_flags_stale_via_dotfilesignore(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """audit reports STALE when the source file is now matched by
        .dotfilesignore."""
        home, dotfile_dir = self._setup_repo_with_stale_link(
            tmp_path, monkeypatch, ignore_via="dotfilesignore"
        )
        with patch.object(Path, "home", return_value=home):
            with pytest.raises(df.ConflictsFound):
                df.do_audit(dotfile_dir, verbose=False)

    def test_audit_flags_stale_via_gitignore(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """audit reports STALE when the source file is now matched by
        .gitignore (managed link semantics also apply to .gitignore
        adds)."""
        home, dotfile_dir = self._setup_repo_with_stale_link(
            tmp_path, monkeypatch, ignore_via="gitignore"
        )
        with patch.object(Path, "home", return_value=home):
            with pytest.raises(df.ConflictsFound):
                df.do_audit(dotfile_dir, verbose=False)

    def test_audit_does_not_flag_unmanaged_links(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """A symlink under target_root pointing outside every
        installed source dir is not flagged."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        dotfile_dir.mkdir()
        (dotfile_dir / "vimrc").write_text("content")
        # Pre-existing user link, not managed.
        unrelated = tmp_path / "elsewhere"
        unrelated.write_text("user data")
        (home / ".user_link").symlink_to(unrelated)

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_install(
                dotfile_dir, dry_run=False, force=False, verbose=False
            )
            df.do_audit(dotfile_dir, verbose=False)
        # The unmanaged link survives.
        assert (home / ".user_link").is_symlink()

    def test_cleanup_removes_stale_managed_link(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """cleanup removes a managed link whose source was removed."""
        home, _ = self._setup_repo_with_stale_link(
            tmp_path, monkeypatch, ignore_via="delete"
        )
        with patch.object(Path, "home", return_value=home):
            df.do_cleanup(dry_run=False)
        assert not (home / ".bashrc").is_symlink()
        # Still-valid managed link is left alone.
        assert (home / ".vimrc").is_symlink()

    def test_cleanup_removes_stale_via_dotfilesignore(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """cleanup removes a managed link when source is ignored."""
        home, _ = self._setup_repo_with_stale_link(
            tmp_path, monkeypatch, ignore_via="dotfilesignore"
        )
        with patch.object(Path, "home", return_value=home):
            df.do_cleanup(dry_run=False)
        assert not (home / ".bashrc").is_symlink()
        assert (home / ".vimrc").is_symlink()

    def test_cleanup_dry_run_keeps_stale_link(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """dry-run cleanup does not remove stale managed links."""
        home, _ = self._setup_repo_with_stale_link(
            tmp_path, monkeypatch, ignore_via="delete"
        )
        with patch.object(Path, "home", return_value=home):
            df.do_cleanup(dry_run=True)
        assert (home / ".bashrc").is_symlink()

    def test_install_prunes_stale_link_when_source_deleted(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """A second install call after the source file is deleted
        prunes the now-stale managed link."""
        home, dotfile_dir = self._setup_repo_with_stale_link(
            tmp_path, monkeypatch, ignore_via="delete"
        )
        with patch.object(Path, "home", return_value=home):
            df.do_install(
                dotfile_dir, dry_run=False, force=False, verbose=False
            )
        assert not (home / ".bashrc").is_symlink()
        assert (home / ".vimrc").is_symlink()

    def test_install_prunes_stale_via_dotfilesignore(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """install prunes a managed link newly matched by
        .dotfilesignore."""
        home, dotfile_dir = self._setup_repo_with_stale_link(
            tmp_path, monkeypatch, ignore_via="dotfilesignore"
        )
        with patch.object(Path, "home", return_value=home):
            df.do_install(
                dotfile_dir, dry_run=False, force=False, verbose=False
            )
        assert not (home / ".bashrc").is_symlink()

    def test_install_dry_run_does_not_prune(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """dry-run install does not unlink stale managed links."""
        home, dotfile_dir = self._setup_repo_with_stale_link(
            tmp_path, monkeypatch, ignore_via="delete"
        )
        with patch.object(Path, "home", return_value=home):
            df.do_install(dotfile_dir, dry_run=True, force=False, verbose=False)
        assert (home / ".bashrc").is_symlink()

    def test_install_does_not_touch_other_repos_links(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """install of repo A does not prune stale links owned by
        repo B."""
        home = tmp_path / "home"
        home.mkdir()
        repo_a = tmp_path / "repo_a"
        repo_a.mkdir()
        (repo_a / "vimrc").write_text("a")
        repo_b = tmp_path / "repo_b"
        repo_b.mkdir()
        (repo_b / "bashrc").write_text("b")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_install(repo_a, dry_run=False, force=False, verbose=False)
            df.do_install(repo_b, dry_run=False, force=False, verbose=False)
            # Make repo_b's bashrc stale.
            (repo_b / "bashrc").unlink()
            # Re-install repo_a only -- repo_b's stale link should
            # survive because it's not in repo_a's blast radius.
            df.do_install(repo_a, dry_run=False, force=False, verbose=False)

        assert (home / ".vimrc").is_symlink()
        assert (home / ".bashrc").is_symlink()

    def test_install_keeps_link_when_source_is_symlink_to_excluded(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """A discovered entry that is itself a symlink within the
        repo (e.g. claude/SHARED.md -> ../_repo_shared/files/SHARED.md
        when _repo_shared/ is excluded by .dotfilesignore) installs
        normally and is NOT then removed by stale-link cleanup.

        Without resolving the immediate link target only, stale
        detection followed the chain into the excluded path and
        unlinked the just-installed file -- so 'install' would
        report 'installed' and leave nothing on disk."""
        home = tmp_path / "home"
        home.mkdir()
        dotfile_dir = tmp_path / "dotfiles"
        (dotfile_dir / "claude").mkdir(parents=True)
        (dotfile_dir / "_repo_shared" / "files").mkdir(parents=True)
        (dotfile_dir / "_repo_shared" / "files" / "SHARED.md").write_text(
            "shared"
        )
        (dotfile_dir / "claude" / "SHARED.md").symlink_to(
            Path("..") / "_repo_shared" / "files" / "SHARED.md"
        )
        (dotfile_dir / ".dotfilesignore").write_text("/_repo_shared/\n")

        installed_file = tmp_path / ".dotfiles.installed"
        monkeypatch.setattr(df, "INSTALLED_FILE", installed_file)

        with patch.object(Path, "home", return_value=home):
            df.do_install(
                dotfile_dir, dry_run=False, force=False, verbose=False
            )

        installed_link = home / ".claude" / "SHARED.md"
        assert installed_link.is_symlink()
        assert installed_link.exists()


class TestCmdCallbacks(CmdCallbacksBase):
    """Test command callback dispatch table."""

    CALLBACKS = df.COMMAND_CALLBACKS
    PARSER_FUNC = df.build_parser
    CLI_FUNC = staticmethod(df.cli)
    EXIT_CODE_USAGE = df.ExitCode.USAGE
    TEST_SUBCOMMAND = "audit"
    EXCEPTION_EXIT_CODE_MAP = [
        (df.ConflictsFound("t"), df.ExitCode.CONFLICTS),
        (df.UsageError("t"), df.ExitCode.USAGE),
        (
            df.MissingDotfilesDirectory("t"),
            df.ExitCode.MISSING_DIR,
        ),
        (RuntimeError("t"), df.ExitCode.CRASHED),
    ]


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
            df.do_install(
                dotfile_dir1, dry_run=False, force=False, verbose=False
            )

            # Install second directory - should conflict
            with pytest.raises(df.ConflictsFound):
                df.do_install(
                    dotfile_dir2, dry_run=False, force=False, verbose=False
                )

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


class TestExceptionHierarchy(ExceptionHierarchyBase):
    BASE_ERROR = df.DotfilesError
    EXIT_CODE = df.ExitCode
    EXCLUDED_CODES = {
        df.ExitCode.SUCCESS,
        df.ExitCode.WARNING,
        df.ExitCode.CONFIG,
        df.ExitCode.SUBPROCESS,
        df.ExitCode.CRASHED,
    }


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
