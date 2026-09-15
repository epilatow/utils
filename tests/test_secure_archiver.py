#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest", "pytest-cov", "tomlkit", "pydantic>=2"]
# ///
# This is AI generated code

"""
Comprehensive unit tests for secure_archiver
"""

import json
import re
import subprocess
import sys
import tomllib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock, call, patch

import pytest
from conftest import (
    CmdCallbacksBase,
    ExceptionHierarchyBase,
    HelpWidthBase,
    UnknownArgRoutedToSubparserBase,
)
from crony_automate_base import CronyAutomateBase

# Repository root directory (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import crony.cli as crony_cli  # noqa: E402
import secure_archiver as sa  # noqa: E402

# The bin script under test, for run_tests' coverage module name.
_script_path = REPO_ROOT / "bin" / "secure-archiver"


class TestConfigFinding:
    """Test configuration file finding logic."""

    def test_explicit_config_takes_priority(self, tmp_path: Path) -> None:
        """Test that explicit config path has highest priority."""
        config_file = tmp_path / "explicit.toml"
        config_file.write_text("[general]\n")

        result = sa.find_config(config_file)
        assert result == config_file

    def test_explicit_config_not_found_raises_error(
        self, tmp_path: Path
    ) -> None:
        """Test error when explicit config doesn't exist."""
        config_file = tmp_path / "nonexistent.toml"
        with pytest.raises(sa.ConfigError, match="Config file not found"):
            sa.find_config(config_file)

    def test_cwd_config_found(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Test that CWD config is found when present."""
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / sa.DEFAULT_CONFIG_NAME
        config_file.write_text("[general]\n")

        result = sa.find_config()
        assert result == config_file

    def test_home_dotfile_fallback(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test fallback to home directory dotfile."""
        # Create a temporary home directory
        home = tmp_path / "home"
        home.mkdir()
        dotfile = home / ".secure-archiver.toml"
        dotfile.write_text("[general]\n")

        # Create a different CWD without config
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setattr(Path, "home", lambda: home)

        result = sa.find_config()
        assert result == dotfile

    def test_no_config_found_raises_error(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Test error when no config file found."""
        # Create empty directories
        home = tmp_path / "home"
        home.mkdir()
        cwd = tmp_path / "cwd"
        cwd.mkdir()

        monkeypatch.chdir(cwd)
        monkeypatch.setattr(Path, "home", lambda: home)

        with pytest.raises(sa.ConfigError, match="No config file found"):
            sa.find_config()


class TestFilePatternExpansion:
    """Test file pattern expansion and glob matching."""

    def test_expand_simple_path(self, tmp_path: Path) -> None:
        """Test expanding a simple file path."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        result = sa.expand_pattern(str(test_file))
        assert result == [test_file]

    def test_expand_glob_pattern(self, tmp_path: Path) -> None:
        """Test expanding a glob pattern."""
        (tmp_path / "file1.txt").write_text("1")
        (tmp_path / "file2.txt").write_text("2")
        (tmp_path / "other.pdf").write_text("3")

        result = sa.expand_pattern(str(tmp_path / "*.txt"))
        assert len(result) == 2
        assert all(p.suffix == ".txt" for p in result)

    def test_expand_recursive_pattern(self, tmp_path: Path) -> None:
        """Test expanding a recursive glob pattern."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (tmp_path / "root.txt").write_text("r")
        (subdir / "nested.txt").write_text("n")

        result = sa.expand_pattern(str(tmp_path / "**/*.txt"))
        assert len(result) == 2

    def test_expand_with_tilde(self) -> None:
        """Test expansion of ~ in path."""
        result = sa.expand_pattern("~/test*.txt")
        # Result should be absolute paths starting from home
        assert all(p.is_absolute() for p in result)

    def test_expand_skips_hidden_names(self, tmp_path: Path) -> None:
        """Test a leading-dot name is not matched by a bare wildcard."""
        (tmp_path / "shown.txt").write_text("s")
        (tmp_path / ".hidden.txt").write_text("h")

        result = sa.expand_pattern(str(tmp_path / "*.txt"))
        assert result == [tmp_path / "shown.txt"]

    def test_expand_skips_hidden_dirs_when_recursive(
        self, tmp_path: Path
    ) -> None:
        """Test a recursive match does not descend into hidden dirs."""
        shown = tmp_path / "shown"
        shown.mkdir()
        (shown / "a.txt").write_text("a")
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "b.txt").write_text("b")

        result = sa.expand_pattern(str(tmp_path / "**/*.txt"))
        assert result == [shown / "a.txt"]

    def test_expand_unreadable_subtree_raises(self, tmp_path: Path) -> None:
        """Test a recursive match raises on a subtree it cannot read.

        The failure this guards is silent subtraction: the unreadable
        subtree would otherwise drop out of the result and publish a
        revision missing its files, indistinguishable from an edit.
        """
        readable = tmp_path / "ok"
        readable.mkdir()
        (readable / "a.pdf").write_text("a")
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "b.pdf").write_text("b")
        locked.chmod(0o000)

        try:
            with pytest.raises(sa.UnreadableError, match="Cannot read"):
                sa.expand_pattern(str(tmp_path / "**/*.pdf"))
        finally:
            locked.chmod(0o755)

    def test_expand_unreadable_mid_level_raises(self, tmp_path: Path) -> None:
        """Test a multi-level match raises below its scan root."""
        car = tmp_path / "car"
        car.mkdir()
        (car / "title.pdf").write_text("t")
        car.chmod(0o000)

        try:
            with pytest.raises(sa.UnreadableError, match="Cannot read"):
                sa.expand_pattern(str(tmp_path / "*/*.pdf"))
        finally:
            car.chmod(0o755)

    def test_expand_ignores_unreadable_dir_off_the_path(
        self, tmp_path: Path
    ) -> None:
        """Test a directory no component selects is never read.

        Reporting one would fail an archive over a directory that could
        not have held a match, which is a worse fault than the silence
        it replaced.
        """
        wanted = tmp_path / "abc"
        wanted.mkdir()
        (wanted / "title.pdf").write_text("t")
        unrelated = tmp_path / "zzz"
        unrelated.mkdir()
        unrelated.chmod(0o000)

        try:
            result = sa.expand_pattern(str(tmp_path / "a*/*.pdf"))
        finally:
            unrelated.chmod(0o755)

        assert result == [wanted / "title.pdf"]

    def test_expand_unreadable_dir_before_literal_leaf_raises(
        self, tmp_path: Path
    ) -> None:
        """Test a literal leaf under a magic component cannot go silent.

        Resolving the leaf is a stat, and the stat fails for every name
        inside a directory the OS refuses. Answering "not there" to that
        would drop the subtree from the archive without a word.
        """
        for name in ("car", "house"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "title.pdf").write_text("t")
        (tmp_path / "car").chmod(0o000)

        try:
            with pytest.raises(sa.UnreadableError, match="Cannot read"):
                sa.expand_pattern(str(tmp_path / "*/title.pdf"))
        finally:
            (tmp_path / "car").chmod(0o755)

    def test_expand_unreadable_dir_before_literal_dir_raises(
        self, tmp_path: Path
    ) -> None:
        """Test the dirs-only shape of the same case also raises."""
        for name in ("alpha", "beta"):
            (tmp_path / name / "invoices").mkdir(parents=True)
        (tmp_path / "alpha").chmod(0o000)

        try:
            with pytest.raises(sa.UnreadableError, match="Cannot read"):
                sa.expand_pattern(f"{tmp_path}/*/invoices/")
        finally:
            (tmp_path / "alpha").chmod(0o755)

    def test_expand_missing_intermediate_is_not_an_error(
        self, tmp_path: Path
    ) -> None:
        """Test a matched directory lacking the next component is skipped."""
        has = tmp_path / "has" / "sub"
        has.mkdir(parents=True)
        (has / "a.pdf").write_text("a")
        (tmp_path / "lacks").mkdir()

        result = sa.expand_pattern(str(tmp_path / "*/sub/*.pdf"))
        assert result == [has / "a.pdf"]

    def test_expand_redundant_separator_still_matches(
        self, tmp_path: Path
    ) -> None:
        """Test a doubled separator does not silently match nothing."""
        (tmp_path / "a.pdf").write_text("a")

        assert sa.expand_pattern(f"{tmp_path}//*.pdf") == [tmp_path / "a.pdf"]

    def test_expand_recursive_on_missing_base_matches_nothing(
        self, tmp_path: Path
    ) -> None:
        """Test a `**` pattern does not match a base that is not there.

        A trailing `**` matches the directory it starts from, which
        would otherwise report a match against a tree that does not
        exist and rob the caller of the missing-directory diagnosis.
        """
        missing = tmp_path / "gone"

        assert sa.expand_pattern(str(missing / "**")) == []
        with pytest.raises(sa.NotFoundError) as excinfo:
            sa.include_entry_to_sources(
                sa.PathIncludeEntry(path=str(missing / "**"))
            )
        assert f"Directory does not exist: {missing}" in str(excinfo.value)

    def test_expand_deduplicates_multiply_reachable_paths(
        self, tmp_path: Path
    ) -> None:
        """Test a path several routes can reach is returned once.

        `glob` returns it once per route, which staging would reject as
        a name collision.
        """
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        (nested / "z.pdf").write_text("z")

        result = sa.expand_pattern(str(tmp_path / "**/**/*.pdf"))
        assert result == [nested / "z.pdf"]

    def test_expand_bare_recursive_is_not_the_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test a pattern of nothing but `**` does not match the cwd."""
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "f.pdf").write_text("f")
        monkeypatch.chdir(tmp_path)

        assert Path(".") not in sa.expand_pattern("**")

    def test_expand_symlink_cycle_raises(self, tmp_path: Path) -> None:
        """Test a directory symlink cycle is reported, not walked forever.

        One of two places the match deliberately parts from `glob`, which
        yields the same file once per level until the OS refuses the path
        length.
        """
        real = tmp_path / "a"
        real.mkdir()
        (real / "f.pdf").write_text("f")
        (real / "loop").symlink_to(real)

        with pytest.raises(sa.UnreadableError, match="Cannot read"):
            sa.expand_pattern(str(tmp_path / "**/*.pdf"))

    def test_expand_multi_level_pattern(self, tmp_path: Path) -> None:
        """Test a magic component below the scan root still matches."""
        car = tmp_path / "car"
        car.mkdir()
        (car / "title.pdf").write_text("t")
        (car / "other.txt").write_text("o")
        (tmp_path / "top.pdf").write_text("x")

        result = sa.expand_pattern(str(tmp_path / "*/*.pdf"))
        assert result == [car / "title.pdf"]

    def test_expand_reaches_explicitly_named_hidden_dir(
        self, tmp_path: Path
    ) -> None:
        """Test a pattern naming a hidden component still descends."""
        hidden = tmp_path / "a" / ".secret"
        hidden.mkdir(parents=True)
        (hidden / "k.pdf").write_text("k")

        result = sa.expand_pattern(str(tmp_path / "*/.secret/*.pdf"))
        assert result == [hidden / "k.pdf"]

    # Pattern shapes the equivalence below is asserted over. `glob` is the
    # reference for matching; only its error handling is being replaced.
    GLOB_EQUIVALENCE_PATTERNS: ClassVar[list[str]] = [
        "*.pdf",
        "*",
        "**/*.pdf",
        "**",
        "a/*.pdf",
        "*/*.pdf",
        "*/*/*.pdf",
        "[at]*.pdf",
        "?op.pdf",
        "a/**/*.pdf",
        "*/",
        "**/",
        "dir.*",
        "nope*.pdf",
        "**/*",
        "*/b/*.pdf",
        "**/**/*.pdf",
        "a/b/**",
        "*/*/",
        "[!a]*",
        ".*",
        ".hid/*",
        "a/b/.*",
        "*/.hid2/*",
        "**/.x/*",
        "e */*.pdf",
        "**/c/*",
        "./*.pdf",
        "a/./*.pdf",
        "/a/*.pdf",
        "a//*.pdf",
        "a/b/../*.pdf",
        "a/",
        "top.pdf/",
        "*/.hid/[.]x.pdf",
        "**/.hid/[.]x.pdf",
        ".hid/[.]x.pdf",
        ".hid/.*",
        "nodir/*.pdf",
        "a/nodir/*.pdf",
        "*/nodir/*.pdf",
        "nodir/**",
        "nodir/**/",
        "**/**",
        "top.pdf/**",
        "a/**/",
        "*/**",
    ]

    def test_matches_glob_exactly(self, tmp_path: Path) -> None:
        """Test the matcher selects the same paths as `glob` does.

        Matching is reimplemented only to keep the OSErrors `glob`
        discards, so which paths are selected has to stay `glob`'s
        answer. Sets are compared because the two deliberately differ on
        multiplicity: a pattern with two `**` components makes `glob`
        emit a path once per way of reaching it, which staging would
        then reject as a collision.
        """
        import glob as glob_module

        for d in ["a/b/c", ".hid", "a/.hid2", "dir.pdf", "e f", "a/b/.x"]:
            (tmp_path / d).mkdir(parents=True, exist_ok=True)
        for f in [
            "top.pdf",
            "top.txt",
            ".dot.pdf",
            "a/x.pdf",
            "a/y.txt",
            "a/b/z.pdf",
            "a/b/c/deep.pdf",
            ".hid/h.pdf",
            ".hid/.x.pdf",
            "a/.hid2/h2.pdf",
            "e f/g.pdf",
            "a/b/.x/hid.pdf",
        ]:
            (tmp_path / f).write_text("x")
        (tmp_path / "link_to_a").symlink_to(tmp_path / "a")
        (tmp_path / "broken_link").symlink_to(tmp_path / "missing")

        for pattern in self.GLOB_EQUIVALENCE_PATTERNS:
            # Joined as text, not through `Path`, which would normalise
            # away the `.` and `//` shapes these patterns exist to cover.
            full = f"{tmp_path}/{pattern}"
            expected = sorted(
                {Path(m) for m in glob_module.glob(full, recursive=True)}
            )
            assert sorted(set(sa.expand_pattern(full))) == expected, pattern


class TestPatternScanRoot:
    """Test which directory a pattern's matches are read from."""

    def test_root_of_glob_pattern(self) -> None:
        """Deepest ancestor free of glob metacharacters."""
        assert sa.pattern_scan_root("/a/b/*_stmt.pdf") == Path("/a/b")

    def test_root_of_multi_level_glob(self) -> None:
        """Magic in a middle component stops the walk there."""
        assert sa.pattern_scan_root("/a/b/*/c*.pdf") == Path("/a/b")

    def test_root_of_recursive_glob(self) -> None:
        """A ** component is magic like any other."""
        assert sa.pattern_scan_root("/a/b/**/c.pdf") == Path("/a/b")

    def test_root_of_character_class(self) -> None:
        """A [...] class marks its component as a pattern."""
        assert sa.pattern_scan_root("/a/b/f[0-9].pdf") == Path("/a/b")

    def test_root_of_literal_path_is_parent(self) -> None:
        """A literal path is found by reading its parent."""
        assert sa.pattern_scan_root("/a/b/c") == Path("/a/b")

    def test_root_of_bare_name_is_cwd(self) -> None:
        """A relative bare name is found by reading the cwd."""
        assert sa.pattern_scan_root("c.pdf") == Path(".")

    def test_root_of_bare_pattern_is_cwd(self) -> None:
        """A relative bare pattern is matched against the cwd."""
        assert sa.pattern_scan_root("*.pdf") == Path(".")


class TestSplitPattern:
    """Test the literal start point a pattern's search descends from."""

    def test_splits_at_first_magic_component(self) -> None:
        """The literal run becomes the base, the rest stays to match."""
        assert sa.split_pattern("/a/b/*.pdf") == (Path("/a/b"), ("*.pdf",))

    def test_keeps_components_below_magic(self) -> None:
        """Components after the first magic one are all still to match."""
        assert sa.split_pattern("/a/*/c/*.pdf") == (
            Path("/a"),
            ("*", "c", "*.pdf"),
        )

    def test_normalizes_redundant_separators(self) -> None:
        """A doubled separator does not change the split.

        Path expansion can produce one whenever a configured value ends
        in a separator, and the search must not care.
        """
        assert sa.split_pattern("/a//b/*.pdf") == (Path("/a/b"), ("*.pdf",))

    def test_normalizes_dot_components(self) -> None:
        """A `.` component is dropped rather than searched for."""
        assert sa.split_pattern("/a/./b/*.pdf") == (Path("/a/b"), ("*.pdf",))

    def test_literal_pattern_has_nothing_left(self) -> None:
        """A pattern with no magic is entirely its own base."""
        assert sa.split_pattern("/a/b/c") == (Path("/a/b/c"), ())


class TestDirEntryCount:
    """Test the directory readability probe."""

    def test_counts_entries(self, tmp_path: Path) -> None:
        """Test counting a readable directory's entries."""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        (tmp_path / "sub").mkdir()

        assert sa.count_dir_entries(tmp_path) == 3

    def test_counts_empty_directory(self, tmp_path: Path) -> None:
        """Test an empty directory counts zero rather than raising."""
        assert sa.count_dir_entries(tmp_path) == 0

    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        """Test a missing directory raises rather than counting zero.

        Absence is a path to correct, so it is reported as not found
        rather than as storage the machine refused.
        """
        with pytest.raises(sa.NotFoundError, match="does not exist"):
            sa.count_dir_entries(tmp_path / "nope")

    def test_unreadable_directory_raises(self, tmp_path: Path) -> None:
        """Test an unreadable directory raises rather than counting zero."""
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "file.txt").write_text("content")
        locked.chmod(0o000)

        try:
            with pytest.raises(sa.UnreadableError, match="Cannot read"):
                sa.count_dir_entries(locked)
        finally:
            locked.chmod(0o755)


class TestDirectoryIteration:
    """Test directory iteration with recursion options."""

    def test_iter_files_non_recursive(self, tmp_path: Path) -> None:
        """Test non-recursive file iteration."""
        (tmp_path / "file1.txt").write_text("1")
        (tmp_path / "file2.txt").write_text("2")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("n")

        result = list(sa.iter_files_in_dir(tmp_path, recurse=False))
        assert len(result) == 2
        assert all(p.parent == tmp_path for p in result)

    def test_iter_files_recursive(self, tmp_path: Path) -> None:
        """Test recursive file iteration."""
        (tmp_path / "file1.txt").write_text("1")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("n")

        result = list(sa.iter_files_in_dir(tmp_path, recurse=True))
        assert len(result) == 2

    def test_iter_files_excludes_directories(self, tmp_path: Path) -> None:
        """Test that directories are excluded from iteration."""
        (tmp_path / "file.txt").write_text("1")
        (tmp_path / "subdir").mkdir()

        result = list(sa.iter_files_in_dir(tmp_path, recurse=False))
        assert len(result) == 1
        assert result[0].name == "file.txt"

    def test_iter_files_unreadable_dir_raises(self, tmp_path: Path) -> None:
        """Test an unreadable directory raises rather than yielding none."""
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "file.txt").write_text("1")
        locked.chmod(0o000)

        try:
            with pytest.raises(sa.UnreadableError, match="Cannot read"):
                list(sa.iter_files_in_dir(locked, recurse=False))
        finally:
            locked.chmod(0o755)

    def test_iter_files_unreadable_subtree_raises(self, tmp_path: Path) -> None:
        """Test a recursive walk raises on a subtree it cannot read."""
        (tmp_path / "file1.txt").write_text("1")
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "nested.txt").write_text("n")
        locked.chmod(0o000)

        try:
            with pytest.raises(sa.UnreadableError, match="Cannot read"):
                list(sa.iter_files_in_dir(tmp_path, recurse=True))
        finally:
            locked.chmod(0o755)


class TestFileStaging:
    """Test file staging functionality."""

    def test_stage_flat_copy_basic(self, tmp_path: Path) -> None:
        """Test basic flat file staging."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()

        src_file = src_dir / "test.txt"
        src_file.write_text("content")

        seen: set[str] = set()
        result = sa.stage_flat_copy(staging, [src_file], seen_names=seen)

        assert result == ["test.txt"]
        assert "test.txt" in seen
        assert (staging / "test.txt").exists()
        assert (staging / "test.txt").read_text() == "content"

    def test_stage_flat_copy_collision_raises_error(
        self, tmp_path: Path
    ) -> None:
        """Test that name collisions raise an error."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()

        file1 = src_dir / "dir1" / "test.txt"
        file1.parent.mkdir()
        file1.write_text("1")

        file2 = src_dir / "dir2" / "test.txt"
        file2.parent.mkdir()
        file2.write_text("2")

        seen: set[str] = set()
        with pytest.raises(sa.CollisionError, match="Name collision"):
            sa.stage_flat_copy(staging, [file1, file2], seen_names=seen)

    def test_stage_flat_copy_permissions(self, tmp_path: Path) -> None:
        """Test that staged files have correct permissions."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()

        src_file = src_dir / "test.txt"
        src_file.write_text("content")

        seen: set[str] = set()
        sa.stage_flat_copy(staging, [src_file], seen_names=seen)

        staged_file = staging / "test.txt"
        mode = staged_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_stage_flat_copy_nonexistent_file_raises_error(
        self, tmp_path: Path
    ) -> None:
        """Test error when source file doesn't exist."""
        staging = tmp_path / "staging"
        staging.mkdir()

        nonexistent = tmp_path / "nonexistent.txt"

        seen: set[str] = set()
        with pytest.raises(sa.ConfigError, match="does not exist"):
            sa.stage_flat_copy(staging, [nonexistent], seen_names=seen)

    def test_stage_flat_copy_with_target_dir(self, tmp_path: Path) -> None:
        """Test staging files into a subdirectory."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()

        src_file = src_dir / "test.txt"
        src_file.write_text("content")

        seen: set[str] = set()
        result = sa.stage_flat_copy(
            staging, [src_file], seen_names=seen, target_dir="subdir"
        )

        assert result == ["subdir/test.txt"]
        assert "subdir/test.txt" in seen
        assert (staging / "subdir" / "test.txt").exists()
        assert (staging / "subdir" / "test.txt").read_text() == "content"

    def test_stage_flat_copy_same_filename_different_dirs(
        self, tmp_path: Path
    ) -> None:
        """Test that same filename in different dirs doesn't collide."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()

        file1 = src_dir / "file1.txt"
        file1.write_text("content1")
        file2 = src_dir / "file2.txt"
        file2.write_text("content2")

        seen: set[str] = set()
        # Stage file1.txt in dir1
        sa.stage_flat_copy(staging, [file1], seen_names=seen, target_dir="dir1")
        # Rename to test.txt for this test
        (staging / "dir1" / "file1.txt").rename(staging / "dir1" / "test.txt")
        seen.clear()
        seen.add("dir1/test.txt")

        # Stage file2.txt as test.txt in dir2 - should not collide
        sa.stage_flat_copy(staging, [file2], seen_names=seen, target_dir="dir2")
        (staging / "dir2" / "file2.txt").rename(staging / "dir2" / "test.txt")

        assert (staging / "dir1" / "test.txt").exists()
        assert (staging / "dir2" / "test.txt").exists()

    def test_stage_flat_copy_collision_in_same_subdir(
        self, tmp_path: Path
    ) -> None:
        """Test that collision is detected within same subdirectory."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()

        file1 = src_dir / "dir1" / "test.txt"
        file1.parent.mkdir()
        file1.write_text("1")

        file2 = src_dir / "dir2" / "test.txt"
        file2.parent.mkdir()
        file2.write_text("2")

        seen: set[str] = set()
        with pytest.raises(sa.CollisionError, match="Name collision"):
            sa.stage_flat_copy(
                staging, [file1, file2], seen_names=seen, target_dir="subdir"
            )


class TestStageOpRefWithDir:
    """Test stage_op_ref with target_dir parameter."""

    def test_stage_op_ref_with_target_dir(self, tmp_path: Path) -> None:
        """Test staging 1Password content into a subdirectory."""
        staging = tmp_path / "staging"
        staging.mkdir()

        seen: set[str] = set()
        result = sa.stage_op_ref(
            staging,
            "secret.txt",
            "op://vault/item/field",
            content="secret content\n",
            seen_names=seen,
            target_dir="secrets",
        )

        assert result == "secrets/secret.txt"
        assert "secrets/secret.txt" in seen
        assert (staging / "secrets" / "secret.txt").exists()
        content = (staging / "secrets" / "secret.txt").read_text()
        assert content == "secret content\n"

    def test_stage_op_ref_same_filename_different_dirs(
        self, tmp_path: Path
    ) -> None:
        """Test same filename in different dirs doesn't collide for op_ref."""
        staging = tmp_path / "staging"
        staging.mkdir()

        seen: set[str] = set()
        sa.stage_op_ref(
            staging,
            "secret.txt",
            "op://vault/item1/field",
            content="content1\n",
            seen_names=seen,
            target_dir="dir1",
        )
        sa.stage_op_ref(
            staging,
            "secret.txt",
            "op://vault/item2/field",
            content="content2\n",
            seen_names=seen,
            target_dir="dir2",
        )

        assert (staging / "dir1" / "secret.txt").exists()
        assert (staging / "dir2" / "secret.txt").exists()
        assert "dir1/secret.txt" in seen
        assert "dir2/secret.txt" in seen


class TestIncludeEntryProcessing:
    """Test include entry to sources conversion."""

    def test_include_file_path(self, tmp_path: Path) -> None:
        """Test including a direct file path."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        entry = sa.PathIncludeEntry(path=str(test_file))
        result = sa.include_entry_to_sources(entry)

        assert result == [test_file]

    def test_include_directory_non_recursive(self, tmp_path: Path) -> None:
        """Test including directory without recursion."""
        (tmp_path / "file1.txt").write_text("1")
        (tmp_path / "file2.txt").write_text("2")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("n")

        entry = sa.PathIncludeEntry(path=str(tmp_path), recurse=False)
        result = sa.include_entry_to_sources(entry)

        assert len(result) == 2
        assert all(p.parent == tmp_path for p in result)

    def test_include_directory_recursive(self, tmp_path: Path) -> None:
        """Test including directory with recursion."""
        (tmp_path / "file1.txt").write_text("1")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("n")

        entry = sa.PathIncludeEntry(path=str(tmp_path), recurse=True)
        result = sa.include_entry_to_sources(entry)

        assert len(result) == 2

    def test_include_glob_pattern(self, tmp_path: Path) -> None:
        """Test including files via glob pattern."""
        (tmp_path / "file1.txt").write_text("1")
        (tmp_path / "file2.txt").write_text("2")
        (tmp_path / "other.pdf").write_text("3")

        entry = sa.PathIncludeEntry(path=str(tmp_path / "*.txt"))
        result = sa.include_entry_to_sources(entry)

        assert len(result) == 2
        assert all(p.suffix == ".txt" for p in result)

    def test_include_latest_option(self, tmp_path: Path) -> None:
        """Test latest option selects last file alphabetically."""
        (tmp_path / "aaa.txt").write_text("a")
        (tmp_path / "bbb.txt").write_text("b")
        (tmp_path / "zzz.txt").write_text("z")

        entry = sa.PathIncludeEntry(path=str(tmp_path / "*.txt"), latest=True)
        result = sa.include_entry_to_sources(entry)

        assert len(result) == 1
        assert result[0].name == "zzz.txt"

    def test_include_no_matches_raises_error(self, tmp_path: Path) -> None:
        """Test error when pattern matches nothing in a readable dir."""
        (tmp_path / "other.pdf").write_text("p")
        entry = sa.PathIncludeEntry(path=str(tmp_path / "nonexistent*.txt"))

        with pytest.raises(sa.NotFoundError) as excinfo:
            sa.include_entry_to_sources(entry)

        # The readable-and-empty case must be distinguishable from a
        # directory the OS refused, so it reports what it verified.
        message = str(excinfo.value)
        assert "No files matched" in message
        assert f"directory {tmp_path} is readable, 1 entry)" in message

    def test_include_unreadable_dir_raises_error(self, tmp_path: Path) -> None:
        """Test an unreadable directory is not reported as no matches."""
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "a.txt").write_text("a")
        locked.chmod(0o000)

        entry = sa.PathIncludeEntry(path=str(locked / "*.txt"))

        try:
            with pytest.raises(sa.UnreadableError) as excinfo:
                sa.include_entry_to_sources(entry)
        finally:
            locked.chmod(0o755)

        message = str(excinfo.value)
        assert f"Cannot read {locked}" in message
        assert "No files matched" not in message

    def test_include_missing_dir_raises_error(self, tmp_path: Path) -> None:
        """Test a missing parent directory reports itself, not the glob.

        A path that is not there is the config's problem, so it is a
        not-found rather than the general error a refusal raises.
        """
        missing = tmp_path / "gone"
        entry = sa.PathIncludeEntry(path=str(missing / "*.txt"))

        with pytest.raises(sa.NotFoundError) as excinfo:
            sa.include_entry_to_sources(entry)

        assert f"Directory does not exist: {missing}" in str(excinfo.value)

    def test_include_refused_match_is_not_reported_as_neither(
        self, tmp_path: Path
    ) -> None:
        """Test a match the OS refuses is reported as a refusal.

        Sorting a match into file or directory is two stats, and both
        fail for a symlink whose target sits under a refused directory.
        Falling through to "neither file nor directory" would blame the
        path for what the machine did.
        """
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "target.txt").write_text("t")
        link = tmp_path / "link.txt"
        link.symlink_to(locked / "target.txt")
        locked.chmod(0o000)

        entry = sa.PathIncludeEntry(path=str(link))

        try:
            with pytest.raises(sa.UnreadableError) as excinfo:
                sa.include_entry_to_sources(entry)
        finally:
            locked.chmod(0o755)

        message = str(excinfo.value)
        assert "Cannot read" in message
        assert "neither file nor directory" not in message

    def test_include_zero_files_raises_error(self, tmp_path: Path) -> None:
        """Test error when match results in zero files."""
        # Create empty directory
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        entry = sa.PathIncludeEntry(path=str(empty_dir))

        with pytest.raises(sa.NotFoundError, match="zero files"):
            sa.include_entry_to_sources(entry)

    def test_include_recurse_with_glob(self, tmp_path: Path) -> None:
        """Test combining glob pattern with recursion."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file1.txt").write_text("1")
        (subdir / "file2.txt").write_text("2")

        # Glob pattern matches a directory, recurse processes it
        entry = sa.PathIncludeEntry(path=str(tmp_path / "sub*"), recurse=True)
        result = sa.include_entry_to_sources(entry)

        assert len(result) == 2


class TestResolvePathIncludes:
    """Test batch resolution of an archive's filesystem includes."""

    def test_resolves_keyed_by_position(self, tmp_path: Path) -> None:
        """Test resolved sources are keyed by include position."""
        (tmp_path / "a.txt").write_text("a")

        include: list[sa.IncludeEntry] = [
            sa.OpRefIncludeEntry(
                op_ref="op://vault/item/field", filename="note.txt"
            ),
            sa.PathIncludeEntry(path=str(tmp_path / "*.txt")),
        ]

        resolved = sa.resolve_path_includes(include)

        # Only the path entry resolves, under its own index.
        assert list(resolved) == [1]
        assert resolved[1] == [tmp_path / "a.txt"]

    def test_single_failure_reported_alone(self, tmp_path: Path) -> None:
        """Test a lone failure reports its own message unwrapped."""
        include: list[sa.IncludeEntry] = [
            sa.PathIncludeEntry(path=str(tmp_path / "nope*.txt")),
        ]

        with pytest.raises(sa.NotFoundError) as excinfo:
            sa.resolve_path_includes(include)

        message = str(excinfo.value)
        assert message.startswith("include[0]: No files matched")
        assert "could not be resolved" not in message
        # The OS error that decided the outcome stays reachable.
        assert excinfo.value.__cause__ is not None

    def test_every_failure_reported(self, tmp_path: Path) -> None:
        """Test resolution continues past a failure and reports them all."""
        good = tmp_path / "good"
        good.mkdir()
        (good / "a.txt").write_text("a")
        missing = tmp_path / "gone"

        include: list[sa.IncludeEntry] = [
            sa.PathIncludeEntry(path=str(tmp_path / "nope*.txt")),
            sa.PathIncludeEntry(path=str(good / "*.txt")),
            sa.PathIncludeEntry(path=str(missing / "*.txt")),
        ]

        with pytest.raises(sa.NotFoundError) as excinfo:
            sa.resolve_path_includes(include)

        message = str(excinfo.value)
        assert "2 include entries could not be resolved" in message
        # Each failure names the include position that asked for it, so a
        # directory-level error is traceable back to its config entry.
        assert "- include[0]: No files matched" in message
        assert "- include[2]: Directory does not exist" in message
        assert str(missing) in message


class TestManifestGeneration:
    """Test manifest creation and validation."""

    def test_build_manifest(self, tmp_path: Path) -> None:
        """Test building manifest from staged files."""
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.txt").write_text("content2")

        manifest = sa.build_manifest(tmp_path, ["file1.txt", "file2.txt"])

        assert manifest["version"] == 1
        assert len(manifest["entries"]) == 2

        entry1 = next(
            e for e in manifest["entries"] if e["name"] == "file1.txt"
        )
        assert entry1["size"] == 8
        assert "sha256" in entry1

    def test_manifest_sorted_by_name(self, tmp_path: Path) -> None:
        """Test that manifest entries are sorted by name."""
        (tmp_path / "zzz.txt").write_text("z")
        (tmp_path / "aaa.txt").write_text("a")
        (tmp_path / "mmm.txt").write_text("m")

        manifest = sa.build_manifest(
            tmp_path, ["zzz.txt", "aaa.txt", "mmm.txt"]
        )

        names = [e["name"] for e in manifest["entries"]]
        assert names == ["aaa.txt", "mmm.txt", "zzz.txt"]

    def test_write_manifest(self, tmp_path: Path) -> None:
        """Test writing manifest to file."""
        manifest: dict[str, Any] = {
            "version": 1,
            "entries": [{"name": "test.txt", "size": 10, "sha256": "abc123"}],
        }

        sa.write_manifest(tmp_path, manifest)

        manifest_file = tmp_path / "manifest.json"
        assert manifest_file.exists()

        loaded = json.loads(manifest_file.read_text())
        assert loaded == manifest

        # Check permissions
        mode = manifest_file.stat().st_mode & 0o777
        assert mode == 0o600


def _build_staging(
    archive_cfg: sa.ArchiveConfig, staging: Path, op_values: dict[str, str]
) -> list[str]:
    """Stage an archive the way do_update does, with 1Password values given."""
    return sa.build_staging_for_archive(
        archive_cfg,
        staging,
        resolved=sa.resolve_path_includes(archive_cfg.include),
        op_values=op_values,
    )


class TestArchiveIntegration:
    """Integration tests for full archive workflow."""

    def test_build_staging_for_archive(self, tmp_path: Path) -> None:
        """Test building staging directory for archive."""
        # Setup test files
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "file1.txt").write_text("content1")
        (src_dir / "file2.txt").write_text("content2")

        staging = tmp_path / "staging"
        staging.mkdir()

        archive_cfg = sa.ArchiveConfig(
            op_password="op://vault/item/password",
            description="Test archive",
            include=[
                sa.PathIncludeEntry(path=str(src_dir / "*.txt")),
                sa.OpRefIncludeEntry(
                    op_ref="op://vault/item/field", filename="note.txt"
                ),
            ],
        )

        result = _build_staging(
            archive_cfg, staging, {"op://vault/item/field": "note content"}
        )

        # Should have 2 included files + 1 op_ref entry
        assert len(result) == 3
        assert "file1.txt" in result
        assert "file2.txt" in result
        assert "note.txt" in result

        # Verify files were staged
        assert (staging / "file1.txt").exists()
        assert (staging / "file2.txt").exists()
        assert (staging / "note.txt").exists()
        assert (staging / "note.txt").read_text() == "note content\n"

    def test_build_staging_normalizes_line_endings(
        self, tmp_path: Path
    ) -> None:
        """Test that op_ref entries normalize line endings."""
        staging = tmp_path / "staging"
        staging.mkdir()

        archive_cfg = sa.ArchiveConfig(
            op_password="op://vault/item/password",
            description="Test archive",
            include=[
                sa.OpRefIncludeEntry(
                    op_ref="op://vault/item/field", filename="note.txt"
                )
            ],
        )

        _build_staging(
            archive_cfg,
            staging,
            {"op://vault/item/field": "line1\r\nline2\r\nline3"},
        )

        content = (staging / "note.txt").read_text()
        assert "\r" not in content
        assert content == "line1\nline2\nline3\n"

    def test_build_staging_with_dir_parameter(self, tmp_path: Path) -> None:
        """Test building staging with files in subdirectories."""
        # Setup test files
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "doc1.txt").write_text("doc content")
        (src_dir / "doc2.txt").write_text("doc content 2")

        staging = tmp_path / "staging"
        staging.mkdir()

        archive_cfg = sa.ArchiveConfig(
            op_password="op://vault/item/password",
            description="Test archive",
            include=[
                # Files without dir go to top level
                sa.PathIncludeEntry(path=str(src_dir / "doc1.txt")),
                # Files with dir go to subdirectory
                sa.PathIncludeEntry(
                    path=str(src_dir / "doc2.txt"), dir="documents"
                ),
                # Op ref with dir goes to subdirectory
                sa.OpRefIncludeEntry(
                    op_ref="op://vault/item/field",
                    filename="secret.txt",
                    dir="secrets",
                ),
            ],
        )

        result = _build_staging(
            archive_cfg, staging, {"op://vault/item/field": "secret content"}
        )

        # Should have all files with correct paths
        assert len(result) == 3
        assert "doc1.txt" in result
        assert "documents/doc2.txt" in result
        assert "secrets/secret.txt" in result

        # Verify files were staged in correct locations
        assert (staging / "doc1.txt").exists()
        assert (staging / "documents" / "doc2.txt").exists()
        assert (staging / "secrets" / "secret.txt").exists()

    def test_build_staging_multiple_files_same_dir(
        self, tmp_path: Path
    ) -> None:
        """Test that multiple entries can use the same dir."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "file1.txt").write_text("content1")
        (src_dir / "file2.txt").write_text("content2")

        staging = tmp_path / "staging"
        staging.mkdir()

        archive_cfg = sa.ArchiveConfig(
            op_password="op://vault/item/password",
            description="Test archive",
            include=[
                sa.PathIncludeEntry(
                    path=str(src_dir / "file1.txt"), dir="shared"
                ),
                sa.PathIncludeEntry(
                    path=str(src_dir / "file2.txt"), dir="shared"
                ),
                sa.OpRefIncludeEntry(
                    op_ref="op://vault/item/field",
                    filename="op_file.txt",
                    dir="shared",
                ),
            ],
        )

        result = _build_staging(
            archive_cfg, staging, {"op://vault/item/field": "op content"}
        )

        assert len(result) == 3
        assert "shared/file1.txt" in result
        assert "shared/file2.txt" in result
        assert "shared/op_file.txt" in result

        # All files should be in the same directory
        assert (staging / "shared" / "file1.txt").exists()
        assert (staging / "shared" / "file2.txt").exists()
        assert (staging / "shared" / "op_file.txt").exists()

    def test_prune_archives(self, tmp_path: Path) -> None:
        """Test pruning old backup archives."""
        # Create mock backup files
        archive_name = "test_archive"
        backups = [
            tmp_path / f"{archive_name}.20250101_120000.7z",
            tmp_path / f"{archive_name}.20250102_120000.7z",
            tmp_path / f"{archive_name}.20250103_120000.7z",
            tmp_path / f"{archive_name}.20250104_120000.7z",
        ]
        for backup in backups:
            backup.write_text("dummy")

        # Keep only 2 most recent
        sa.prune_archives(tmp_path, archive_name, keep=2)

        # Check that oldest 2 were deleted
        assert not backups[0].exists()
        assert not backups[1].exists()
        assert backups[2].exists()
        assert backups[3].exists()

    def test_prune_archives_keep_zero(self, tmp_path: Path) -> None:
        """Test pruning with keep=0 deletes all backups."""
        archive_name = "test_archive"
        backup = tmp_path / f"{archive_name}.20250101_120000.7z"
        backup.write_text("dummy")

        sa.prune_archives(tmp_path, archive_name, keep=0)

        assert not backup.exists()

    def test_list_archives(self, tmp_path: Path) -> None:
        """Test listing backup archives."""
        archive_name = "test_archive"

        # Create valid backups
        (tmp_path / f"{archive_name}.20250101_120000.7z").write_text("1")
        (tmp_path / f"{archive_name}.20250103_120000.7z").write_text("3")
        (tmp_path / f"{archive_name}.20250102_120000.7z").write_text("2")

        # Create invalid files (should be ignored)
        (tmp_path / f"{archive_name}.7z").write_text("base")
        (tmp_path / f"{archive_name}.invalid.7z").write_text("bad")
        (tmp_path / "other.20250101_120000.7z").write_text("other")

        result = sa.list_archives(tmp_path, archive_name)

        assert len(result) == 3
        # Should be sorted chronologically
        assert result[0].name == f"{archive_name}.20250101_120000.7z"
        assert result[1].name == f"{archive_name}.20250102_120000.7z"
        assert result[2].name == f"{archive_name}.20250103_120000.7z"

    def test_list_archives_unreadable_dir_raises(self, tmp_path: Path) -> None:
        """Test an unreadable output directory is not read as empty.

        An empty listing decides that nothing has been published yet,
        which would republish unconditionally and prune nothing -- so
        the refusal has to surface instead.
        """
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "test.20250101_120000.7z").write_text("1")
        out_dir.chmod(0o000)

        try:
            with pytest.raises(sa.UnreadableError, match="Cannot read"):
                sa.list_archives(out_dir, "test")
        finally:
            out_dir.chmod(0o755)

    def test_list_archives_missing_dir_raises(self, tmp_path: Path) -> None:
        """Test a vanished output directory is not read as empty."""
        with pytest.raises(sa.NotFoundError, match="does not exist"):
            sa.list_archives(tmp_path / "gone", "test")


class TestArchiveCreationAndExtraction:
    """Integration tests for 7z archive creation and extraction."""

    def test_make_archive_with_7zz(self, tmp_path: Path) -> None:
        """Test creating actual 7z archive."""
        # Skip if 7zz not available
        import shutil

        if shutil.which("7zz") is None:
            pytest.skip("7zz not available")

        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file1.txt").write_text("content1")
        (staging / "file2.txt").write_text("content2")

        out_archive = tmp_path / "test.7z"

        sa.make_archive_with_7zz(
            staging, out_archive, "password123", ["file1.txt", "file2.txt"]
        )

        # Verify archive was created
        assert out_archive.exists()
        assert out_archive.stat().st_size > 0

    def test_make_archive_with_7zz_failure(self, tmp_path: Path) -> None:
        """Test error handling when 7zz fails."""
        import shutil

        if shutil.which("7zz") is None:
            pytest.skip("7zz not available")

        staging = tmp_path / "staging"
        staging.mkdir()

        out_archive = tmp_path / "test.7z"

        # Try to archive non-existent files
        with pytest.raises(RuntimeError, match="Failed to create archive"):
            sa.make_archive_with_7zz(
                staging, out_archive, "password123", ["nonexistent.txt"]
            )

    def test_extract_manifest_no_archive(self, tmp_path: Path) -> None:
        """Test extracting manifest from non-existent archive."""
        nonexistent = tmp_path / "nonexistent.7z"

        result = sa.extract_manifest_from_archive(nonexistent, "password")

        assert result is None

    def test_create_and_extract_manifest(self, tmp_path: Path) -> None:
        """Test creating archive with manifest and extracting it."""
        import shutil

        if shutil.which("7zz") is None:
            pytest.skip("7zz not available")

        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file1.txt").write_text("content1")
        (staging / "file2.txt").write_text("content2")

        # Create manifest
        manifest = sa.build_manifest(staging, ["file1.txt", "file2.txt"])
        sa.write_manifest(staging, manifest)

        # Create archive with manifest
        out_archive = tmp_path / "test.7z"
        sa.make_archive_with_7zz(
            staging,
            out_archive,
            "password123",
            ["file1.txt", "file2.txt", "manifest.json"],
        )

        # Extract and verify manifest
        extracted = sa.extract_manifest_from_archive(out_archive, "password123")

        assert extracted is not None
        assert extracted["version"] == 1
        assert len(extracted["entries"]) == 2
        assert any(e["name"] == "file1.txt" for e in extracted["entries"])
        assert any(e["name"] == "file2.txt" for e in extracted["entries"])


class TestPublish:
    """Test archive publishing."""

    @patch("secure_archiver.make_archive_with_7zz", autospec=True)
    @patch("secure_archiver.extract_manifest_from_archive", autospec=True)
    def test_publish_no_changes(
        self,
        mock_extract: MagicMock,
        mock_make: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that no publish occurs when content unchanged."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file.txt").write_text("content")

        # Create fake existing archive so find_latest_archive finds it
        existing_archive = out_dir / "test.20250101_120000.7z"
        existing_archive.write_text("dummy")

        # Create corresponding readme with matching content
        existing_readme = out_dir / "test.20250101_120000.txt"
        existing_readme.write_text(
            sa.generate_readme_content(
                "test", "op://vault/item/password", "Test description"
            )
        )

        # Mock existing manifest matching current content
        manifest = {
            "version": 1,
            "entries": [
                {
                    "name": "file.txt",
                    "size": 7,
                    "sha256": sa.sha256_file(staging / "file.txt"),
                }
            ],
        }
        mock_extract.return_value = manifest

        result = sa.publish(
            archive_name="test",
            out_dir=out_dir,
            password="pass",
            staging_dir=staging,
            staged_names=["file.txt"],
            timestamp="20260115_120000",
            op_password_uri="op://vault/item/password",
            description="Test description",
            keep_revisions=3,
            dry_run=False,
            force_update=False,
        )

        assert result is False
        assert not mock_make.called

    @patch("secure_archiver.make_archive_with_7zz", autospec=True)
    @patch("secure_archiver.extract_manifest_from_archive", autospec=True)
    def test_publish_readme_changed(
        self,
        mock_extract: MagicMock,
        mock_make: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test publish occurs when only readme changed."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file.txt").write_text("content")

        # Create fake existing archive so find_latest_archive finds it
        existing_archive = out_dir / "test.20250101_120000.7z"
        existing_archive.write_text("dummy")

        # Create corresponding readme with OLD description
        existing_readme = out_dir / "test.20250101_120000.txt"
        existing_readme.write_text(
            sa.generate_readme_content(
                "test", "op://vault/item/password", "Old description"
            )
        )

        # Mock existing manifest matching current content (no content change)
        manifest = {
            "version": 1,
            "entries": [
                {
                    "name": "file.txt",
                    "size": 7,
                    "sha256": sa.sha256_file(staging / "file.txt"),
                }
            ],
        }
        mock_extract.return_value = manifest

        # Mock make_archive_with_7zz to actually create the archive file
        def mock_make_archive(
            _staging_dir: Path, out_archive: Path, **_kwargs: object
        ) -> None:
            out_archive.write_text("archive content")

        mock_make.side_effect = mock_make_archive

        # Publish with NEW description - should trigger publish
        result = sa.publish(
            archive_name="test",
            out_dir=out_dir,
            password="pass",
            staging_dir=staging,
            staged_names=["file.txt"],
            timestamp="20260115_120000",
            op_password_uri="op://vault/item/password",
            description="New description",  # Changed from "Old description"
            keep_revisions=3,
            dry_run=False,
            force_update=False,
        )

        assert result is True
        assert mock_make.called
        # Verify new archive and readme were created
        assert (out_dir / "test.20260115_120000.7z").exists()
        assert (out_dir / "test.20260115_120000.txt").exists()
        # Verify new readme has the new description
        new_readme = (out_dir / "test.20260115_120000.txt").read_text()
        assert "New description" in new_readme

    @patch("secure_archiver.make_archive_with_7zz", autospec=True)
    @patch("secure_archiver.extract_manifest_from_archive", autospec=True)
    def test_publish_force_update(
        self,
        mock_extract: MagicMock,
        mock_make: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test force update even when content unchanged."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file.txt").write_text("content")

        # Create fake existing archive so find_latest_archive finds it
        existing_archive = out_dir / "test.20250101_120000.7z"
        existing_archive.write_text("dummy")

        # Mock existing manifest matching current content
        manifest = {
            "version": 1,
            "entries": [
                {
                    "name": "file.txt",
                    "size": 7,
                    "sha256": sa.sha256_file(staging / "file.txt"),
                }
            ],
        }
        mock_extract.return_value = manifest

        # Mock make_archive_with_7zz to actually create the archive file
        def mock_make_archive(
            _staging_dir: Path, out_archive: Path, **_kwargs: object
        ) -> None:
            out_archive.write_text("dummy archive")

        mock_make.side_effect = mock_make_archive

        result = sa.publish(
            archive_name="test",
            out_dir=out_dir,
            password="pass",
            staging_dir=staging,
            staged_names=["file.txt"],
            timestamp="20260115_120000",
            op_password_uri="op://vault/item/password",
            description="Test description",
            keep_revisions=3,
            dry_run=False,
            force_update=True,
        )

        assert result is True
        assert mock_make.called

    @patch("secure_archiver.make_archive_with_7zz", autospec=True)
    @patch("secure_archiver.extract_manifest_from_archive", autospec=True)
    def test_publish_dry_run(
        self,
        mock_extract: MagicMock,
        _mock_make: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test dry run mode."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file.txt").write_text("new content")

        # Mock different existing manifest
        mock_extract.return_value = {"version": 1, "entries": []}

        result = sa.publish(
            archive_name="test",
            out_dir=out_dir,
            password="pass",
            staging_dir=staging,
            staged_names=["file.txt"],
            timestamp="20260115_120000",
            op_password_uri="op://vault/item/password",
            description="Test description",
            keep_revisions=3,
            dry_run=True,
            force_update=False,
        )

        assert result is True
        # Archive created in temp dir, not in out_dir
        archives = sa.list_archives(out_dir, "test")
        assert len(archives) == 0

    @patch("secure_archiver.prune_archives", autospec=True)
    @patch("secure_archiver.make_archive_with_7zz", autospec=True)
    @patch("secure_archiver.extract_manifest_from_archive", autospec=True)
    def test_publish_creates_archive(
        self,
        mock_extract: MagicMock,
        mock_make: MagicMock,
        mock_prune: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that publish creates archive."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file.txt").write_text("new content")

        # Create existing timestamped archive
        existing_archive = out_dir / "test.20250101_120000.7z"
        existing_archive.write_text("old archive")

        # Mock different existing manifest (so publish proceeds)
        mock_extract.return_value = {"version": 1, "entries": []}

        # Mock make_archive_with_7zz to actually create the archive file
        def mock_make_archive(
            _staging_dir: Path, out_archive: Path, **_kwargs: object
        ) -> None:
            out_archive.write_text("dummy archive")

        mock_make.side_effect = mock_make_archive

        sa.publish(
            archive_name="test",
            out_dir=out_dir,
            password="pass",
            staging_dir=staging,
            staged_names=["file.txt"],
            timestamp="20260115_120000",
            op_password_uri="op://vault/item/password",
            description="Test description",
            keep_revisions=3,
            dry_run=False,
            force_update=False,
        )

        # Old archive should still exist
        assert existing_archive.exists()

        # New archive should be created
        archives = sa.list_archives(out_dir, "test")
        assert len(archives) == 2

        # Prune should have been called
        assert mock_prune.called


class TestOutputDirValidation:
    """Test output directory validation."""

    def test_ensure_out_dir_usable_success(self, tmp_path: Path) -> None:
        """Test validation passes for valid directory."""
        # Should not raise
        sa.ensure_out_dir_usable(tmp_path)

    def test_ensure_out_dir_not_exist(self, tmp_path: Path) -> None:
        """Test error when output dir doesn't exist."""
        nonexistent = tmp_path / "nonexistent"

        with pytest.raises(sa.ConfigError, match="does not exist"):
            sa.ensure_out_dir_usable(nonexistent)

    def test_ensure_out_dir_not_directory(self, tmp_path: Path) -> None:
        """Test error when output dir is a file."""
        not_dir = tmp_path / "file.txt"
        not_dir.write_text("content")

        with pytest.raises(sa.ConfigError, match="not a directory"):
            sa.ensure_out_dir_usable(not_dir)

    def test_ensure_out_dir_not_writable(self, tmp_path: Path) -> None:
        """Test error when output dir is not writable."""
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(0o555)

        try:
            with pytest.raises(sa.ConfigError, match="not writable"):
                sa.ensure_out_dir_usable(readonly)
        finally:
            # Restore permissions for cleanup
            readonly.chmod(0o755)

    def test_ensure_out_dir_not_readable(self, tmp_path: Path) -> None:
        """Test error when output dir cannot be listed.

        Deciding what to publish means reading what is already there, so
        a write-only directory is rejected up front rather than looking
        like one holding no revisions.
        """
        writeonly = tmp_path / "writeonly"
        writeonly.mkdir()
        writeonly.chmod(0o333)

        try:
            with pytest.raises(sa.ConfigError, match="not readable"):
                sa.ensure_out_dir_usable(writeonly)
        finally:
            writeonly.chmod(0o755)


class TestCommandExecution:
    """Test command execution helpers."""

    def test_run_cmd_success(self) -> None:
        """Test successful command execution."""
        result = sa.run_cmd(["echo", "hello"])

        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_run_cmd_failure_check_true(self) -> None:
        """Test command failure with check=True."""
        with pytest.raises(sa.SubprocessError):
            sa.run_cmd(["false"], check=True)

    def test_run_cmd_failure_check_false(self) -> None:
        """Test command failure with check=False."""
        result = sa.run_cmd(["false"], check=False)

        assert result.returncode != 0

    def test_run_cmd_feeds_stdin_text(self) -> None:
        """Test stdin_text is piped to the command's stdin."""
        result = sa.run_cmd(["cat"], stdin_text="from stdin")

        assert result.stdout == "from stdin"

    def test_stage_op_ref_empty_content_raises(self, tmp_path: Path) -> None:
        """Test stage_op_ref raises ConfigError on empty content."""
        seen: set[str] = set()

        with pytest.raises(sa.ConfigError, match="empty content"):
            sa.stage_op_ref(
                tmp_path,
                "test.txt",
                "op://vault/item",
                content="",
                seen_names=seen,
            )

    def test_stage_op_ref_whitespace_only_raises(self, tmp_path: Path) -> None:
        """Test stage_op_ref raises ConfigError on whitespace-only content."""
        seen: set[str] = set()

        with pytest.raises(sa.ConfigError, match="empty content"):
            sa.stage_op_ref(
                tmp_path,
                "test.txt",
                "op://vault/item",
                content="   \n\t\n   ",
                seen_names=seen,
            )


# The two forms the 1Password CLI reports a client-setup timeout in, and
# the forms it reports an unanswered and a dismissed authorization prompt
# in -- failures that must not be retried.
_OP_DEADLINE_STDERR = (
    "[ERROR] 2026/09/14 18:52:01 could not read secret 'op://v/i/f': "
    "error initializing client: read: context deadline exceeded\n"
)
_OP_CONNECT_STDERR = (
    "[ERROR] 2026/09/14 18:52:01 error initializing client: "
    "connecting to desktop app: connecting to desktop app timed out\n"
)
_OP_AUTH_TIMEOUT_STDERR = (
    "[ERROR] 2026/09/14 22:58:40 could not read secret 'op://v/i/f': "
    "error initializing client: authorization timeout\n"
)
_OP_AUTH_DISMISSED_STDERR = (
    "[ERROR] 2026/09/14 22:58:40 could not read secret 'op://v/i/f': "
    "error initializing client: authorization prompt dismissed, "
    "please try again\n"
)
_OP_NOT_RETRIED_STDERRS = [
    _OP_AUTH_TIMEOUT_STDERR,
    _OP_AUTH_DISMISSED_STDERR,
    "[ERROR] error initializing client: authorization timed out\n",
    "[ERROR] could not read secret 'op://v/i/f': item not found\n",
]


def _op_failure(stderr: str, stdout: str = "") -> sa.SubprocessError:
    return sa.SubprocessError(1, ["op", "read", "op://v/i/f"], stdout, stderr)


def _op_success(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["op"], 0, stdout, "")


class TestRunOp:
    """Test the retrying 1Password CLI runner."""

    @pytest.mark.parametrize(
        ("stderr", "expected"),
        [
            (_OP_DEADLINE_STDERR, True),
            (_OP_CONNECT_STDERR, True),
            *((stderr, False) for stderr in _OP_NOT_RETRIED_STDERRS),
            ("read: context deadline exceeded\n", False),
            ("", False),
            (None, False),
        ],
    )
    def test_client_timeout_detection(
        self, stderr: str | None, expected: bool
    ) -> None:
        """Test only a client-setup timeout counts as a stall."""
        assert sa.op_client_timed_out(stderr) is expected

    @pytest.mark.parametrize(
        "stderr", [_OP_DEADLINE_STDERR, _OP_CONNECT_STDERR]
    )
    @patch("secure_archiver.time.sleep", autospec=True)
    @patch("secure_archiver.run_cmd", autospec=True)
    def test_retries_client_timeout(
        self,
        mock_run_cmd: MagicMock,
        mock_sleep: MagicMock,
        stderr: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test a client-setup timeout is retried and then succeeds."""
        mock_run_cmd.side_effect = [_op_failure(stderr), _op_success("v\n")]

        result = sa.run_op(["read", "op://v/i/f"])

        assert result.stdout == "v\n"
        assert mock_run_cmd.call_count == 2
        mock_sleep.assert_called_once_with(sa._OP_RETRY_DELAY_SECONDS)
        err = capsys.readouterr().err
        assert f"(attempt 1/{sa._OP_ATTEMPTS})" in err
        assert "error initializing client" in err

    @pytest.mark.parametrize("stderr", _OP_NOT_RETRIED_STDERRS)
    @patch("secure_archiver.time.sleep", autospec=True)
    @patch("secure_archiver.run_cmd", autospec=True)
    def test_other_failure_raises_at_once(
        self, mock_run_cmd: MagicMock, mock_sleep: MagicMock, stderr: str
    ) -> None:
        """Test a failure that is not a client-setup timeout is not retried."""
        mock_run_cmd.side_effect = [_op_failure(stderr), _op_success("v\n")]

        with pytest.raises(sa.SubprocessError) as excinfo:
            sa.run_op(["read", "op://v/i/f"])

        assert excinfo.value.stderr == stderr
        assert mock_run_cmd.call_count == 1
        mock_sleep.assert_not_called()

    @patch("secure_archiver.time.sleep", autospec=True)
    @patch("secure_archiver.run_cmd", autospec=True)
    def test_gives_up_after_last_attempt(
        self,
        mock_run_cmd: MagicMock,
        mock_sleep: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test a timeout on every attempt raises after the last one."""
        mock_run_cmd.side_effect = _op_failure(_OP_DEADLINE_STDERR)

        with pytest.raises(sa.SubprocessError, match="context deadline"):
            sa.run_op(["read", "op://v/i/f"])

        assert mock_run_cmd.call_count == sa._OP_ATTEMPTS
        assert mock_sleep.call_count == sa._OP_ATTEMPTS - 1
        err = capsys.readouterr().err
        assert err.count("retrying in") == sa._OP_ATTEMPTS - 1

    @pytest.mark.parametrize(
        "stderr", [_OP_DEADLINE_STDERR, _OP_AUTH_TIMEOUT_STDERR]
    )
    @patch("secure_archiver.time.sleep", autospec=True)
    @patch("secure_archiver.run_cmd", autospec=True)
    def test_error_never_carries_stdout(
        self,
        mock_run_cmd: MagicMock,
        _mock_sleep: MagicMock,
        stderr: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test stdout is dropped from the raised error and retry notes."""
        mock_run_cmd.side_effect = _op_failure(stderr, stdout="SECRET-OUT")

        with pytest.raises(sa.SubprocessError) as excinfo:
            sa.run_op(["read", "op://v/i/f"])

        assert excinfo.value.stdout is None
        assert "SECRET-OUT" not in str(excinfo.value)
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__context__ is None
        assert "SECRET-OUT" not in capsys.readouterr().err

    @patch("secure_archiver.time.sleep", autospec=True)
    @patch("secure_archiver.run_cmd", autospec=True)
    def test_pipes_stdin_text_to_every_attempt(
        self, mock_run_cmd: MagicMock, _mock_sleep: MagicMock
    ) -> None:
        """Test a retried command is piped the same stdin as the first try."""
        mock_run_cmd.side_effect = [
            _op_failure(_OP_DEADLINE_STDERR),
            _op_success("out"),
        ]

        sa.run_op(["inject"], stdin_text="template")

        assert mock_run_cmd.call_args_list == [
            call(["op", "inject"], stdin_text="template"),
            call(["op", "inject"], stdin_text="template"),
        ]


# Matches each enclosed reference in a template handed to `op inject`.
_INJECT_REF = re.compile(r"\{\{ (.+?) \}\}")


class _FakeInject:
    """
    A run_op stand-in that substitutes like `op inject`: each enclosed
    reference in the template piped to its stdin replaced by its value,
    with the trailing newline the real command adds. Records every
    template it was piped.
    """

    def __init__(self, values: dict[str, str], *, trailing: str = "\n"):
        self.values = values
        self.trailing = trailing
        self.templates: list[str] = []

    def __call__(
        self, args: list[str], *, stdin_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        # The template reaches `op` only on its stdin, never as a file.
        assert args == ["inject"]
        assert stdin_text is not None
        self.templates.append(stdin_text)
        out = _INJECT_REF.sub(lambda m: self.values[m.group(1)], stdin_text)
        return _op_success(out + self.trailing)


class TestOpReadRefs:
    """Test reading many 1Password references with one `op inject`."""

    @patch("secure_archiver.run_op", autospec=True)
    def test_reads_every_ref_in_one_call(self, mock_run_op: MagicMock) -> None:
        """Test all references resolve from a single `op inject`."""
        values = {
            "op://Vault/Item One/password": "pw",
            "op://Vault/abc123/Security/one-time password?attribute=otp": "1",
        }
        fake = _FakeInject(values)
        mock_run_op.side_effect = fake

        assert sa.op_read_refs(list(values)) == values
        mock_run_op.assert_called_once()
        [template] = fake.templates
        for ref in values:
            assert template.count(f"{{{{ {ref} }}}}") == 1

    @patch("secure_archiver.run_op", autospec=True)
    def test_duplicate_refs_are_read_once(self, mock_run_op: MagicMock) -> None:
        """Test a reference listed twice appears once in the template."""
        fake = _FakeInject({"op://v/i/pw": "pw"})
        mock_run_op.side_effect = fake

        result = sa.op_read_refs(["op://v/i/pw", "op://v/i/pw"])

        assert result == {"op://v/i/pw": "pw"}
        assert fake.templates[0].count("{{ op://v/i/pw }}") == 1

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("line1\nline2\n\n", "line1\nline2"),
            ("  indented\r\nwindows\r\n", "  indented\r\nwindows\r"),
            ("{{ op://v/i/other }}", "{{ op://v/i/other }}"),
            ("<<secure-archiver:0:1>>", "<<secure-archiver:0:1>>"),
            ("", ""),
        ],
    )
    @patch("secure_archiver.run_op", autospec=True)
    def test_values_come_back_verbatim(
        self, mock_run_op: MagicMock, value: str, expected: str
    ) -> None:
        """Test values keep their content, less trailing newlines."""
        refs = ["op://v/i/before", "op://v/i/value", "op://v/i/after"]
        mock_run_op.side_effect = _FakeInject(
            {refs[0]: "a", refs[1]: value, refs[2]: "b"}
        )

        assert sa.op_read_refs(refs) == {
            refs[0]: "a",
            refs[1]: expected,
            refs[2]: "b",
        }

    @patch("secure_archiver.run_op", autospec=True)
    def test_output_without_trailing_newline(
        self, mock_run_op: MagicMock
    ) -> None:
        """Test output is accepted whether or not it ends in a newline."""
        mock_run_op.side_effect = _FakeInject({"op://v/i/f": "x"}, trailing="")

        assert sa.op_read_refs(["op://v/i/f"]) == {"op://v/i/f": "x"}

    @patch("secure_archiver.run_op", autospec=True)
    def test_markers_differ_per_call(self, mock_run_op: MagicMock) -> None:
        """Test each call frames its references with a fresh nonce."""
        fake = _FakeInject({"op://v/i/f": "x"})
        mock_run_op.side_effect = fake

        sa.op_read_refs(["op://v/i/f"])
        sa.op_read_refs(["op://v/i/f"])

        first, second = fake.templates
        assert first != second

    @patch("secure_archiver.run_op", autospec=True)
    def test_op_failure_names_the_references(
        self, mock_run_op: MagicMock
    ) -> None:
        """Test a failing `op inject` says which references it was reading."""
        stderr = "[ERROR] parsing error at 1:24: invalid character: (\n"
        mock_run_op.side_effect = sa.SubprocessError(
            1, ["op", "inject"], None, stderr
        )

        with pytest.raises(sa.SubprocessError) as excinfo:
            sa.op_read_refs(["op://v/i/a", "op://v/i/b"])

        message = str(excinfo.value)
        assert "op inject, reading:\n  - op://v/i/a\n  - op://v/i/b" in message
        assert stderr.strip() in message
        assert excinfo.value.stdout is None
        assert excinfo.value.exit_code == sa.ExitCode.SUBPROCESS

    @patch("secure_archiver.run_op", autospec=True)
    def test_no_refs_never_calls_op(self, mock_run_op: MagicMock) -> None:
        """Test an empty reference list reads nothing."""
        assert sa.op_read_refs([]) == {}
        mock_run_op.assert_not_called()

    @pytest.mark.parametrize(
        "mangle",
        [
            pytest.param(lambda out, m: out[: out.rindex(m[-1])], id="cut"),
            pytest.param(lambda out, _m: "junk" + out, id="leading"),
            pytest.param(lambda out, _m: out + "junk", id="trailing"),
            pytest.param(lambda out, _m: out + "\n", id="extra-newline"),
            pytest.param(
                lambda out, m: out.replace(m[1], m[1] + "SECRET-A" + m[1]),
                id="duplicated-marker",
            ),
            pytest.param(
                lambda out, m: (
                    out.replace(m[0], "@0@")
                    .replace(m[1], m[0])
                    .replace("@0@", m[1])
                ),
                id="reordered",
            ),
        ],
    )
    @patch("secure_archiver.run_op", autospec=True)
    def test_unexpected_output_raises_without_echoing(
        self,
        mock_run_op: MagicMock,
        mangle: Callable[[str, list[str]], str],
    ) -> None:
        """Test malformed output fails, naming references but no values."""
        refs = ["op://v/i/a", "op://v/i/b"]
        honest = _FakeInject({refs[0]: "SECRET-A", refs[1]: "SECRET-B"})

        def run(
            args: list[str], *, stdin_text: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            out = honest(args, stdin_text=stdin_text).stdout
            markers = re.findall(
                r"<<secure-archiver:[0-9a-f]+:\d+>>", honest.templates[-1]
            )
            return _op_success(mangle(out, markers))

        mock_run_op.side_effect = run

        with pytest.raises(sa.SecureArchiverError) as excinfo:
            sa.op_read_refs(refs)

        message = str(excinfo.value)
        assert "op://v/i/a" in message
        assert "op://v/i/b" in message
        assert "SECRET" not in message


class TestUtilityFunctions:
    """Test utility and helper functions."""

    def test_sha256_file(self, tmp_path: Path) -> None:
        """Test SHA256 hash calculation."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        result = sa.sha256_file(test_file)

        # Verify it's a valid hex string of correct length
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_sha256_file_large(self, tmp_path: Path) -> None:
        """Test SHA256 works with files larger than chunk size."""
        test_file = tmp_path / "large.bin"
        # Create file > 1MB (chunk size in sha256_file)
        test_file.write_bytes(b"x" * (2 * 1024 * 1024))

        result = sa.sha256_file(test_file)

        assert len(result) == 64

    def test_timestamp_format(self) -> None:
        """Test timestamp format is correct."""
        ts = sa.timestamp_now()

        # Should be YYYYMMDD_HHMMSS
        assert len(ts) == 15
        assert ts[8] == "_"
        assert sa.TS_RE.match(ts)

    def test_require_passes_on_true(self) -> None:
        """Test require passes when condition is true."""
        # Should not raise
        sa.require(True, "This should not be raised")

    def test_require_raises_on_false(self) -> None:
        """Test require raises ConfigError when condition is false."""
        with pytest.raises(sa.ConfigError, match="Test error"):
            sa.require(False, "Test error")

    def test_load_config(self, tmp_path: Path) -> None:
        """Test loading valid config file."""
        config_file = tmp_path / "test.toml"
        config_file.write_text("""
[general]
output_dir = "/tmp/test"
keep_revisions = 5

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [{ path = "~/Documents" }]
""")

        result = sa.load_config(config_file)

        assert isinstance(result, sa.Config)
        assert result.general.output_dir == "/tmp/test"
        assert result.general.keep_revisions == 5
        assert "Test" in result.archives
        assert result.archives["Test"].op_password == "op://vault/item/password"

    def test_load_config_not_found(self, tmp_path: Path) -> None:
        """Test error when config file doesn't exist."""
        config_file = tmp_path / "nonexistent.toml"

        with pytest.raises(sa.ConfigError, match="Config file not found"):
            sa.load_config(config_file)

    def test_locale_sorted_paths(self, tmp_path: Path) -> None:
        """Test locale-aware path sorting."""
        paths = [
            tmp_path / "zzz.txt",
            tmp_path / "aaa.txt",
            tmp_path / "mmm.txt",
        ]

        result = sa.locale_sorted_paths(paths)

        assert result[0].name == "aaa.txt"
        assert result[1].name == "mmm.txt"
        assert result[2].name == "zzz.txt"


class TestRunUpdate:
    """Test run_update orchestration function."""

    @patch("secure_archiver.op_read_refs", autospec=True)
    def test_run_update_creates_archives(
        self, mock_op_read_refs: MagicMock, tmp_path: Path
    ) -> None:
        """Test that run_update creates archives from config."""
        import shutil

        if shutil.which("7zz") is None:
            pytest.skip("7zz not available")

        # Every 1Password reference reads as the test password
        mock_op_read_refs.side_effect = lambda refs: dict.fromkeys(
            refs, "test_password"
        )

        # Create source files to archive
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        (src_dir / "file1.txt").write_text("content1")
        (src_dir / "file2.txt").write_text("content2")

        # Create output directory
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        # Create test config
        config_file = tmp_path / "test_config.toml"
        config_file.write_text(f"""
[general]
output_dir = "{out_dir}"
keep_revisions = 2

[archive.TestArchive]
op_password = "op://vault/item/password"
description = "Test archive description"
include = [
    {{ path = "{src_dir}/*.txt" }},
]
""")

        # Run update
        sa.do_update(config_file, dry_run=False, force_update=False)

        # Verify archive was created (with timestamp in filename)
        archive = sa.find_latest_archive(out_dir, "TestArchive")
        assert archive is not None
        assert archive.exists()

        # Extract and verify contents
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        import subprocess

        subprocess.run(
            [
                "7zz",
                "e",
                "-y",
                "-ptest_password",
                str(archive),
                f"-o{extract_dir}",
            ],
            check=True,
            capture_output=True,
        )

        # Verify extracted files
        assert (extract_dir / "file1.txt").exists()
        assert (extract_dir / "file2.txt").exists()
        assert (extract_dir / "file1.txt").read_text() == "content1"
        assert (extract_dir / "file2.txt").read_text() == "content2"
        assert (extract_dir / "manifest.json").exists()

    # The tool probe runs before any include work and would fail the
    # test on a host without `op` / `7zz`; what is under test is the
    # ordering after it.
    @patch("secure_archiver.ensure_tools", autospec=True)
    @patch("secure_archiver.op_read_refs", autospec=True)
    def test_run_update_bad_include_never_reads_1password(
        self,
        mock_op_read_refs: MagicMock,
        _mock_ensure_tools: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test an unresolvable include stops the run before 1Password.

        An archive's secrets, password included, are all read before its
        files are staged, so an include fault found only during staging
        would already have prompted 1Password for an archive that cannot
        be built -- even one whose op_ref entry comes first.
        """
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        config_file = tmp_path / "test_config.toml"
        config_file.write_text(f"""
[general]
output_dir = "{out_dir}"

[archive.TestArchive]
op_password = "op://vault/item/password"
description = "Test archive description"
include = [
    {{ op_ref = "op://vault/item/field", filename = "note.txt" }},
    {{ path = "{tmp_path}/nothing*.txt" }},
]
""")

        with pytest.raises(sa.NotFoundError, match="No files matched"):
            sa.do_update(config_file, dry_run=False, force_update=False)

        mock_op_read_refs.assert_not_called()

    @patch("secure_archiver.publish", autospec=True)
    @patch("secure_archiver.ensure_tools", autospec=True)
    @patch("secure_archiver.op_read_refs", autospec=True)
    def test_run_update_reads_each_archive_in_one_call(
        self,
        mock_op_read_refs: MagicMock,
        _mock_ensure_tools: MagicMock,
        mock_publish: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test each archive's secrets come from one read, then staged."""
        src = tmp_path / "file.txt"
        src.write_text("file content")
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        config_file = tmp_path / "test_config.toml"
        config_file.write_text(f"""
[general]
output_dir = "{out_dir}"

[archive.A]
op_password = "op://v/a/password"
description = "Archive A"
include = [
    {{ op_ref = "op://v/a/one", filename = "one.txt" }},
    {{ path = "{src}" }},
    {{ op_ref = "op://v/a/two", filename = "two.txt", dir = "sub" }},
]

[archive.B]
op_password = "op://v/b/password"
description = "Archive B"
include = [{{ path = "{src}" }}]
""")
        mock_op_read_refs.side_effect = lambda refs: {
            ref: f"value of {ref}" for ref in refs
        }

        published: dict[str, tuple[str, dict[str, str]]] = {}

        def record(**kwargs: Any) -> bool:
            staging_dir: Path = kwargs["staging_dir"]
            published[kwargs["archive_name"]] = (
                kwargs["password"],
                {
                    name: (staging_dir / name).read_text()
                    for name in kwargs["staged_names"]
                },
            )
            return False

        mock_publish.side_effect = record

        sa.do_update(config_file, dry_run=False, force_update=False)

        assert mock_op_read_refs.call_args_list == [
            call(["op://v/a/one", "op://v/a/two", "op://v/a/password"]),
            call(["op://v/b/password"]),
        ]
        assert published == {
            "A": (
                "value of op://v/a/password",
                {
                    "one.txt": "value of op://v/a/one\n",
                    "file.txt": "file content",
                    "sub/two.txt": "value of op://v/a/two\n",
                },
            ),
            "B": ("value of op://v/b/password", {"file.txt": "file content"}),
        }


class TestArchiveReadme:
    """Test archive readme generation."""

    def test_write_archive_readme_creates_file_with_content(
        self, tmp_path: Path
    ) -> None:
        """Test that write_archive_readme creates file with correct content."""
        archive_name = "MyTestArchive"
        timestamp = "20260115_120000"
        op_password_uri = "op://Recovery/Estate Documents/password"
        description = (
            "This is a detailed test description\nwith multiple lines."
        )

        sa.write_archive_readme(
            out_dir=tmp_path,
            archive_name=archive_name,
            timestamp=timestamp,
            op_password_uri=op_password_uri,
            description=description,
        )

        readme_file = tmp_path / f"{archive_name}.{timestamp}.txt"
        assert readme_file.exists()

        content = readme_file.read_text()
        assert f"# {archive_name} Description" in content
        assert f"# {archive_name} Password" in content
        assert description in content
        assert op_password_uri in content

    def test_write_archive_readme_dry_run_does_not_create_file(
        self, tmp_path: Path
    ) -> None:
        """Test that dry_run=True prevents file creation."""
        sa.write_archive_readme(
            out_dir=tmp_path,
            archive_name="TestArchive",
            timestamp="20260115_120000",
            op_password_uri="op://vault/item/password",
            description="Test description",
            dry_run=True,
        )

        readme_file = tmp_path / "TestArchive.20260115_120000.txt"
        assert not readme_file.exists()

    def test_write_archive_readme_fails_if_exists(self, tmp_path: Path) -> None:
        """Test that write_archive_readme fails if file already exists."""
        readme_file = tmp_path / "TestArchive.20260115_120000.txt"
        readme_file.write_text("pre-existing content")

        with pytest.raises(RuntimeError, match="already exists"):
            sa.write_archive_readme(
                out_dir=tmp_path,
                archive_name="TestArchive",
                timestamp="20260115_120000",
                op_password_uri="op://vault/item/password",
                description="New description",
            )

    @patch("secure_archiver.op_read_refs", autospec=True)
    def test_run_update_creates_readme(
        self, mock_op_read_refs: MagicMock, tmp_path: Path
    ) -> None:
        """Test that run_update creates readme alongside archive."""
        import shutil

        if shutil.which("7zz") is None:
            pytest.skip("7zz not available")

        mock_op_read_refs.side_effect = lambda refs: dict.fromkeys(
            refs, "test_password"
        )

        # Create source files
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        (src_dir / "file.txt").write_text("content")

        # Create output directory
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        # Create test config
        config_file = tmp_path / "test_config.toml"
        op_password_uri = "op://TestVault/TestItem/password"
        description = "Test archive for readme verification"
        config_file.write_text(f"""
[general]
output_dir = "{out_dir}"

[archive.ReadmeTest]
op_password = "{op_password_uri}"
description = "{description}"
include = [
    {{ path = "{src_dir}/*.txt" }},
]
""")

        sa.do_update(config_file, dry_run=False, force_update=False)

        # Verify archive was created
        archive = sa.find_latest_archive(out_dir, "ReadmeTest")
        assert archive is not None

        # Verify readme was created with same timestamp as archive
        readme_file = archive.with_suffix(".txt")
        assert readme_file.exists()

        # Verify readme contains correct values
        content = readme_file.read_text()
        assert "ReadmeTest" in content
        assert op_password_uri in content
        assert description in content


class TestUnknownArgRoutedToSubparser(UnknownArgRoutedToSubparserBase):
    """Unknown args print the subcommand's usage line."""

    PARSER_FUNC = staticmethod(sa.build_parser)
    CASES: ClassVar = [
        (["create", "--bogus"], "create"),
        (["config", "validate", "--bogus"], "config validate"),
        (["config", "init", "--bogus"], "config init"),
        (["automate", "apply", "--bogus"], "automate apply"),
        (["automate", "status", "--bogus"], "automate status"),
        (["automate", "destroy", "--bogus"], "automate destroy"),
    ]


class TestCmdCallbacks(CmdCallbacksBase):
    """Test COMMAND_CALLBACKS dispatch table."""

    CALLBACKS = sa.COMMAND_CALLBACKS
    PARSER_FUNC = staticmethod(sa.build_parser)
    CLI_FUNC = staticmethod(sa.cli)
    EXIT_CODE_USAGE = sa.ExitCode.USAGE
    TEST_SUBCOMMAND = "create"
    EXCEPTION_EXIT_CODE_MAP: ClassVar = [
        (sa.UsageError("t"), sa.ExitCode.USAGE),
        (sa.ConfigError("t"), sa.ExitCode.CONFIG),
        (sa.NotFoundError("t"), sa.ExitCode.NOT_FOUND),
        (
            sa.SubprocessError(1, ["cmd"], "out", "err"),
            sa.ExitCode.SUBPROCESS,
        ),
        (sa.CollisionError("t"), sa.ExitCode.ERROR),
        (RuntimeError("t"), sa.ExitCode.CRASHED),
    ]


class TestConfigGroupDispatch:
    """The `config` group routes the space-joined COMMAND_CALLBACKS keys
    (`config init` / `config validate`) through cli() to the right
    handler. A flat dispatch table without the join would key on the
    bare leaf name and silently fail to match."""

    def test_config_init_dispatches(self, tmp_path: Path) -> None:
        mock_cb = MagicMock()
        output_file = tmp_path / "example.toml"
        with (
            patch.dict(sa.COMMAND_CALLBACKS, {"config init": mock_cb}),
            patch(
                "sys.argv",
                ["prog", "config", "init", str(output_file)],
            ),
        ):
            result = sa.cli()
        assert result == 0
        mock_cb.assert_called_once_with(output_file=output_file)

    def test_config_validate_dispatches(self, tmp_path: Path) -> None:
        mock_cb = MagicMock()
        config_file = tmp_path / "secure-archiver.toml"
        with (
            patch.dict(sa.COMMAND_CALLBACKS, {"config validate": mock_cb}),
            patch(
                "sys.argv",
                ["prog", "config", "validate", "--config", str(config_file)],
            ),
        ):
            result = sa.cli()
        assert result == 0
        mock_cb.assert_called_once_with(config=config_file)


class TestWriteExampleConfig:
    """Test the `config init` subcommand."""

    def test_writes_example_config(self, tmp_path: Path) -> None:
        """Test that `config init` creates a file."""
        output_file = tmp_path / "example.toml"
        sa.do_config_init(output_file)
        assert output_file.exists()
        content = output_file.read_text()
        assert "[general]" in content
        assert "[archive." in content

    def test_fails_if_file_exists(self, tmp_path: Path) -> None:
        """Test that `config init` raises if file already exists."""
        output_file = tmp_path / "example.toml"
        output_file.write_text("existing content")
        with pytest.raises(sa.ConfigError, match="already exists"):
            sa.do_config_init(output_file)
        # Original content should be preserved
        assert output_file.read_text() == "existing content"

    def test_generated_config_is_valid(self, tmp_path: Path) -> None:
        """Test that the generated example config passes validation."""
        output_file = tmp_path / "example.toml"
        sa.do_config_init(output_file)

        # Load config - validation happens inside load_config
        # If it returns without raising, the config is valid
        cfg = sa.load_config(output_file)
        assert isinstance(cfg, sa.Config)
        assert cfg.general.output_dir == "~/secure_archives"

    def test_shipped_default_config_is_valid(self, tmp_path: Path) -> None:
        """The shipped data file `config init` writes must exist, be
        ASCII, parse as TOML, and pass the config loader. Guard against
        the file (or the repo-relative path to it) going missing."""
        shipped = sa._DEFAULT_CONFIG_PATH
        assert shipped.is_file()
        text = shipped.read_text(encoding="utf-8")
        text.encode("ascii")  # raises if non-ASCII slips in
        tomllib.loads(text)
        config_file = tmp_path / "secure-archiver.toml"
        config_file.write_text(text, encoding="utf-8")
        assert isinstance(sa.load_config(config_file), sa.Config)


class TestCheckConfig:
    """Test the `config validate` subcommand."""

    def test_valid_config_passes(self, tmp_path: Path) -> None:
        """Test that a valid config passes validation."""
        config_file = tmp_path / "valid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"
keep_revisions = 3

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [
  { path = "~/Documents" },
]
""")
        sa.do_config_validate(config_file)  # Should not raise

    def test_missing_general_section(self, tmp_path: Path) -> None:
        """Test that missing [general] section is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [{ path = "~/Documents" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_missing_output_dir(self, tmp_path: Path) -> None:
        """Test that missing output_dir is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
keep_revisions = 3

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [{ path = "~/Documents" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_missing_archive_section(self, tmp_path: Path) -> None:
        """Test that missing archive section is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_missing_op_password(self, tmp_path: Path) -> None:
        """Test that missing op_password is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
description = "Test archive"
include = [{ path = "~/Documents" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_missing_description(self, tmp_path: Path) -> None:
        """Test that missing description is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
include = [{ path = "~/Documents" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_missing_include(self, tmp_path: Path) -> None:
        """Test that missing include is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_empty_include(self, tmp_path: Path) -> None:
        """Test that empty include array is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = []
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_empty_output_dir(self, tmp_path: Path) -> None:
        """Test that empty output_dir is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = ""

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [{ path = "~/Documents" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_empty_op_password(self, tmp_path: Path) -> None:
        """Test that empty op_password is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = ""
description = "Test archive"
include = [{ path = "~/Documents" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_empty_description(self, tmp_path: Path) -> None:
        """Test that empty description is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = ""
include = [{ path = "~/Documents" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_empty_path(self, tmp_path: Path) -> None:
        """Test that empty path in include entry is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [{ path = "" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_empty_op_ref(self, tmp_path: Path) -> None:
        """Test that empty op_ref in include entry is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [{ op_ref = "", filename = "test.txt" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    @staticmethod
    def _write_refs_config(config_file: Path, field: str, ref: str) -> None:
        """Write a config using `ref` as `field`, the other field valid."""
        password = ref if field == "op_password" else "op://vault/item/pw"
        op_ref = ref if field == "op_ref" else "op://vault/item/notes"
        config_file.write_text(f"""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = {json.dumps(password)}
description = "Test archive"
include = [{{ op_ref = {json.dumps(op_ref)}, filename = "test.txt" }}]
""")

    @pytest.mark.parametrize(
        "ref",
        [
            # What the `op inject` template language would interpret.
            '"op://vault/item/field"',
            "op://vault/$ITEM/field",
            "op://vault/${ITEM}/field",
            "op://vault/{item}/field",
            "op://vault/item/field }}",
            "op://vault/item/field\nop://vault/item/other",
            " op://vault/item/field",
            "op://vault/item/field\t",
            # Outside 1Password's reference syntax altogether.
            "vault/item/field",
            "op://vault/item",
            "op://vault/item/section/field/extra",
            "op://vault/Ed's item/field",
            "op://vault/item/field?attribute=otp&x=y",
            f"op://vault/it{chr(0xE9)}m/field",
        ],
    )
    @pytest.mark.parametrize("field", ["op_password", "op_ref"])
    def test_invalid_op_reference(
        self, tmp_path: Path, field: str, ref: str
    ) -> None:
        """Test a reference outside the secret-reference syntax is refused."""
        config_file = tmp_path / "invalid.toml"
        self._write_refs_config(config_file, field, ref)

        with pytest.raises(
            sa.ConfigError, match=rf"{field} must be a secret reference"
        ):
            sa.do_config_validate(config_file)

    @pytest.mark.parametrize(
        "ref",
        [
            "op://Vault/Estate Item - Drive/password",
            "op://Private/abc123def/Security/one-time password?attribute=otp",
            "op://dev_vault/ssh.key/private key?ssh-format=openssh",
            "op://Vault/Item/file.txt",
        ],
    )
    @pytest.mark.parametrize("field", ["op_password", "op_ref"])
    def test_valid_op_reference(
        self, tmp_path: Path, field: str, ref: str
    ) -> None:
        """Test documented reference shapes stay valid."""
        config_file = tmp_path / "valid.toml"
        self._write_refs_config(config_file, field, ref)

        sa.do_config_validate(config_file)  # Should not raise

    def test_empty_filename(self, tmp_path: Path) -> None:
        """Test that empty filename in include entry is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [{ op_ref = "op://vault/item/notes", filename = "" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_include_entry_without_path_or_op_ref(self, tmp_path: Path) -> None:
        """Test that include entry without path or op_ref is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [{ recurse = true }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_include_entry_with_both_path_and_op_ref(
        self, tmp_path: Path
    ) -> None:
        """Test that include entry with both path and op_ref is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [{ path = "~/Documents", op_ref = "op://vault/item/notes" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_op_ref_without_filename(self, tmp_path: Path) -> None:
        """Test that op_ref entry without filename is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [{ op_ref = "op://vault/item/notes" }]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_valid_config_with_op_ref(self, tmp_path: Path) -> None:
        """Test that valid config with op_ref entry passes."""
        config_file = tmp_path / "valid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [
  { path = "~/Documents" },
  { op_ref = "op://vault/item/notes", filename = "secrets.txt" },
]
""")
        sa.do_config_validate(config_file)  # Should not raise

    def test_nonexistent_config_file(self, tmp_path: Path) -> None:
        """Test that nonexistent config file raises ConfigError."""
        config_file = tmp_path / "nonexistent.toml"
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_invalid_toml_syntax(self, tmp_path: Path) -> None:
        """Test that invalid TOML syntax raises ConfigError."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("this is not valid [[[toml")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_path_entry_with_invalid_key(self, tmp_path: Path) -> None:
        """Test that path entry with invalid key is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [
  { path = "~/Documents", invalid_key = true },
]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_path_entry_with_filename_is_invalid(self, tmp_path: Path) -> None:
        """Test that path entry with filename key is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [
  { path = "~/Documents", filename = "foo.txt" },
]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_path_entry_with_dir_is_valid(self, tmp_path: Path) -> None:
        """Test that path entry with dir key is accepted."""
        config_file = tmp_path / "valid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [
  { path = "~/Documents/*.pdf", dir = "pdfs" },
]
""")
        sa.do_config_validate(config_file)  # Should not raise

    def test_op_ref_entry_with_dir_is_valid(self, tmp_path: Path) -> None:
        """Test that op_ref entry with dir key is accepted."""
        config_file = tmp_path / "valid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [
  { op_ref = "op://vault/item/notes", filename = "s.txt", dir = "secrets" },
]
""")
        sa.do_config_validate(config_file)  # Should not raise

    def test_path_entry_with_empty_dir_is_invalid(self, tmp_path: Path) -> None:
        """Test that path entry with empty dir is rejected."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [
  { path = "~/Documents/*.pdf", dir = "" },
]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_op_ref_entry_with_empty_dir_is_invalid(
        self, tmp_path: Path
    ) -> None:
        """Test that op_ref entry with empty dir is rejected."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [
  { op_ref = "op://vault/item/notes", filename = "s.txt", dir = "  " },
]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_op_ref_entry_with_invalid_key(self, tmp_path: Path) -> None:
        """Test that op_ref entry with invalid key is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [
  { op_ref = "op://vault/item/notes", filename = "s.txt", recurse = true },
]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)

    def test_op_ref_entry_with_latest_is_invalid(self, tmp_path: Path) -> None:
        """Test that op_ref entry with latest key is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"

[archive.Test]
op_password = "op://vault/item/password"
description = "Test archive"
include = [
  { op_ref = "op://vault/item/notes", filename = "s.txt", latest = true },
]
""")
        with pytest.raises(sa.ConfigError):
            sa.do_config_validate(config_file)


class TestConfigFromDict:
    """Test Config.from_dict() validation."""

    def test_negative_keep_revisions(self, tmp_path: Path) -> None:
        """Test that negative keep_revisions is caught."""
        cfg_dict: dict[str, Any] = {
            "general": {"output_dir": "~/archives", "keep_revisions": -1},
            "archive": {
                "Test": {
                    "op_password": "op://v/i/p",
                    "description": "desc",
                    "include": [{"path": "~/Documents"}],
                }
            },
        }
        with pytest.raises(sa.ConfigError, match="keep_revisions"):
            sa.Config.from_dict(cfg_dict, tmp_path / "test.toml")

    def test_multiple_errors_reported(self, tmp_path: Path) -> None:
        """Test that multiple validation errors are all reported."""
        cfg_dict: dict[str, Any] = {
            "general": {},  # missing output_dir
            "archive": {
                "Test": {
                    # missing op_password, description, include
                }
            },
        }
        with pytest.raises(sa.ConfigError) as exc_info:
            sa.Config.from_dict(cfg_dict, tmp_path / "test.toml")
        # Check that multiple errors are reported
        error_msg = str(exc_info.value)
        assert "output_dir" in error_msg
        assert "op_password" in error_msg


class TestOutputReadme:
    """Test write_output_readme() function."""

    def test_creates_readme(self, tmp_path: Path) -> None:
        """Test that README.txt is created."""
        content = "This is a test readme."
        result = sa.write_output_readme(tmp_path, content)
        assert result is True
        readme_path = tmp_path / "README.txt"
        assert readme_path.exists()
        assert readme_path.read_text() == content + "\n"

    def test_normalizes_trailing_newline(self, tmp_path: Path) -> None:
        """Test that content is normalized to have single trailing newline."""
        content = "Test content\n\n\n"
        sa.write_output_readme(tmp_path, content)
        readme_path = tmp_path / "README.txt"
        assert readme_path.read_text() == "Test content\n"

    def test_returns_false_if_unchanged(self, tmp_path: Path) -> None:
        """Test that returns False when content unchanged."""
        content = "Test content"
        sa.write_output_readme(tmp_path, content)
        result = sa.write_output_readme(tmp_path, content)
        assert result is False

    def test_returns_true_if_changed(self, tmp_path: Path) -> None:
        """Test that returns True when content changed."""
        sa.write_output_readme(tmp_path, "Original content")
        result = sa.write_output_readme(tmp_path, "New content")
        assert result is True
        readme_path = tmp_path / "README.txt"
        assert readme_path.read_text() == "New content\n"

    def test_dry_run_does_not_create_file(self, tmp_path: Path) -> None:
        """Test that dry_run=True doesn't create the file."""
        content = "Test content"
        result = sa.write_output_readme(tmp_path, content, dry_run=True)
        assert result is True
        readme_path = tmp_path / "README.txt"
        assert not readme_path.exists()

    def test_dry_run_returns_true_for_update(self, tmp_path: Path) -> None:
        """Test that dry_run=True returns True for would-be update."""
        sa.write_output_readme(tmp_path, "Original content")
        result = sa.write_output_readme(tmp_path, "New content", dry_run=True)
        assert result is True
        # Original content should be unchanged
        readme_path = tmp_path / "README.txt"
        assert readme_path.read_text() == "Original content\n"


class TestGeneralConfigReadme:
    """Test GeneralConfig readme field parsing."""

    def test_readme_field_parsed(self, tmp_path: Path) -> None:
        """Test that readme field is correctly parsed."""
        cfg_dict: dict[str, Any] = {
            "general": {
                "output_dir": "~/archives",
                "readme": "Test readme content",
            },
            "archive": {
                "Test": {
                    "op_password": "op://v/i/p",
                    "description": "desc",
                    "include": [{"path": "~/Documents"}],
                }
            },
        }
        cfg = sa.Config.from_dict(cfg_dict, tmp_path / "test.toml")
        assert cfg.general.readme == "Test readme content"

    def test_readme_field_optional(self, tmp_path: Path) -> None:
        """Test that readme field is optional."""
        cfg_dict: dict[str, Any] = {
            "general": {
                "output_dir": "~/archives",
            },
            "archive": {
                "Test": {
                    "op_password": "op://v/i/p",
                    "description": "desc",
                    "include": [{"path": "~/Documents"}],
                }
            },
        }
        cfg = sa.Config.from_dict(cfg_dict, tmp_path / "test.toml")
        assert cfg.general.readme is None

    def test_readme_multiline(self, tmp_path: Path) -> None:
        """Test that multi-line readme is parsed correctly."""
        cfg_dict: dict[str, Any] = {
            "general": {
                "output_dir": "~/archives",
                "readme": "Line 1\nLine 2\nLine 3",
            },
            "archive": {
                "Test": {
                    "op_password": "op://v/i/p",
                    "description": "desc",
                    "include": [{"path": "~/Documents"}],
                }
            },
        }
        cfg = sa.Config.from_dict(cfg_dict, tmp_path / "test.toml")
        assert cfg.general.readme == "Line 1\nLine 2\nLine 3"

    def test_readme_must_be_string(self, tmp_path: Path) -> None:
        """Test that non-string readme raises error."""
        cfg_dict: dict[str, Any] = {
            "general": {
                "output_dir": "~/archives",
                "readme": 123,  # Invalid: not a string
            },
            "archive": {
                "Test": {
                    "op_password": "op://v/i/p",
                    "description": "desc",
                    "include": [{"path": "~/Documents"}],
                }
            },
        }
        with pytest.raises(sa.ConfigError, match="readme must be a string"):
            sa.Config.from_dict(cfg_dict, tmp_path / "test.toml")


class TestUnreadableExitCode:
    """Test what a refused path exits with."""

    def test_unreadable_uses_the_general_error_code(self) -> None:
        """Test a refusal exits as a general error, not as not-found.

        "File not found" would mislabel a permission or I/O refusal for
        anything reading the exit status, and there is nothing more
        specific such a reader could do about it anyway.
        """
        assert sa.UnreadableError.exit_code == sa.ExitCode.ERROR
        # Carried by inheritance, so it cannot drift from the base.
        assert "exit_code" not in sa.UnreadableError.__dict__


class TestExceptionHierarchy(ExceptionHierarchyBase):
    """Test SecureArchiverError exception hierarchy."""

    BASE_ERROR = sa.SecureArchiverError
    EXIT_CODE = sa.ExitCode
    EXCLUDED_CODES: ClassVar = {
        sa.ExitCode.SUCCESS,
        sa.ExitCode.WARNING,
        sa.ExitCode.CRASHED,
    }


class TestAutomate(CronyAutomateBase):
    """Test the automate subcommand (crony-backed).

    The generic apply / status / destroy mechanics and the two crony
    contract gates (bundle-validates, argv-parses) come from
    CronyAutomateBase; only secure-archiver's own render -- a single
    weekly create job with Darwin-gated interactive flag -- is tested
    here.
    """

    MODULE: ClassVar[Any] = sa
    BUNDLE = "secure-archiver"
    ERROR = sa.SecureArchiverError
    CRONY_PARSER = staticmethod(crony_cli._build_parser)
    EXPECTED_VERBS: ClassVar = {"apply", "status", "destroy"}

    def apply(self, *, config_only: bool) -> None:
        sa.do_automate_apply(config_only=config_only)

    def status(self, *, config_only: bool) -> None:
        sa.do_automate_status(config_only=config_only)

    def destroy(self, *, config_only: bool) -> None:
        sa.do_automate_destroy(config_only=config_only)

    def render_cases(self, monkeypatch: Any) -> list[tuple[str, str]]:
        # The interactive flag is gated to Darwin, so both platform
        # renders (with and without it) must validate against crony.
        cases: list[tuple[str, str]] = []
        for system in ("Darwin", "Linux"):
            monkeypatch.setattr(sa.platform, "system", lambda s=system: s)
            cases.append((system, sa._render_crony_bundle()))
        return cases

    @pytest.mark.parametrize(
        ("system", "expect_flag"),
        [("Darwin", True), ("Linux", False)],
    )
    def test_interactive_flag_gated_on_macos(
        self, system: str, expect_flag: bool, monkeypatch: Any
    ) -> None:
        # crony's interactive flag (wait-for-user + confirm) is macOS
        # only -- it would crash the runner on Linux -- so the create job
        # carries it on Darwin and omits it everywhere else, where the
        # job runs unattended.
        monkeypatch.setattr(sa.platform, "system", lambda: system)
        doc = tomllib.loads(sa._render_crony_bundle())
        if expect_flag:
            assert doc["job"]["create"]["flags"] == ["interactive"]
        else:
            assert "flags" not in doc["job"]["create"]

    def test_render_is_single_weekly_create_job(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(sa.platform, "system", lambda: "Darwin")
        doc = tomllib.loads(sa._render_crony_bundle())
        assert set(doc["job"]) == {"create"}
        assert doc["job"]["create"]["interval"] == "1w"
        assert doc["job"]["create"]["command"].endswith(" create")
        assert doc["defaults"]["notify-channels"] == ["default"]
        assert doc["defaults"]["env"] == {"PATH": "$HOME/.local/bin:$PATH"}
        assert set(doc["target"]) == {"all"}
        assert doc["target"]["all"]["jobs"] == ["create"]

    def test_deterministic_uuids(self) -> None:
        assert sa._CRONY.job_uuid("create") == sa._CRONY.job_uuid("create")
        # canonical lowercase UUID form (crony requires it)
        parsed = uuid.UUID(sa._CRONY.job_uuid("create"))
        assert str(parsed) == sa._CRONY.job_uuid("create")


class TestHelpWidth(HelpWidthBase):
    PROG = "secure-archiver"
    PARSER_FUNC = staticmethod(sa.build_parser)


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
