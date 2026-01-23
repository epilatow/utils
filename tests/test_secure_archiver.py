#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov"]
# ///
# This is AI generated code

"""
Comprehensive unit tests for secure_archiver
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest  # type: ignore[import-not-found]

# Repository root directory (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent

# Import secure_archiver module from bin/ (works with or without .py extension)
_script_path = REPO_ROOT / "bin" / "secure-archiver"
if not _script_path.exists():
    _script_path = REPO_ROOT / "bin" / "secure-archiver.py"
_loader = importlib.machinery.SourceFileLoader(
    "secure_archiver", str(_script_path)
)
_spec = importlib.util.spec_from_loader("secure_archiver", _loader)
assert _spec and _spec.loader
sa = importlib.util.module_from_spec(_spec)
sys.modules["secure_archiver"] = sa
_spec.loader.exec_module(sa)


class TestArgumentParser:
    """Test argument parser structure."""

    def test_parser_builds_successfully(self) -> None:
        """Verify parser can be built without errors."""
        parser = sa.build_parser()
        assert parser is not None

    def test_all_subcommands_have_help(self) -> None:
        """Smoke test: verify subcommands can show help."""
        parser = sa.build_parser()
        # If this doesn't raise SystemExit, the subcommand is broken
        with pytest.raises(SystemExit):
            parser.parse_args(["create", "--help"])
        with pytest.raises(SystemExit):
            parser.parse_args(["self-test", "--help"])
        with pytest.raises(SystemExit):
            parser.parse_args(["write-example-config", "--help"])
        with pytest.raises(SystemExit):
            parser.parse_args(["check-config", "--help"])


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

    def test_expand_with_tilde(self, monkeypatch: Any) -> None:
        """Test expansion of ~ in path."""
        result = sa.expand_pattern("~/test*.txt")
        # Result should be absolute paths starting from home
        assert all(p.is_absolute() for p in result)


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
        with patch.object(sa, "op_read", return_value="secret content\n"):
            result = sa.stage_op_ref(
                staging,
                "secret.txt",
                "op://vault/item/field",
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
        with patch.object(sa, "op_read", return_value="content1\n"):
            sa.stage_op_ref(
                staging,
                "secret.txt",
                "op://vault/item1/field",
                seen_names=seen,
                target_dir="dir1",
            )

        with patch.object(sa, "op_read", return_value="content2\n"):
            sa.stage_op_ref(
                staging,
                "secret.txt",
                "op://vault/item2/field",
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
        """Test error when pattern matches nothing."""
        entry = sa.PathIncludeEntry(path=str(tmp_path / "nonexistent*.txt"))

        with pytest.raises(FileNotFoundError, match="No matches"):
            sa.include_entry_to_sources(entry)

    def test_include_zero_files_raises_error(self, tmp_path: Path) -> None:
        """Test error when match results in zero files."""
        # Create empty directory
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        entry = sa.PathIncludeEntry(path=str(empty_dir))

        with pytest.raises(FileNotFoundError, match="zero files"):
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
        manifest: Dict[str, Any] = {
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


class TestArchiveIntegration:
    """Integration tests for full archive workflow."""

    @patch("secure_archiver.op_read", autospec=True)
    def test_build_staging_for_archive(
        self, mock_op_read: MagicMock, tmp_path: Path
    ) -> None:
        """Test building staging directory for archive."""
        # Setup test files
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "file1.txt").write_text("content1")
        (src_dir / "file2.txt").write_text("content2")

        staging = tmp_path / "staging"
        staging.mkdir()

        # Mock op_read for op_ref entries
        mock_op_read.return_value = "note content"

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

        result = sa.build_staging_for_archive(archive_cfg, staging)

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

    @patch("secure_archiver.op_read", autospec=True)
    def test_build_staging_normalizes_line_endings(
        self, mock_op_read: MagicMock, tmp_path: Path
    ) -> None:
        """Test that op_ref entries normalize line endings."""
        staging = tmp_path / "staging"
        staging.mkdir()

        # Mock op_read with Windows line endings
        mock_op_read.return_value = "line1\r\nline2\r\nline3"

        archive_cfg = sa.ArchiveConfig(
            op_password="op://vault/item/password",
            description="Test archive",
            include=[
                sa.OpRefIncludeEntry(
                    op_ref="op://vault/item/field", filename="note.txt"
                )
            ],
        )

        sa.build_staging_for_archive(archive_cfg, staging)

        content = (staging / "note.txt").read_text()
        assert "\r" not in content
        assert content == "line1\nline2\nline3\n"

    @patch("secure_archiver.op_read", autospec=True)
    def test_build_staging_with_dir_parameter(
        self, mock_op_read: MagicMock, tmp_path: Path
    ) -> None:
        """Test building staging with files in subdirectories."""
        # Setup test files
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "doc1.txt").write_text("doc content")
        (src_dir / "doc2.txt").write_text("doc content 2")

        staging = tmp_path / "staging"
        staging.mkdir()

        # Mock op_read for op_ref entries
        mock_op_read.return_value = "secret content"

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

        result = sa.build_staging_for_archive(archive_cfg, staging)

        # Should have all files with correct paths
        assert len(result) == 3
        assert "doc1.txt" in result
        assert "documents/doc2.txt" in result
        assert "secrets/secret.txt" in result

        # Verify files were staged in correct locations
        assert (staging / "doc1.txt").exists()
        assert (staging / "documents" / "doc2.txt").exists()
        assert (staging / "secrets" / "secret.txt").exists()

    @patch("secure_archiver.op_read", autospec=True)
    def test_build_staging_multiple_files_same_dir(
        self, mock_op_read: MagicMock, tmp_path: Path
    ) -> None:
        """Test that multiple entries can use the same dir."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "file1.txt").write_text("content1")
        (src_dir / "file2.txt").write_text("content2")

        staging = tmp_path / "staging"
        staging.mkdir()

        mock_op_read.return_value = "op content"

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

        result = sa.build_staging_for_archive(archive_cfg, staging)

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
            staging_dir: Path,
            out_archive: Path,
            password: str,
            filenames: list[str],
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
            staging_dir: Path,
            out_archive: Path,
            password: str,
            filenames: list[str],
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
        mock_make: MagicMock,
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
            staging_dir: Path,
            out_archive: Path,
            password: str,
            filenames: list[str],
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

    def test_ensure_out_dir_exists_writable_success(
        self, tmp_path: Path
    ) -> None:
        """Test validation passes for valid directory."""
        # Should not raise
        sa.ensure_out_dir_exists_writable(tmp_path)

    def test_ensure_out_dir_not_exist(self, tmp_path: Path) -> None:
        """Test error when output dir doesn't exist."""
        nonexistent = tmp_path / "nonexistent"

        with pytest.raises(sa.ConfigError, match="does not exist"):
            sa.ensure_out_dir_exists_writable(nonexistent)

    def test_ensure_out_dir_not_directory(self, tmp_path: Path) -> None:
        """Test error when output dir is a file."""
        not_dir = tmp_path / "file.txt"
        not_dir.write_text("content")

        with pytest.raises(sa.ConfigError, match="not a directory"):
            sa.ensure_out_dir_exists_writable(not_dir)

    def test_ensure_out_dir_not_writable(self, tmp_path: Path) -> None:
        """Test error when output dir is not writable."""
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(0o555)

        try:
            with pytest.raises(sa.ConfigError, match="not writable"):
                sa.ensure_out_dir_exists_writable(readonly)
        finally:
            # Restore permissions for cleanup
            readonly.chmod(0o755)


class TestCommandExecution:
    """Test command execution helpers."""

    def test_run_cmd_success(self) -> None:
        """Test successful command execution."""
        result = sa.run_cmd(["echo", "hello"])

        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_run_cmd_failure_check_true(self) -> None:
        """Test command failure with check=True."""
        import subprocess

        with pytest.raises(subprocess.CalledProcessError):
            sa.run_cmd(["false"], check=True)

    def test_run_cmd_failure_check_false(self) -> None:
        """Test command failure with check=False."""
        result = sa.run_cmd(["false"], check=False)

        assert result.returncode != 0

    @patch("secure_archiver.run_cmd", autospec=True)
    def test_op_read(self, mock_run_cmd: MagicMock) -> None:
        """Test reading from 1Password."""
        mock_run_cmd.return_value = MagicMock(
            stdout="secret_value\n", returncode=0
        )

        result = sa.op_read("op://vault/item/field")

        assert result == "secret_value"
        assert mock_run_cmd.called
        cmd = mock_run_cmd.call_args[0][0]
        assert cmd[0] == "op"
        assert cmd[1] == "read"

    @patch("secure_archiver.op_read", autospec=True)
    def test_stage_op_ref_empty_content_raises(
        self, mock_op_read: MagicMock, tmp_path: Path
    ) -> None:
        """Test stage_op_ref raises ConfigError on empty content."""
        mock_op_read.return_value = ""
        seen: set[str] = set()

        with pytest.raises(sa.ConfigError, match="empty content"):
            sa.stage_op_ref(
                tmp_path, "test.txt", "op://vault/item", seen_names=seen
            )

    @patch("secure_archiver.op_read", autospec=True)
    def test_stage_op_ref_whitespace_only_raises(
        self, mock_op_read: MagicMock, tmp_path: Path
    ) -> None:
        """Test stage_op_ref raises ConfigError on whitespace-only content."""
        mock_op_read.return_value = "   \n\t\n   "
        seen: set[str] = set()

        with pytest.raises(sa.ConfigError, match="empty content"):
            sa.stage_op_ref(
                tmp_path, "test.txt", "op://vault/item", seen_names=seen
            )


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

    @patch("secure_archiver.op_read", autospec=True)
    def test_run_update_creates_archives(
        self, mock_op_read: MagicMock, tmp_path: Path
    ) -> None:
        """Test that run_update creates archives from config."""
        import shutil

        if shutil.which("7zz") is None:
            pytest.skip("7zz not available")

        # Mock op_read to return a test password
        mock_op_read.return_value = "test_password"

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
        cfg = sa.load_config(config_file)
        sa.do_update(cfg, dry_run=False, force_update=False)

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

    @patch("secure_archiver.op_read", autospec=True)
    def test_run_update_creates_readme(
        self, mock_op_read: MagicMock, tmp_path: Path
    ) -> None:
        """Test that run_update creates readme alongside archive."""
        import shutil

        if shutil.which("7zz") is None:
            pytest.skip("7zz not available")

        mock_op_read.return_value = "test_password"

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

        cfg = sa.load_config(config_file)
        sa.do_update(cfg, dry_run=False, force_update=False)

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


class TestRunTests:
    """Test do_self_test function."""

    @patch("secure_archiver.run_cmd", autospec=True)
    def test_run_tests_basic(self, mock_run_cmd: MagicMock) -> None:
        """Test do_self_test executes pytest via uvx."""
        mock_run_cmd.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        sa.do_self_test(verbose=False, coverage=False)

        assert mock_run_cmd.called
        # Verify command structure
        cmd = mock_run_cmd.call_args[0][0]
        assert "uvx" in cmd
        assert "pytest" in cmd

    @patch("secure_archiver.run_cmd", autospec=True)
    def test_run_tests_with_verbose(self, mock_run_cmd: MagicMock) -> None:
        """Test do_self_test passes verbose flag to pytest."""
        mock_run_cmd.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        sa.do_self_test(verbose=True, coverage=False)

        cmd = mock_run_cmd.call_args[0][0]
        assert "-v" in cmd

    @patch("secure_archiver.run_cmd", autospec=True)
    def test_run_tests_with_coverage(self, mock_run_cmd: MagicMock) -> None:
        """Test do_self_test passes coverage flags to pytest."""
        mock_run_cmd.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        sa.do_self_test(verbose=False, coverage=True)

        cmd = mock_run_cmd.call_args[0][0]
        assert "--cov=secure_archiver" in cmd
        assert "--with" in cmd
        assert "pytest-cov" in cmd

    @patch("secure_archiver.run_cmd", autospec=True)
    def test_run_tests_raises_on_failure(self, mock_run_cmd: MagicMock) -> None:
        """Test do_self_test raises TestError when tests fail."""
        mock_run_cmd.return_value = MagicMock(
            returncode=1, stdout="FAILED", stderr=""
        )

        with pytest.raises(sa.TestError, match="Tests failed"):
            sa.do_self_test(verbose=False, coverage=False)


class TestMain:
    """Test main function and command dispatch."""

    @patch("secure_archiver.do_self_test", autospec=True)
    def test_main_code_test_command(self, mock_do_self_test: MagicMock) -> None:
        """Test main dispatches to do_self_test for self-test command."""
        args = argparse.Namespace(
            command="self-test", verbose=False, coverage=False
        )

        sa.main(args)

        assert mock_do_self_test.called

    @patch("secure_archiver.do_update", autospec=True)
    @patch("secure_archiver.load_config", autospec=True)
    def test_main_create_command(
        self, mock_load_config: MagicMock, mock_do_update: MagicMock
    ) -> None:
        """Test main dispatches to do_update for create command."""
        mock_load_config.return_value = {"general": {}, "archive": {}}
        args = argparse.Namespace(
            command="create", config=None, dry_run=False, force_update=False
        )

        sa.main(args)

        assert mock_do_update.called
        assert mock_load_config.called

    @patch("secure_archiver.do_write_example_config", autospec=True)
    def test_main_write_example_config_command(
        self, mock_do_write_example_config: MagicMock
    ) -> None:
        """Test main dispatches to do_write_example_config."""
        args = argparse.Namespace(
            command="write-example-config", output_file=Path("/tmp/test.toml")
        )

        sa.main(args)

        assert mock_do_write_example_config.called
        # Verify the path argument was passed correctly
        call_args = mock_do_write_example_config.call_args
        assert call_args[0][0] == Path("/tmp/test.toml")

    @patch("secure_archiver.do_check_config", autospec=True)
    def test_main_check_config_command(
        self, mock_do_check_config: MagicMock
    ) -> None:
        """Test main dispatches to do_check_config."""
        args = argparse.Namespace(
            command="check-config", config=Path("/tmp/test.toml")
        )

        sa.main(args)

        assert mock_do_check_config.called
        # Verify the config argument was passed correctly
        call_args = mock_do_check_config.call_args
        assert call_args[0][0] == Path("/tmp/test.toml")

    def test_main_no_command(self) -> None:
        """Test main raises UsageError if no subcommand is specified."""
        args = argparse.Namespace(command=None)

        with pytest.raises(sa.UsageError, match="No subcommand"):
            sa.main(args)


class TestCli:
    """Test cli() function argument parsing and exception handling."""

    @patch("secure_archiver.main", autospec=True)
    def test_cli_returns_zero_on_success(self, mock_main: MagicMock) -> None:
        """Test cli() returns 0 when main() succeeds."""
        with patch("sys.argv", ["prog", "self-test"]):
            result = sa.cli()
        assert result == 0
        assert mock_main.called

    @patch("secure_archiver.main", autospec=True)
    def test_cli_handles_usage_error(self, mock_main: MagicMock) -> None:
        """Test cli() catches UsageError and returns exit code 1."""
        mock_main.side_effect = sa.UsageError("test usage error")
        with patch("sys.argv", ["prog", "self-test"]):
            result = sa.cli()
        assert result == 1

    @patch("secure_archiver.main", autospec=True)
    def test_cli_handles_test_error(self, mock_main: MagicMock) -> None:
        """Test cli() catches TestError and returns exit code 5."""
        mock_main.side_effect = sa.TestError("test error")
        with patch("sys.argv", ["prog", "self-test"]):
            result = sa.cli()
        assert result == 5

    @patch("secure_archiver.main", autospec=True)
    def test_cli_handles_config_error(self, mock_main: MagicMock) -> None:
        """Test cli() catches ConfigError and returns exit code 2."""
        mock_main.side_effect = sa.ConfigError("test config error")
        with patch("sys.argv", ["prog", "self-test"]):
            result = sa.cli()
        assert result == 2

    @patch("secure_archiver.main", autospec=True)
    def test_cli_handles_file_not_found_error(
        self, mock_main: MagicMock
    ) -> None:
        """Test cli() catches FileNotFoundError and returns exit code 3."""
        mock_main.side_effect = FileNotFoundError("test file not found")
        with patch("sys.argv", ["prog", "self-test"]):
            result = sa.cli()
        assert result == 3

    @patch("secure_archiver.main", autospec=True)
    def test_cli_handles_called_process_error(
        self, mock_main: MagicMock
    ) -> None:
        """Test cli() catches CalledProcessError and returns exit code 4."""
        import subprocess

        mock_main.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["test", "command"],
            output="stdout output",
            stderr="stderr output",
        )
        with patch("sys.argv", ["prog", "self-test"]):
            result = sa.cli()
        assert result == 4

    @patch("secure_archiver.main", autospec=True)
    def test_cli_handles_unexpected_error(self, mock_main: MagicMock) -> None:
        """Test cli() catches unexpected exceptions and returns exit code 5."""
        mock_main.side_effect = RuntimeError("unexpected error")
        with patch("sys.argv", ["prog", "self-test"]):
            result = sa.cli()
        assert result == 5

    @patch("secure_archiver.main", autospec=True)
    def test_cli_handles_collision_error(self, mock_main: MagicMock) -> None:
        """Test cli() catches CollisionError and returns exit code 5."""
        mock_main.side_effect = sa.CollisionError("test collision error")
        with patch("sys.argv", ["prog", "self-test"]):
            result = sa.cli()
        assert result == 5

    def test_cli_invalid_command(self) -> None:
        """Test cli() returns 1 for invalid subcommand."""
        with patch("sys.argv", ["prog", "foobar"]):
            result = sa.cli()
        assert result == 1

    def test_cli_help_returns_zero(self) -> None:
        """Test cli() returns 0 for --help."""
        with patch("sys.argv", ["prog", "--help"]):
            result = sa.cli()
        assert result == 0


class TestWriteExampleConfig:
    """Test write-example-config subcommand."""

    def test_writes_example_config(self, tmp_path: Path) -> None:
        """Test that write-example-config creates a file."""
        output_file = tmp_path / "example.toml"
        sa.do_write_example_config(output_file)
        assert output_file.exists()
        content = output_file.read_text()
        assert "[general]" in content
        assert "[archive." in content

    def test_fails_if_file_exists(self, tmp_path: Path) -> None:
        """Test that write-example-config raises if file already exists."""
        output_file = tmp_path / "example.toml"
        output_file.write_text("existing content")
        with pytest.raises(sa.ConfigError, match="already exists"):
            sa.do_write_example_config(output_file)
        # Original content should be preserved
        assert output_file.read_text() == "existing content"

    def test_generated_config_is_valid(self, tmp_path: Path) -> None:
        """Test that the generated example config passes validation."""
        output_file = tmp_path / "example.toml"
        sa.do_write_example_config(output_file)

        # Load config - validation happens inside load_config
        # If it returns without raising, the config is valid
        cfg = sa.load_config(output_file)
        assert isinstance(cfg, sa.Config)
        assert cfg.general.output_dir == "~/secure_archives"


class TestCheckConfig:
    """Test check-config subcommand."""

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
        sa.do_check_config(config_file)  # Should not raise

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

    def test_missing_archive_section(self, tmp_path: Path) -> None:
        """Test that missing archive section is caught."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("""
[general]
output_dir = "~/archives"
""")
        with pytest.raises(sa.ConfigError):
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
        sa.do_check_config(config_file)  # Should not raise

    def test_nonexistent_config_file(self, tmp_path: Path) -> None:
        """Test that nonexistent config file raises ConfigError."""
        config_file = tmp_path / "nonexistent.toml"
        with pytest.raises(sa.ConfigError):
            sa.do_check_config(config_file)

    def test_invalid_toml_syntax(self, tmp_path: Path) -> None:
        """Test that invalid TOML syntax raises ConfigError."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("this is not valid [[[toml")
        with pytest.raises(sa.ConfigError):
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
        sa.do_check_config(config_file)  # Should not raise

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
        sa.do_check_config(config_file)  # Should not raise

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)

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
            sa.do_check_config(config_file)


class TestConfigFromDict:
    """Test Config.from_dict() validation."""

    def test_negative_keep_revisions(self, tmp_path: Path) -> None:
        """Test that negative keep_revisions is caught."""
        cfg_dict: Dict[str, Any] = {
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
        cfg_dict: Dict[str, Any] = {
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
        cfg_dict: Dict[str, Any] = {
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
        cfg_dict: Dict[str, Any] = {
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
        cfg_dict: Dict[str, Any] = {
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
        cfg_dict: Dict[str, Any] = {
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
                str(REPO_ROOT / "tests" / "test_secure_archiver.py"),
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
                str(REPO_ROOT / "tests" / "test_secure_archiver.py"),
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
        import os
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
                str(REPO_ROOT / "tests" / "test_secure_archiver.py"),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"mypy check failed:\n{result.stdout}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
