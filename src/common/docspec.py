# This is AI generated code

"""The data model for a utility's reference documentation.

A utility opts into generated docs by exposing a module-level `MAN_SPEC`
of this type; `scripts/render-docs` discovers those specs and renders
each into a roff man page and a GFM doc. The spec carries only the
per-utility bits the argparse parser doesn't -- the NAME one-liner, the
DESCRIPTION overview, extra prose sections, an optional reference
section, a FILES list, and an EXIT STATUS table. Everything derivable
from the parser (the synopsis, the subcommand list, the arguments shared
across subcommands) is left to the renderer so it can't drift from the
CLI. Keeping the spec here -- importable, separate from the renderer --
lets each utility own its documentation content while render-docs stays
pure formatting.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ManSpec:
    """One utility's reference doc. `build_parser` returns its argparse
    parser; the rest supplies what the parser doesn't carry."""

    prog: str
    section: int
    build_parser: Callable[[], argparse.ArgumentParser]
    name_description: str
    # DESCRIPTION body; falls back to the parser's own `description`.
    description: str = ""
    # Extra top-level prose sections rendered after DESCRIPTION and
    # before COMMON ARGUMENTS, as `(SECTION TITLE, body)`. The body is
    # prose (reflowed like DESCRIPTION).
    prose_sections: list[tuple[str, str]] = field(default_factory=list)
    # An optional trailing reference section (e.g. crony's status
    # columns): a heading, an intro paragraph, and structured subsections.
    reference_title: str = ""
    reference_intro: str = ""
    reference_sections: list[tuple[str, str, list[tuple[str, str]]]] = field(
        default_factory=list
    )
    # Files the utility reads / writes, as `(path, description)`, rendered
    # in a FILES section just before EXIT STATUS. [] to omit.
    files: list[tuple[str, str]] = field(default_factory=list)
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
