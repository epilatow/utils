#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest",
#     "pytest-cov",
#     "tomlkit",
#     "pydantic>=2",
#     "mdformat",
#     "mdformat-gfm",
#     "mdformat-tables",
# ]
# ///
# This is AI generated code

"""Freshness gate for the generated reference docs.

`scripts/render-docs` renders each utility's roff man page (e.g.
`share/man/man1/crony.1`) and GitHub-browsable GFM doc (e.g.
`docs/crony.md`) from its argparse parser. Both checked-in artifacts are
gated against a fresh render, so a CLI / help-text change that isn't
accompanied by regenerated docs -- or a hand-edit of a generated file --
fails here.

The GFM doc is built in pure Python (plus mdformat), so its comparison
needs no pandoc. The roff comparison shells out to pandoc, a required,
version-pinned dev/CI dependency installed by `scripts/pandoc install`
into `.tools/` (see DEVELOPMENT.md): a missing pinned pandoc is a hard
failure here, not a skip, because without it the man page can't be
regenerated at all.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import render_docs  # noqa: E402  (import after the src/ path insert above)

_script_path = REPO_ROOT / "src" / "render_docs.py"


@pytest.mark.parametrize(
    "make_spec", render_docs._SPECS, ids=lambda m: m().prog
)
def test_man_roff_is_current(
    make_spec: Callable[[], render_docs.ManSpec],
) -> None:
    if render_docs._pinned_pandoc() is None:
        pytest.fail(
            f"pandoc {render_docs.PANDOC_VERSION} not found; run "
            "scripts/pandoc install to fetch the pinned version into "
            ".tools/ (see DEVELOPMENT.md)."
        )
    spec = make_spec()
    rendered = render_docs._render_roff(render_docs.build_markdown(spec))
    assert spec.man_path.read_text() == rendered


@pytest.mark.parametrize(
    "make_spec", render_docs._SPECS, ids=lambda m: m().prog
)
def test_docs_gfm_is_current(
    make_spec: Callable[[], render_docs.ManSpec],
) -> None:
    # The GFM doc is pure Python plus mdformat, so no pandoc is needed.
    spec = make_spec()
    assert spec.docs_path.read_text() == render_docs.build_gfm(spec)


def test_gfm_has_single_title_and_demoted_sections() -> None:
    # The GFM doc carries exactly one top-level heading -- the `# <prog> -
    # <summary>` title (the man NAME line, with no separate NAME section)
    # -- with every man section demoted under it, so it reads as one
    # GitHub document rather than a run of sibling H1s.
    spec = render_docs.ManSpec(
        prog="demo",
        section=1,
        build_parser=argparse.ArgumentParser,
        name_description="demo tool",
        description="Demo overview.",
    )
    gfm = render_docs.build_gfm(spec)
    assert gfm.startswith("# demo - demo tool\n")
    h1s = [ln for ln in gfm.splitlines() if ln.startswith("# ")]
    assert h1s == ["# demo - demo tool"]
    assert "## NAME" not in gfm
    assert "## DESCRIPTION" in gfm


def test_extra_sections_render_between_description_and_common_args() -> None:
    # `extra_sections` are top-level prose sections placed after
    # DESCRIPTION and before COMMON ARGUMENTS, in order.
    spec = render_docs.ManSpec(
        prog="demo",
        section=1,
        build_parser=argparse.ArgumentParser,
        name_description="demo tool",
        description="Demo overview.",
        extra_sections=[
            ("GETTING STARTED", "Start here."),
            ("NOTES", "A note."),
        ],
        common_args=[("`-x`", "an option")],
    )
    md = render_docs.build_markdown(spec)
    assert "Start here." in md and "A note." in md
    assert (
        md.index("# DESCRIPTION")
        < md.index("# GETTING STARTED")
        < md.index("# NOTES")
        < md.index("# COMMON ARGUMENTS")
    )


class TestInvocation:
    """`_invocation` formats an action the way `--help` shows it, built
    from public attributes (not argparse's formatter)."""

    def test_switch_joins_option_strings(self) -> None:
        a = argparse.ArgumentParser().add_argument(
            "-v", "--verbose", action="store_true"
        )
        assert render_docs._invocation(a) == "-v, --verbose"

    def test_value_option_uses_uppercased_dest(self) -> None:
        a = argparse.ArgumentParser().add_argument("-b", "--bundle")
        assert render_docs._invocation(a) == "-b, --bundle BUNDLE"

    def test_explicit_metavar_wins(self) -> None:
        a = argparse.ArgumentParser().add_argument("--file", metavar="PATH")
        assert render_docs._invocation(a) == "--file PATH"

    def test_positional_uses_metavar(self) -> None:
        a = argparse.ArgumentParser().add_argument(
            "jobs", nargs="*", metavar="job"
        )
        assert render_docs._invocation(a) == "job"

    def test_nargs_shapes(self) -> None:
        p = argparse.ArgumentParser()
        star = p.add_argument("--star", nargs="*")
        plus = p.add_argument("--plus", nargs="+")
        opt = p.add_argument("--opt", nargs="?")
        assert render_docs._invocation(star) == "--star [STAR ...]"
        assert render_docs._invocation(plus) == "--plus PLUS [PLUS ...]"
        assert render_docs._invocation(opt) == "--opt [OPT]"


class TestWalkSubcommands:
    """`_walk_subcommands` collects only leaf, user-facing subcommands."""

    @staticmethod
    def _parser() -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="demo")
        sub = p.add_subparsers()
        sub.add_parser("shown", help="A shown command.")
        sub.add_parser("hidden")  # no help -> internal entry, omitted
        sub.add_parser("withdesc", help="short", description="Full desc.")
        parent = sub.add_parser("parent", help="a parent")
        parent.add_subparsers().add_parser("leaf", help="a leaf")
        return p

    def _summaries(self) -> dict[str, str]:
        acc: list[tuple[argparse.ArgumentParser, str]] = []
        render_docs._walk_subcommands(self._parser(), acc)
        return {sub.prog.split(" ", 1)[1]: summary for sub, summary in acc}

    def test_omits_internal_and_parent_lists_leaf(self) -> None:
        names = self._summaries()
        assert "shown" in names
        # A subcommand registered without help is internal -> omitted.
        assert "hidden" not in names
        # A parent with subcommands isn't itself a leaf; its child is.
        assert "parent" not in names
        assert "parent leaf" in names

    def test_summary_prefers_description_over_help(self) -> None:
        names = self._summaries()
        assert names["withdesc"] == "Full desc."
        assert names["shown"] == "A shown command."


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
