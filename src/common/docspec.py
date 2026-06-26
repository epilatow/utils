# This is AI generated code

"""The data model for a utility's reference documentation.

A utility opts into generated docs by exposing a module-level `MAN_SPEC`
of this type; `scripts/render-docs` discovers those specs and renders
each into a roff man page and a GFM doc. The spec carries only the
per-utility bits the argparse parser doesn't: the NAME one-liner, the
DESCRIPTION overview, the EXIT STATUS table, and a list of free-form
sections to place before and after the auto-derived COMMON ARGUMENTS /
OPTIONS / SUBCOMMANDS listing. Each free-form section picks its
rendered shape (prose, a definition list, or titled sub-lists).
Everything derivable from the parser (the synopsis, a single-command
tool's own options, the subcommand list, the arguments shared across
subcommands) is left to the renderer so it can't drift from the CLI.
Keeping the spec here -- importable, separate from the renderer --
lets each utility own its documentation content while render-docs stays
pure formatting.
"""

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TextSection:
    """A prose section, reflowed like DESCRIPTION (e.g. GETTING STARTED)."""

    title: str
    body: str


@dataclass(frozen=True)
class ItemsSection:
    """A flat definition list of `(term, description)` items, the term
    shown as code (e.g. FILES)."""

    title: str
    items: list[tuple[str, str]]


@dataclass(frozen=True)
class ItemListsSection:
    """A section with an optional intro and titled sub-lists, each a
    definition list of `(term, description)` items (e.g. crony's status
    columns)."""

    title: str
    intro: str
    subsections: list[tuple[str, str, list[tuple[str, str]]]]


# A free-form section placed in `ManSpec.pre_sections` / `post_sections`.
# render-docs dispatches on the concrete type to pick the rendered shape.
# Every section in those lists renders, heading included; omit a section
# by leaving it out of the list rather than handing in an empty one.
Section = TextSection | ItemsSection | ItemListsSection


@dataclass(frozen=True)
class ManSpec:
    """One utility's reference doc. `build_parser` returns its argparse
    parser; the rest supplies what the parser doesn't carry."""

    prog: str
    section: int
    build_parser: Callable[[], argparse.ArgumentParser]
    # The man NAME line and GFM title ("prog - <name_description>"); a terse,
    # single-line whatis phrase, no trailing period (gated by test_render_docs).
    name_description: str
    # DESCRIPTION body; falls back to the parser's own `description`.
    description: str = ""
    # Free-form sections placed after DESCRIPTION and before the derived
    # COMMON ARGUMENTS / OPTIONS / SUBCOMMANDS listing (e.g. GETTING
    # STARTED).
    pre_sections: list[Section] = field(default_factory=list)
    # Free-form sections placed after the derived OPTIONS / SUBCOMMANDS
    # listing and before EXIT STATUS (e.g. crony's STATUS COLUMNS, a FILES
    # list).
    post_sections: list[Section] = field(default_factory=list)
    # EXIT STATUS as `(code, description)` items, or [] to omit it.
    exit_status: list[tuple[str, str]] = field(default_factory=list)

    @property
    def man_path(self) -> Path:
        return (
            _REPO_ROOT
            / "share"
            / "man"
            / f"man{self.section}"
            / f"{self.prog}.{self.section}"
        )

    @property
    def docs_path(self) -> Path:
        return _REPO_ROOT / "docs" / f"{self.prog}.md"
