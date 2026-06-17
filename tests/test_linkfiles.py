#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov"]
# ///
# This is AI generated code

"""
Unit tests for the linkfiles mode.

linkfiles is the general engine the dotfiles / binfiles modes are
fixed-target presets of (all three are the same source; cli() picks the
mode from sys.argv[0]). The shared link/discover/stale machinery is
covered in test_dotfiles.py; this file focuses on what is unique to
linkfiles: the explicit-target CLI, the ~/.linkfiles.installed record
format (escaping, flags, sorting), and per-record install / audit /
remove / cleanup spanning distinct targets.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    CmdCallbacksBase,
    ExceptionHierarchyBase,
    isolate_home,
)

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# Imported under its own module name (bin/linkfiles is the real script)
# so this test gets ACTIVE_PROFILE state distinct from the dotfiles /
# binfiles test modules.
import linkfiles as lf  # noqa: E402

_script_path = REPO_ROOT / "bin" / "linkfiles"


@pytest.fixture(autouse=True)
def _activate(tmp_path: Path, monkeypatch: Any) -> None:
    """Run every test in linkfiles mode with an isolated, nonexistent
    home / tracking file (behavioral tests point INSTALLED_FILE at a
    writable path of their own)."""
    monkeypatch.setattr(lf, "ACTIVE_PROFILE", lf.LINKFILES_PROFILE)
    isolate_home(lf, ".linkfiles.installed", tmp_path, monkeypatch)


@pytest.fixture
def tracked(tmp_path: Path, monkeypatch: Any) -> Path:
    """A writable tracking file under tmp_path; returns tmp_path."""
    monkeypatch.setattr(lf, "INSTALLED_FILE", tmp_path / ".linkfiles.installed")
    return tmp_path


def _make_tree(root: Path, files: dict[str, str]) -> Path:
    """Create `files` (relative path -> contents) under root."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


class TestSelectProfile:
    """linkfiles is the fallthrough; only dotfiles / binfiles are
    special-cased to their fixed-target presets."""

    def test_linkfiles_name(self) -> None:
        assert lf.select_profile("linkfiles") is lf.LINKFILES_PROFILE

    def test_path_prefixed(self) -> None:
        assert (
            lf.select_profile("/usr/local/bin/linkfiles")
            is lf.LINKFILES_PROFILE
        )

    def test_unknown_name_is_linkfiles(self) -> None:
        assert lf.select_profile("anything-else") is lf.LINKFILES_PROFILE

    def test_presets_are_special_cased(self) -> None:
        assert lf.select_profile("dotfiles") is lf.DOTFILES_PROFILE
        assert lf.select_profile("binfiles") is lf.BINFILES_PROFILE


class TestFieldEscaping:
    """Record fields round-trip through escape/unescape, including the
    separator (tab), the escape char, and newlines."""

    @pytest.mark.parametrize(
        "value",
        [
            "/plain/path",
            "/has space/dir",
            "/tab\tin/path",
            "/newline\nin/path",
            "/back\\slash",
            "/all\t\n\\three",
        ],
    )
    def test_round_trip(self, value: str) -> None:
        assert lf._unescape_field(lf._escape_field(value)) == value

    def test_escaped_has_no_raw_separators(self) -> None:
        escaped = lf._escape_field("/a\tb\nc")
        assert "\t" not in escaped
        assert "\n" not in escaped

    def test_split_on_unescaped_tab_only(self) -> None:
        line = lf._escape_field("/a\tb") + "\t" + lf._escape_field("/c")
        fields = lf._split_escaped(line)
        assert len(fields) == 2
        assert lf._unescape_field(fields[0]) == "/a\tb"
        assert lf._unescape_field(fields[1]) == "/c"


class TestLinkRecord:
    """A record reconstitutes a concrete profile from its dst / flags."""

    def test_profile_target_is_dst(self) -> None:
        rec = lf.LinkRecord(
            dst=Path("/t"), src=Path("/s"), dotfiles=False, no_recurse=False
        )
        assert rec.profile().target_root() == Path("/t")

    def test_dotfiles_flag_dot_prefixes(self) -> None:
        rec = lf.LinkRecord(
            dst=Path("/t"), src=Path("/s"), dotfiles=True, no_recurse=False
        )
        assert rec.profile().transform_segment("rc") == ".rc"

    def test_plain_flag_is_identity(self) -> None:
        rec = lf.LinkRecord(
            dst=Path("/t"), src=Path("/s"), dotfiles=False, no_recurse=False
        )
        assert rec.profile().transform_segment("rc") == "rc"

    def test_no_recurse_sets_flat(self) -> None:
        rec = lf.LinkRecord(
            dst=Path("/t"), src=Path("/s"), dotfiles=False, no_recurse=True
        )
        assert rec.profile().flat is True

    def test_flag_tokens_order(self) -> None:
        rec = lf.LinkRecord(
            dst=Path("/t"), src=Path("/s"), dotfiles=True, no_recurse=True
        )
        assert rec.flag_tokens() == ["dotfiles", "no-recurse"]


@pytest.mark.usefixtures("tracked")
class TestTrackingIO:
    """~/.linkfiles.installed round-trips records, sorted by (dst, src),
    as <dst>\\t<src>[\\t<flags>]."""

    def test_save_load_round_trip(self) -> None:
        recs = [
            lf.LinkRecord(
                dst=Path("/share"),
                src=Path("/s1"),
                dotfiles=False,
                no_recurse=False,
            ),
            lf.LinkRecord(
                dst=Path("/home"),
                src=Path("/s2"),
                dotfiles=True,
                no_recurse=True,
            ),
        ]
        lf.save_link_records(recs)
        loaded = lf.load_link_records()
        assert {(r.dst, r.src, tuple(r.flag_tokens())) for r in loaded} == {
            (r.dst, r.src, tuple(r.flag_tokens())) for r in recs
        }

    def test_sorted_by_dst_then_src(self) -> None:
        lf.save_link_records(
            [
                lf.LinkRecord(
                    dst=Path("/z"),
                    src=Path("/a"),
                    dotfiles=False,
                    no_recurse=False,
                ),
                lf.LinkRecord(
                    dst=Path("/a"),
                    src=Path("/b"),
                    dotfiles=False,
                    no_recurse=False,
                ),
                lf.LinkRecord(
                    dst=Path("/a"),
                    src=Path("/a"),
                    dotfiles=False,
                    no_recurse=False,
                ),
            ]
        )
        lines = lf.INSTALLED_FILE.read_text().splitlines()
        assert lines == ["/a\t/a", "/a\t/b", "/z\t/a"]

    def test_flags_field_omitted_when_empty(self) -> None:
        lf.save_link_records(
            [
                lf.LinkRecord(
                    dst=Path("/t"),
                    src=Path("/s"),
                    dotfiles=False,
                    no_recurse=False,
                )
            ]
        )
        assert lf.INSTALLED_FILE.read_text().strip() == "/t\t/s"

    def test_flags_field_written_when_set(self) -> None:
        lf.save_link_records(
            [
                lf.LinkRecord(
                    dst=Path("/t"),
                    src=Path("/s"),
                    dotfiles=True,
                    no_recurse=True,
                )
            ]
        )
        assert (
            lf.INSTALLED_FILE.read_text().strip()
            == "/t\t/s\tdotfiles,no-recurse"
        )

    def test_path_with_tab_round_trips(self) -> None:
        rec = lf.LinkRecord(
            dst=Path("/t\tx"),
            src=Path("/s"),
            dotfiles=False,
            no_recurse=False,
        )
        lf.save_link_records([rec])
        assert lf.load_link_records()[0].dst == Path("/t\tx")

    def test_add_replaces_same_dst_src(self) -> None:
        lf.add_link_record(
            lf.LinkRecord(
                dst=Path("/t"),
                src=Path("/s"),
                dotfiles=False,
                no_recurse=False,
            )
        )
        lf.add_link_record(
            lf.LinkRecord(
                dst=Path("/t"), src=Path("/s"), dotfiles=True, no_recurse=False
            )
        )
        recs = lf.load_link_records()
        assert len(recs) == 1
        assert recs[0].dotfiles is True

    def test_remove_drops_record(self) -> None:
        lf.add_link_record(
            lf.LinkRecord(
                dst=Path("/t"),
                src=Path("/s"),
                dotfiles=False,
                no_recurse=False,
            )
        )
        lf.remove_link_record(Path("/t"), Path("/s"))
        assert lf.load_link_records() == []

    def test_malformed_line_skipped(self) -> None:
        lf.INSTALLED_FILE.write_text("no-tab-here\n/t\t/s\n")
        recs = lf.load_link_records()
        assert len(recs) == 1
        assert recs[0].src == Path("/s")


class TestLinkfilesInstall:
    """install <src> <dst> links recursively into the explicit target,
    records the install, and honors --dotfiles / --no-recurse."""

    def test_recursive_into_explicit_target(self, tracked: Path) -> None:
        src = _make_tree(tracked / "share", {"man/man1/foo.1": "page"})
        dst = tracked / "dest"
        lf.do_install_linkfiles(
            src,
            dst,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )
        link = dst / "man" / "man1" / "foo.1"
        assert link.is_symlink()
        assert link.resolve() == (src / "man" / "man1" / "foo.1").resolve()

    def test_records_the_install(self, tracked: Path) -> None:
        src = _make_tree(tracked / "share", {"x": "y"})
        dst = tracked / "dest"
        lf.do_install_linkfiles(
            src,
            dst,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )
        recs = lf.load_link_records()
        assert len(recs) == 1
        assert recs[0].src == src.resolve()
        assert recs[0].dst == dst.resolve()

    def test_dotfiles_flag_dot_prefixes_top_level(self, tracked: Path) -> None:
        src = _make_tree(tracked / "cfg", {"rc": "x", "sub/deep": "y"})
        dst = tracked / "home"
        lf.do_install_linkfiles(
            src,
            dst,
            dotfiles=True,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )
        assert (dst / ".rc").is_symlink()
        # Only the top-level segment is dot-prefixed.
        assert (dst / ".sub" / "deep").is_symlink()

    def test_no_recurse_links_top_level_only(self, tracked: Path) -> None:
        src = _make_tree(tracked / "bin", {"tool": "x", "sub/nested": "y"})
        dst = tracked / "dest"
        lf.do_install_linkfiles(
            src,
            dst,
            dotfiles=False,
            no_recurse=True,
            dry_run=False,
            force=False,
            verbose=False,
        )
        assert (dst / "tool").is_symlink()
        assert not (dst / "sub").exists()

    def test_links_non_executable_files(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"readme": "docs"})
        dst = tracked / "dest"
        lf.do_install_linkfiles(
            src,
            dst,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )
        assert (dst / "readme").is_symlink()

    def test_one_arg_without_target_errors(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y"})
        with pytest.raises(lf.UsageError):
            lf.do_install_linkfiles(
                src,
                None,
                dotfiles=False,
                no_recurse=False,
                dry_run=False,
                force=False,
                verbose=False,
            )

    def test_no_args_resyncs_all_tracked(self, tracked: Path) -> None:
        src1 = _make_tree(tracked / "s1", {"a": "1"})
        src2 = _make_tree(tracked / "s2", {"b": "2"})
        d1, d2 = tracked / "d1", tracked / "d2"
        for s, d in ((src1, d1), (src2, d2)):
            lf.do_install_linkfiles(
                s,
                d,
                dotfiles=False,
                no_recurse=False,
                dry_run=False,
                force=False,
                verbose=False,
            )
        # Delete the links; a no-arg install re-syncs every record.
        (d1 / "a").unlink()
        (d2 / "b").unlink()
        lf.do_install_linkfiles(
            None,
            None,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )
        assert (d1 / "a").is_symlink()
        assert (d2 / "b").is_symlink()


class TestLinkfilesDiscovery:
    """linkfiles shares the discovery rules: it honors .linkfiles.ignore
    (and the other ignore files) and skips every dotfile."""

    def _install(self, src: Path, dst: Path) -> None:
        lf.do_install_linkfiles(
            src,
            dst,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )

    def test_linkfiles_ignore_excludes(self, tracked: Path) -> None:
        src = _make_tree(
            tracked / "s",
            {
                "keep": "y",
                "skip.me": "z",
                ".linkfiles.ignore": "skip.me\n",
            },
        )
        dst = tracked / "d"
        self._install(src, dst)
        assert (dst / "keep").is_symlink()
        assert not (dst / "skip.me").exists()
        # The ignore file is itself a dotfile, so it is never linked.
        assert not (dst / ".linkfiles.ignore").exists()

    def test_skips_dotfiles_at_any_depth(self, tracked: Path) -> None:
        src = _make_tree(
            tracked / "s",
            {
                "visible": "y",
                "sub/visible": "y",
                "sub/.hidden": "z",
                ".top": "w",
            },
        )
        dst = tracked / "d"
        self._install(src, dst)
        assert (dst / "visible").is_symlink()
        assert (dst / "sub" / "visible").is_symlink()
        assert not (dst / "sub" / ".hidden").exists()
        assert not (dst / ".top").exists()


class TestLinkfilesAuditRemoveCleanup:
    """audit / remove / cleanup operate across every tracked record."""

    def _install(self, src: Path, dst: Path, **flags: bool) -> None:
        lf.do_install_linkfiles(
            src,
            dst,
            dotfiles=flags.get("dotfiles", False),
            no_recurse=flags.get("no_recurse", False),
            dry_run=False,
            force=False,
            verbose=False,
        )

    def test_audit_clean(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y"})
        self._install(src, tracked / "d")
        lf.do_audit_linkfiles(verbose=False)  # no raise

    def test_audit_flags_stale(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y", "gone": "z"})
        dst = tracked / "d"
        self._install(src, dst)
        # Remove a source file; its managed link is now stale.
        (src / "gone").unlink()
        with pytest.raises(lf.ConflictsFound):
            lf.do_audit_linkfiles(verbose=False)

    def test_remove_one_install(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y"})
        dst = tracked / "d"
        self._install(src, dst)
        lf.do_remove_linkfiles(src, dst, dry_run=False, verbose=False)
        assert not (dst / "x").exists()
        assert lf.load_link_records() == []

    def test_remove_untracked_errors(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y"})
        with pytest.raises(lf.MissingDotfilesDirectory):
            lf.do_remove_linkfiles(
                src, tracked / "never", dry_run=False, verbose=False
            )

    def test_cleanup_spans_distinct_targets(self, tracked: Path) -> None:
        src1 = _make_tree(tracked / "s1", {"a": "1"})
        src2 = _make_tree(tracked / "s2", {"b": "2"})
        d1, d2 = tracked / "d1", tracked / "d2"
        self._install(src1, d1)
        self._install(src2, d2)
        # Drop a file from each source: both links are now stale.
        (src1 / "a").unlink()
        (src2 / "b").unlink()
        lf.do_cleanup(dry_run=False)
        assert not (d1 / "a").exists()
        assert not (d2 / "b").exists()

    def test_cleanup_removes_dangling(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y"})
        dst = tracked / "d"
        self._install(src, dst)
        # A dangling link under a managed target is reclaimed.
        (dst / "stray").symlink_to(tracked / "missing")
        lf.do_cleanup(dry_run=False)
        assert not (dst / "stray").is_symlink()

    def test_cleanup_keeps_valid_links_for_same_source(
        self, tracked: Path
    ) -> None:
        # One source installed into two targets with different recurse
        # flags: cleanup must not reclaim a link that is valid under its
        # own record just because the other record's discovered set
        # omits it. Records load sorted by dst, so the no-recurse target
        # is named to sort last -- the order that, without the union,
        # would let its narrower set decide the recursive target's
        # nested link.
        src = _make_tree(tracked / "s", {"top": "y", "sub/nested": "z"})
        d_rec, d_flat = tracked / "d_a_recursive", tracked / "d_z_flat"
        self._install(src, d_rec)
        self._install(src, d_flat, no_recurse=True)
        lf.do_cleanup(dry_run=False)
        assert (d_rec / "top").is_symlink()
        assert (d_rec / "sub" / "nested").is_symlink()
        assert (d_flat / "top").is_symlink()


class TestCmdCallbacks(CmdCallbacksBase):
    """Reuse the generic CLI dispatch tests against the linkfiles
    callbacks / parser (the autouse fixture keeps the active profile on
    linkfiles so build_parser builds the linkfiles parser)."""

    CALLBACKS = lf.LINKFILES_COMMAND_CALLBACKS
    PARSER_FUNC = lf.build_parser
    CLI_FUNC = staticmethod(lf.cli)
    EXIT_CODE_USAGE = lf.ExitCode.USAGE
    TEST_SUBCOMMAND = "audit"
    CLI_ARGV0 = "linkfiles"
    EXCEPTION_EXIT_CODE_MAP = [
        (lf.ConflictsFound("t"), lf.ExitCode.CONFLICTS),
        (lf.UsageError("t"), lf.ExitCode.USAGE),
        (
            lf.MissingDotfilesDirectory("t"),
            lf.ExitCode.MISSING_DIR,
        ),
        (RuntimeError("t"), lf.ExitCode.CRASHED),
    ]


class TestExceptionHierarchy(ExceptionHierarchyBase):
    BASE_ERROR = lf.DotfilesError
    EXIT_CODE = lf.ExitCode
    EXCLUDED_CODES = {
        lf.ExitCode.SUCCESS,
        lf.ExitCode.WARNING,
        lf.ExitCode.CONFIG,
        lf.ExitCode.SUBPROCESS,
        lf.ExitCode.CRASHED,
    }


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
