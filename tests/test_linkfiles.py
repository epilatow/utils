#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov"]
# ///
# This is AI generated code

"""
Unit tests for linkfiles.

linkfiles symlinks a source dir's entries into an explicit target. This
file covers the engine end to end: the explicit-target CLI, the
~/.linkfiles.installed record format (escaping, flags, sorting), the
discovery rules (ignore files, dotfile-skipping), and per-record install
/ audit / remove spanning distinct targets.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    CmdCallbacksBase,
    ExceptionHierarchyBase,
    HelpWidthBase,
    isolate_home,
)

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import linkfiles as lf  # noqa: E402

_script_path = REPO_ROOT / "bin" / "linkfiles"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: Any) -> None:
    """Run every test with an isolated, nonexistent home / tracking
    files (behavioral tests point the tracking files at writable paths
    of their own)."""
    isolate_home(lf, ".linkfiles.installed", tmp_path, monkeypatch)
    monkeypatch.setattr(lf, "LINKED_FILE", Path.home() / ".linkfiles.linked")


@pytest.fixture
def tracked(tmp_path: Path, monkeypatch: Any) -> Path:
    """Writable tracking files under tmp_path; returns tmp_path."""
    monkeypatch.setattr(lf, "INSTALLED_FILE", tmp_path / ".linkfiles.installed")
    monkeypatch.setattr(lf, "LINKED_FILE", tmp_path / ".linkfiles.linked")
    return tmp_path


def _make_tree(root: Path, files: dict[str, str]) -> Path:
    """Create `files` (relative path -> contents) under root."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


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
        assert lf.LineRecord.decode(lf.LineRecord.encode(value)) == value

    def test_escaped_has_no_raw_separators(self) -> None:
        escaped = lf.LineRecord.encode("/a\tb\nc")
        assert "\t" not in escaped
        assert "\n" not in escaped

    def test_split_on_unescaped_tab_only(self) -> None:
        line = lf.LineRecord.encode("/a\tb") + "\t" + lf.LineRecord.encode("/c")
        fields = lf.LineRecord._split(line)
        assert len(fields) == 2
        assert lf.LineRecord.decode(fields[0]) == "/a\tb"
        assert lf.LineRecord.decode(fields[1]) == "/c"


class TestInstallRecord:
    """Flag tokens serialize in a stable order."""

    def test_flag_tokens_order(self) -> None:
        rec = lf.InstallRecord(
            tgt=Path("/t"), src=Path("/s"), dotfiles=True, no_recurse=True
        )
        assert rec.flag_tokens() == ["dotfiles", "no-recurse"]


class TestTargetPath:
    """_target_path places a source entry's relative path under the
    install's tgt, dot-prefixing the top-level component under dotfiles."""

    def _tgt(self, rel: str, **flags: bool) -> Path:
        install = lf.InstallRecord(
            tgt=Path("/t"),
            src=Path("/s"),
            dotfiles=flags.get("dotfiles", False),
            no_recurse=flags.get("no_recurse", False),
        )
        return lf._target_path(install, Path(rel))

    def test_target_is_under_tgt(self) -> None:
        assert self._tgt("bin/x") == Path("/t/bin/x")

    def test_dotfiles_dot_prefixes_top_level(self) -> None:
        assert self._tgt("rc", dotfiles=True) == Path("/t/.rc")

    def test_dotfiles_prefixes_only_top_level(self) -> None:
        assert self._tgt("rc/sub", dotfiles=True) == Path("/t/.rc/sub")

    def test_plain_is_identity(self) -> None:
        assert self._tgt("rc") == Path("/t/rc")


@pytest.mark.usefixtures("tracked")
class TestTrackingIO:
    """~/.linkfiles.installed round-trips records, sorted by (tgt, src),
    as <tgt>\\t<src>[\\t<flags>]."""

    def test_save_load_round_trip(self) -> None:
        recs = [
            lf.InstallRecord(
                tgt=Path("/share"),
                src=Path("/s1"),
                dotfiles=False,
                no_recurse=False,
            ),
            lf.InstallRecord(
                tgt=Path("/home"),
                src=Path("/s2"),
                dotfiles=True,
                no_recurse=True,
            ),
        ]
        lf.save_install_records(recs)
        loaded = lf.load_install_records()
        assert {(r.tgt, r.src, tuple(r.flag_tokens())) for r in loaded} == {
            (r.tgt, r.src, tuple(r.flag_tokens())) for r in recs
        }

    def test_sorted_by_tgt_then_src(self) -> None:
        lf.save_install_records(
            [
                lf.InstallRecord(
                    tgt=Path("/z"),
                    src=Path("/a"),
                    dotfiles=False,
                    no_recurse=False,
                ),
                lf.InstallRecord(
                    tgt=Path("/a"),
                    src=Path("/b"),
                    dotfiles=False,
                    no_recurse=False,
                ),
                lf.InstallRecord(
                    tgt=Path("/a"),
                    src=Path("/a"),
                    dotfiles=False,
                    no_recurse=False,
                ),
            ]
        )
        lines = lf.INSTALLED_FILE.read_text().splitlines()
        assert lines == ["/a\t/a", "/a\t/b", "/z\t/a"]

    def test_flags_field_omitted_when_empty(self) -> None:
        lf.save_install_records(
            [
                lf.InstallRecord(
                    tgt=Path("/t"),
                    src=Path("/s"),
                    dotfiles=False,
                    no_recurse=False,
                )
            ]
        )
        assert lf.INSTALLED_FILE.read_text().strip() == "/t\t/s"

    def test_flags_field_written_when_set(self) -> None:
        lf.save_install_records(
            [
                lf.InstallRecord(
                    tgt=Path("/t"),
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
        rec = lf.InstallRecord(
            tgt=Path("/t\tx"),
            src=Path("/s"),
            dotfiles=False,
            no_recurse=False,
        )
        lf.save_install_records([rec])
        assert lf.load_install_records()[0].tgt == Path("/t\tx")

    def test_malformed_line_skipped(self) -> None:
        lf.INSTALLED_FILE.write_text("no-tab-here\n/t\t/s\n")
        recs = lf.load_install_records()
        assert len(recs) == 1
        assert recs[0].src == Path("/s")


class TestLinkfilesInstall:
    """install <src> <tgt> links recursively into the explicit target,
    records the install, and honors --dotfiles / --no-recurse."""

    def _install(self, src: Path, tgt: Path) -> None:
        lf.do_install(
            src,
            tgt,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )

    def test_recursive_into_explicit_target(self, tracked: Path) -> None:
        src = _make_tree(tracked / "share", {"man/man1/foo.1": "page"})
        tgt = tracked / "dest"
        lf.do_install(
            src,
            tgt,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )
        link = tgt / "man" / "man1" / "foo.1"
        assert link.is_symlink()
        assert link.resolve() == (src / "man" / "man1" / "foo.1").resolve()

    def test_records_the_install(self, tracked: Path) -> None:
        src = _make_tree(tracked / "share", {"x": "y"})
        tgt = tracked / "dest"
        lf.do_install(
            src,
            tgt,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )
        recs = lf.load_install_records()
        assert len(recs) == 1
        assert recs[0].src == src.resolve()
        assert recs[0].tgt == tgt.resolve()

    def test_dotfiles_flag_dot_prefixes_top_level(self, tracked: Path) -> None:
        src = _make_tree(tracked / "cfg", {"rc": "x", "sub/deep": "y"})
        tgt = tracked / "home"
        lf.do_install(
            src,
            tgt,
            dotfiles=True,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )
        assert (tgt / ".rc").is_symlink()
        # Only the top-level segment is dot-prefixed.
        assert (tgt / ".sub" / "deep").is_symlink()

    def test_no_recurse_links_top_level_only(self, tracked: Path) -> None:
        src = _make_tree(tracked / "bin", {"tool": "x", "sub/nested": "y"})
        tgt = tracked / "dest"
        lf.do_install(
            src,
            tgt,
            dotfiles=False,
            no_recurse=True,
            dry_run=False,
            force=False,
            verbose=False,
        )
        assert (tgt / "tool").is_symlink()
        assert not (tgt / "sub").exists()

    def test_links_non_executable_files(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"readme": "docs"})
        tgt = tracked / "dest"
        lf.do_install(
            src,
            tgt,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )
        assert (tgt / "readme").is_symlink()

    def test_one_arg_without_target_errors(self) -> None:
        # Arg-combination validation lives in the parser, so a half-named
        # pair exits 2 before any command dispatch.
        with pytest.raises(SystemExit) as exc:
            lf.build_parser().parse_command(["install", "only-src"])
        assert exc.value.code == 2

    def test_no_args_resyncs_all_tracked(self, tracked: Path) -> None:
        src1 = _make_tree(tracked / "s1", {"a": "1"})
        src2 = _make_tree(tracked / "s2", {"b": "2"})
        d1, d2 = tracked / "d1", tracked / "d2"
        for s, d in ((src1, d1), (src2, d2)):
            lf.do_install(
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
        lf.do_install(
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

    def test_rejects_source_nested_in_tracked_source(
        self, tracked: Path
    ) -> None:
        # A source dir nested within an already-tracked install's source
        # is refused: it would make a link's source ambiguous between the
        # two installs.
        outer = _make_tree(tracked / "outer", {"sub/x": "y"})
        self._install(outer, tracked / "d1")
        with pytest.raises(lf.LinkfilesError):
            self._install(outer / "sub", tracked / "d2")

    def test_allows_nested_targets_with_distinct_sources(
        self, tracked: Path
    ) -> None:
        # Distinct (non-nested) sources may link into nested targets.
        s1 = _make_tree(tracked / "s1", {"a": "1"})
        s2 = _make_tree(tracked / "s2", {"b": "2"})
        self._install(s1, tracked / "t")
        self._install(s2, tracked / "t" / "nested")
        assert (tracked / "t" / "a").is_symlink()
        assert (tracked / "t" / "nested" / "b").is_symlink()


class TestLinkfilesDiscovery:
    """linkfiles shares the discovery rules: it honors .linkfiles.ignore
    (and the other ignore files) and skips every dotfile."""

    def _install(self, src: Path, tgt: Path) -> None:
        lf.do_install(
            src,
            tgt,
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
        tgt = tracked / "d"
        self._install(src, tgt)
        assert (tgt / "keep").is_symlink()
        assert not (tgt / "skip.me").exists()
        # The ignore file is itself a dotfile, so it is never linked.
        assert not (tgt / ".linkfiles.ignore").exists()

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
        tgt = tracked / "d"
        self._install(src, tgt)
        assert (tgt / "visible").is_symlink()
        assert (tgt / "sub" / "visible").is_symlink()
        assert not (tgt / "sub" / ".hidden").exists()
        assert not (tgt / ".top").exists()


class TestLinkfilesAuditRemove:
    """audit / remove / re-sync operate across every tracked record."""

    def _install(self, src: Path, tgt: Path, **flags: bool) -> None:
        lf.do_install(
            src,
            tgt,
            dotfiles=flags.get("dotfiles", False),
            no_recurse=flags.get("no_recurse", False),
            dry_run=False,
            force=False,
            verbose=False,
        )

    def test_audit_clean(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y"})
        self._install(src, tracked / "d")
        lf.do_audit(verbose=False)  # no raise

    def test_audit_flags_stale(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y", "gone": "z"})
        tgt = tracked / "d"
        self._install(src, tgt)
        # Remove a source file; its managed link is now stale.
        (src / "gone").unlink()
        with pytest.raises(lf.ConflictsFound):
            lf.do_audit(verbose=False)

    def test_remove_one_install(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        lf.do_remove(src, tgt, all_installs=False, dry_run=False, verbose=False)
        assert not (tgt / "x").exists()
        assert lf.load_install_records() == []

    def test_remove_one_of_several_keeps_the_rest(self, tracked: Path) -> None:
        src1 = _make_tree(tracked / "s1", {"a": "1"})
        src2 = _make_tree(tracked / "s2", {"b": "2"})
        d1, d2 = tracked / "d1", tracked / "d2"
        self._install(src1, d1)
        self._install(src2, d2)
        lf.do_remove(src1, d1, all_installs=False, dry_run=False, verbose=False)
        assert not (d1 / "a").exists()  # the removed install's link is gone
        assert (d2 / "b").is_symlink()  # the other install is untouched
        remaining = lf.load_install_records()
        assert [(r.tgt, r.src) for r in remaining] == [(d2, src2)]

    def test_remove_untracked_errors(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y"})
        with pytest.raises(lf.LinkfilesError):
            lf.do_remove(
                src,
                tracked / "never",
                all_installs=False,
                dry_run=False,
                verbose=False,
            )

    def test_remove_all_removes_every_install(self, tracked: Path) -> None:
        src1 = _make_tree(tracked / "s1", {"a": "1"})
        src2 = _make_tree(tracked / "s2", {"b": "2"})
        d1, d2 = tracked / "d1", tracked / "d2"
        self._install(src1, d1)
        self._install(src2, d2)
        lf.do_remove(
            None, None, all_installs=True, dry_run=False, verbose=False
        )
        assert not (d1 / "a").exists()
        assert not (d2 / "b").exists()
        assert lf.load_install_records() == []

    def test_bare_remove_errors(self) -> None:
        # A bare `remove` must not wipe every install; the parser
        # rejects it (needs --all or a source/target pair) with exit 2.
        with pytest.raises(SystemExit) as exc:
            lf.build_parser().parse_command(["remove"])
        assert exc.value.code == 2

    def test_remove_all_with_pair_errors(self) -> None:
        with pytest.raises(SystemExit) as exc:
            lf.build_parser().parse_command(["remove", "--all", "s", "d"])
        assert exc.value.code == 2

    def test_remove_half_pair_errors(self) -> None:
        with pytest.raises(SystemExit) as exc:
            lf.build_parser().parse_command(["remove", "only-src"])
        assert exc.value.code == 2

    def test_remove_missing_source_cleans_dangling_and_untracks(
        self, tracked: Path
    ) -> None:
        # The source has vanished, so its links are dangling. remove drives
        # cleanup from the ledger (not source discovery), so it deletes the
        # dangling link and untracks the install instead of leaving orphans.
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        link = tgt / "x"
        shutil.rmtree(src)
        assert link.is_symlink()  # dangling but still present
        result = lf.do_remove(
            src, tgt, all_installs=False, dry_run=False, verbose=False
        )
        assert not link.is_symlink()  # dangling link cleaned up
        assert lf.load_install_records() == []
        assert result is None

    def test_remove_missing_target_untracks(self, tracked: Path) -> None:
        # The whole target tree is gone, so the ledger's links are already
        # absent; remove warns per link, untracks the install, and does not
        # error.
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        shutil.rmtree(tgt)
        result = lf.do_remove(
            src, tgt, all_installs=False, dry_run=False, verbose=False
        )
        assert lf.load_install_records() == []
        assert result is None

    def test_remove_all_cleans_missing_and_present(self, tracked: Path) -> None:
        # remove --all tears down every install -- the one whose source
        # vanished (its dangling links cleaned via the ledger) and the
        # present one -- instead of aborting on the first.
        src1 = _make_tree(tracked / "s1", {"a": "1"})
        src2 = _make_tree(tracked / "s2", {"b": "2"})
        d1, d2 = tracked / "d1", tracked / "d2"
        self._install(src1, d1)
        self._install(src2, d2)
        shutil.rmtree(src1)
        result = lf.do_remove(
            None, None, all_installs=True, dry_run=False, verbose=False
        )
        assert lf.load_install_records() == []  # both untracked
        assert not (d1 / "a").is_symlink()  # dangling link cleaned
        assert not (d2 / "b").exists()  # the present install's links removed
        assert result is None

    def test_resync_skips_missing_source(self, tracked: Path) -> None:
        # A re-sync (install with no source/target) skips a tracked install
        # whose source has since gone missing, and still syncs the rest --
        # and surfaces a WARNING exit code for the skip.
        src1 = _make_tree(tracked / "s1", {"a": "1"})
        src2 = _make_tree(tracked / "s2", {"b": "2"})
        d1, d2 = tracked / "d1", tracked / "d2"
        self._install(src1, d1)
        self._install(src2, d2)
        shutil.rmtree(src1)
        result = lf.do_install(
            None,
            None,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )  # no raise despite src1 missing
        assert (d2 / "b").is_symlink()
        assert result == lf.ExitCode.WARNING

    def test_rejects_same_source_for_a_second_target(
        self, tracked: Path
    ) -> None:
        # A source dir can't be tracked by two installs (even into
        # different targets): install sources must be unique.
        src = _make_tree(tracked / "s", {"x": "y"})
        self._install(src, tracked / "d1")
        with pytest.raises(lf.LinkfilesError):
            self._install(src, tracked / "d2")

    def test_audit_flags_missing_source(self, tracked: Path) -> None:
        # A tracked install whose source vanished has dangling links; audit
        # reports the divergence and fails rather than passing silently.
        src = _make_tree(tracked / "s", {"x": "y"})
        self._install(src, tracked / "d")
        shutil.rmtree(src)
        with pytest.raises(lf.ConflictsFound):
            lf.do_audit(verbose=False)

    def test_audit_flags_missing_link(self, tracked: Path) -> None:
        # audit reports a link install would create -- a source entry whose
        # target link a user has removed by hand -- as a divergence.
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        (tgt / "x").unlink()  # the link install would re-create
        with pytest.raises(lf.ConflictsFound):
            lf.do_audit(verbose=False)

    def test_install_prunes_dangling_link(self, tracked: Path) -> None:
        # Re-installing a source after one of its files is removed unlinks
        # the now-dangling managed link, leaving the rest in place.
        src = _make_tree(tracked / "s", {"keep": "1", "gone": "2"})
        tgt = tracked / "d"
        self._install(src, tgt)
        assert (tgt / "gone").is_symlink()
        (src / "gone").unlink()
        self._install(src, tgt)
        assert not (tgt / "gone").is_symlink()
        assert (tgt / "keep").is_symlink()

    def test_resync_missing_source_prunes_links_keeps_record(
        self, tracked: Path
    ) -> None:
        # A vanished source has its dangling links pruned but keeps its
        # install record (returning WARNING), so a restored source is
        # re-linked by a later install.
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        shutil.rmtree(src)
        result = lf.do_install(
            None,
            None,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )
        assert not (tgt / "x").is_symlink()  # dangling link pruned
        assert lf.load_install_records() != []  # record kept
        assert result == lf.ExitCode.WARNING
        _make_tree(src, {"x": "y"})  # source restored
        lf.do_install(
            None,
            None,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )
        assert (tgt / "x").is_symlink()  # re-linked

    def test_install_writes_ledger(self, tracked: Path) -> None:
        # install persists every link it makes to ~/.linkfiles.linked.
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        assert lf.LinkRecord(tgt=tgt / "x", src=src / "x") in (
            lf.WorldState.load_link_records()
        )

    def test_links_seeded_from_sources_when_absent(self, tracked: Path) -> None:
        # With a tracked install but no ledger file (a pre-ledger install),
        # the links are seeded in memory from source discovery, so the
        # already-present link still categorizes as KEEP.
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        lf.LINKED_FILE.unlink()
        state = lf.WorldState.load()
        keep = {link.tgt for link in state.plan.keep}
        assert tgt / "x" in keep

    def test_single_install_preserves_other_installs_links(
        self, tracked: Path
    ) -> None:
        # A single install run categorizes only its own install; another
        # install's recorded links are preserved untouched in .linked.
        s1 = _make_tree(tracked / "s1", {"a": "1"})
        s2 = _make_tree(tracked / "s2", {"b": "2"})
        d1, d2 = tracked / "d1", tracked / "d2"
        self._install(s1, d1)
        self._install(s2, d2)
        self._install(s1, d1)  # re-install only s1
        recorded = {r.tgt for r in lf.WorldState.load_link_records()}
        assert d1 / "a" in recorded
        assert d2 / "b" in recorded  # the other install's link survives

    def test_orphan_link_record_dropped_on_write(self, tracked: Path) -> None:
        # A ledger line owned by no tracked install is dropped on the next
        # write; its symlink, which linkfiles cannot attribute, is left in
        # place.
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        orphan = tgt / "orphan"
        orphan.symlink_to(tracked / "nowhere")
        lines = lf.LINKED_FILE.read_text().splitlines()
        lines.append(
            lf.LinkRecord(tgt=orphan, src=tracked / "ghost" / "f").to_line()
        )
        lf.LINKED_FILE.write_text("\n".join(lines) + "\n")
        self._install(src, tgt)  # a resync rewrites .linked
        recorded = {r.tgt for r in lf.WorldState.load_link_records()}
        assert orphan not in recorded  # orphan record dropped
        assert orphan.is_symlink()  # but its symlink is left on disk

    def test_remove_tolerates_stale_ledger_entry(self, tracked: Path) -> None:
        # A ledger entry whose link a user already deleted by hand warns but
        # does not abort: the rest of the install is still torn down.
        src = _make_tree(tracked / "s", {"a": "1", "b": "2"})
        tgt = tracked / "d"
        self._install(src, tgt)
        (tgt / "a").unlink()  # user removed one link by hand
        lf.do_remove(src, tgt, all_installs=False, dry_run=False, verbose=False)
        assert not (tgt / "b").exists()  # the rest still removed
        assert lf.load_install_records() == []

    def test_explicit_install_missing_source_errors(
        self, tracked: Path
    ) -> None:
        # An explicit source that does not exist is still an error.
        with pytest.raises(lf.LinkfilesError):
            lf.do_install(
                tracked / "nope",
                tracked / "d",
                dotfiles=False,
                no_recurse=False,
                dry_run=False,
                force=False,
                verbose=False,
            )

    def test_install_recreates_link_removed_by_hand(
        self, tracked: Path
    ) -> None:
        # Interruption / manual-delete recovery: a link recorded in the
        # ledger but missing on disk is re-created by a later install
        # (the ledger records intent before the link exists).
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        (tgt / "x").unlink()
        assert not (tgt / "x").is_symlink()
        self._install(src, tgt)
        assert (tgt / "x").is_symlink()

    def test_newly_ignored_file_is_pruned(self, tracked: Path) -> None:
        # A file linked earlier but newly matched by .linkfiles.ignore is
        # no longer wanted, so a re-install prunes its link.
        src = _make_tree(tracked / "s", {"keep": "1", "drop": "2"})
        tgt = tracked / "d"
        self._install(src, tgt)
        assert (tgt / "drop").is_symlink()
        (src / ".linkfiles.ignore").write_text("drop\n")
        self._install(src, tgt)
        assert not (tgt / "drop").is_symlink()
        assert (tgt / "keep").is_symlink()

    def test_conflicting_file_is_left_untouched(self, tracked: Path) -> None:
        # A real file where a link would go is a conflict: install reports
        # it (raising) and never clobbers the file.
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        tgt.mkdir()
        (tgt / "x").write_text("pre-existing")
        with pytest.raises(lf.ConflictsFound):
            self._install(src, tgt)
        assert (tgt / "x").read_text() == "pre-existing"

    def test_force_replaces_conflicting_symlink(self, tracked: Path) -> None:
        # A symlink pointing elsewhere blocks install as a conflict, but
        # --force replaces it (a real file would still be left alone).
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        tgt.mkdir()
        (tgt / "x").symlink_to(tracked / "elsewhere")
        with pytest.raises(lf.ConflictsFound):
            self._install(src, tgt)
        lf.do_install(
            src,
            tgt,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=True,
            verbose=False,
        )
        assert (tgt / "x").resolve() == (src / "x").resolve()

    def test_reinstall_updates_install_flags(self, tracked: Path) -> None:
        # Re-installing the same source/target updates the tracked
        # install's flags in place rather than duplicating the record.
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        self._install(src, tgt, dotfiles=True)
        recs = lf.load_install_records()
        assert len(recs) == 1
        assert recs[0].dotfiles is True


class TestLineRecordSerialization:
    """Both record types round-trip through the shared LineRecord
    to_line / from_line, including paths with the separator, the escape
    char, and newlines."""

    def test_install_record_round_trip(self) -> None:
        rec = lf.InstallRecord(
            tgt=Path("/t\tx"),
            src=Path("/s\\y"),
            dotfiles=True,
            no_recurse=True,
        )
        assert lf.InstallRecord.from_line(rec.to_line()) == rec

    def test_link_record_round_trip(self) -> None:
        rec = lf.LinkRecord(tgt=Path("/t\nx"), src=Path("/s/y"))
        assert lf.LinkRecord.from_line(rec.to_line()) == rec

    def test_from_line_rejects_too_few_fields(self) -> None:
        assert lf.LinkRecord.from_line("only-one-field") is None


@pytest.mark.usefixtures("tracked")
class TestWorldStateLoad:
    """WorldState.load sorts each in-scope install's links into the
    keep / install / remove / conflict buckets from one source scan plus
    one stat per target."""

    def _install(self, src: Path, tgt: Path) -> None:
        lf.do_install(
            src,
            tgt,
            dotfiles=False,
            no_recurse=False,
            dry_run=False,
            force=False,
            verbose=False,
        )

    def _all(self) -> lf.WorldState:
        return lf.WorldState.load()

    def _tgts(self, links: list[lf.LinkRecord]) -> set[Path]:
        return {link.tgt for link in links}

    def test_keep_for_a_correct_link(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        state = self._all()
        assert self._tgts(state.plan.keep) == {tgt / "x"}
        assert not state.plan.install
        assert not state.plan.remove

    def test_install_for_a_new_source_file(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        (src / "new").write_text("z")  # added after install, not yet linked
        state = self._all()
        assert tgt / "new" in self._tgts(state.plan.install)

    def test_remove_for_a_vanished_source_file(self, tracked: Path) -> None:
        src = _make_tree(tracked / "s", {"x": "y", "gone": "z"})
        tgt = tracked / "d"
        self._install(src, tgt)
        (src / "gone").unlink()  # source file removed: its link is now stale
        state = self._all()
        assert tgt / "gone" in self._tgts(state.plan.remove)

    def test_bucket_order_is_sorted_by_target(self, tracked: Path) -> None:
        # Each bucket is ordered by target path, so the per-link report is
        # deterministic regardless of dict / set iteration order.
        src = _make_tree(tracked / "s", {"c": "1", "a": "2", "b": "3"})
        tgt = tracked / "d"
        self._install(src, tgt)
        keep = [link.tgt for link in self._all().plan.keep]
        assert keep == sorted(keep)
        assert len(keep) == 3

    def test_conflict_splits_link_from_nonlink(self, tracked: Path) -> None:
        # A wanted target occupied by a foreign symlink is a LINK_CONFLICT
        # (force-fixable); by a real file, a NONLINK_CONFLICT (never
        # touched). Both land in CONFLICT, carrying their kind.
        src = _make_tree(tracked / "s", {"link": "a", "file": "b"})
        tgt = tracked / "d"
        tgt.mkdir()
        (tgt / "link").symlink_to(tracked / "elsewhere")
        (tgt / "file").write_text("occupied")
        install = lf.InstallRecord(
            tgt=tgt, src=src, dotfiles=False, no_recurse=False
        )
        lf.save_install_records([install])
        state = lf.WorldState.load()
        kinds = {link.tgt: link.status() for link in state.plan.conflict}
        assert kinds[tgt / "link"] == lf.LinkStatus.LINK_CONFLICT
        assert kinds[tgt / "file"] == lf.LinkStatus.NONLINK_CONFLICT

    def test_blocked_conflict_is_not_recorded(self, tracked: Path) -> None:
        # A wanted target blocked by a real file is reported as a conflict
        # but not recorded in the ledger -- linkfiles never claims a path
        # it does not own. The clean links of the same install are still
        # recorded, and the user's file is left untouched.
        src = _make_tree(tracked / "s", {"x": "y", "ok": "z"})
        tgt = tracked / "d"
        tgt.mkdir()
        (tgt / "x").write_text("user file")
        with pytest.raises(lf.ConflictsFound):
            lf.do_install(
                src,
                tgt,
                dotfiles=False,
                no_recurse=False,
                dry_run=False,
                force=False,
                verbose=False,
            )
        recorded = {r.tgt for r in lf.WorldState.load_link_records()}
        assert tgt / "x" not in recorded
        assert tgt / "ok" in recorded
        assert (tgt / "x").read_text() == "user file"

    def test_executing_verbs_update_the_plan_to_the_reload_state(
        self, tracked: Path
    ) -> None:
        # Executing a phase updates the buckets so the plan resembles a
        # reload: a removed link leaves remove, an installed link moves into
        # keep, and install empties.
        src = _make_tree(tracked / "s", {"keep": "1", "gone": "2"})
        tgt = tracked / "d"
        self._install(src, tgt)
        (src / "gone").unlink()  # recorded link now stale -> remove
        (src / "new").write_text("3")  # new source file -> install
        plan = lf.WorldState.load().plan
        assert {link.tgt for link in plan.remove} == {tgt / "gone"}
        assert {link.tgt for link in plan.install} == {tgt / "new"}
        removed = plan.remove_links(dry_run=False)
        installed = plan.install_links(dry_run=False)
        assert {r.record.tgt for r in removed} == {tgt / "gone"}
        assert {r.record.tgt for r in installed} == {tgt / "new"}
        # The removed link left remove; the installed link moved into keep
        # alongside the pre-existing one; install is empty.
        assert plan.remove == []
        assert plan.install == []
        assert {link.tgt for link in plan.keep} == {
            tgt / "keep",
            tgt / "new",
        }

    def test_unremovable_link_stays_in_remove(
        self, tracked: Path, monkeypatch: Any
    ) -> None:
        # A remove link that cannot be unlinked is kept in the bucket (and so
        # rewritten), letting a later run of its still-tracked install retry
        # it rather than leaking the link.
        src = _make_tree(tracked / "s", {"x": "y"})
        tgt = tracked / "d"
        self._install(src, tgt)
        (src / "x").unlink()  # recorded link now stale -> remove
        plan = lf.WorldState.load().plan
        monkeypatch.setattr(lf.LinkRecord, "remove", lambda *_a, **_k: False)
        plan.remove_links(dry_run=False)
        assert {link.tgt for link in plan.remove} == {tgt / "x"}

    def test_audit_does_not_write(self, tracked: Path) -> None:
        # audit reports from the built state alone -- it never writes the
        # tracking files.
        src = _make_tree(tracked / "s", {"x": "y"})
        self._install(src, tracked / "d")
        installed = lf.INSTALLED_FILE.read_bytes()
        linked = lf.LINKED_FILE.read_bytes()
        lf.do_audit(verbose=False)
        assert lf.INSTALLED_FILE.read_bytes() == installed
        assert lf.LINKED_FILE.read_bytes() == linked

    def test_dry_run_install_and_remove_leave_state_untouched(
        self, tracked: Path
    ) -> None:
        # A dry run reports without touching disk links or the tracking
        # files: nothing is created, pruned, or torn down.
        src = _make_tree(tracked / "s", {"keep": "1", "gone": "2"})
        tgt = tracked / "d"
        self._install(src, tgt)
        (src / "gone").unlink()  # a dangling link install would prune
        (src / "new").write_text("3")  # a link install would create
        installed = lf.INSTALLED_FILE.read_bytes()
        linked = lf.LINKED_FILE.read_bytes()
        lf.do_install(
            src,
            tgt,
            dotfiles=False,
            no_recurse=False,
            dry_run=True,
            force=False,
            verbose=False,
        )
        assert not (tgt / "new").exists()  # not created
        assert (tgt / "gone").is_symlink()  # not pruned
        lf.do_remove(src, tgt, all_installs=False, dry_run=True, verbose=False)
        assert (tgt / "keep").is_symlink()  # not torn down
        assert lf.INSTALLED_FILE.read_bytes() == installed
        assert lf.LINKED_FILE.read_bytes() == linked


class TestCmdCallbacks(CmdCallbacksBase):
    """Reuse the generic CLI dispatch tests against the linkfiles
    callbacks / parser."""

    CALLBACKS = lf.COMMAND_CALLBACKS
    PARSER_FUNC = lf.build_parser
    CLI_FUNC = staticmethod(lf.cli)
    EXIT_CODE_USAGE = lf.ExitCode.USAGE
    TEST_SUBCOMMAND = "audit"
    EXCEPTION_EXIT_CODE_MAP = [
        (lf.ConflictsFound("t"), lf.ExitCode.CONFLICTS),
        (lf.UsageError("t"), lf.ExitCode.USAGE),
        (lf.LinkfilesError("t"), lf.ExitCode.ERROR),
        (RuntimeError("t"), lf.ExitCode.CRASHED),
    ]


class TestExceptionHierarchy(ExceptionHierarchyBase):
    BASE_ERROR = lf.LinkfilesError
    EXIT_CODE = lf.ExitCode
    EXCLUDED_CODES = {
        lf.ExitCode.SUCCESS,
        lf.ExitCode.WARNING,
        lf.ExitCode.CONFIG,
        lf.ExitCode.SUBPROCESS,
        lf.ExitCode.CRASHED,
    }


class TestHelpWidth(HelpWidthBase):
    PROG = "linkfiles"
    PARSER_FUNC = staticmethod(lf.build_parser)


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
