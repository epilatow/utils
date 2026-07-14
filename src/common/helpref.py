# This is AI generated code

"""Shared formatting for `--help` value-reference sections.

A utility that documents a closed set of values in its `--help` (status
column names, automation job names, ...) models each block as a
`ReferenceSection` and renders it with `reference_section_text`: a
`Title:` header with the body indented two spaces beneath it, the body
being an optional wrapped lead paragraph followed by a
`<value>  <description>` definition list. The section is plain data, so
the same source can also feed a `common.docspec` section and the
generated docs can't drift from the terminal help. Only formatting
lives here; each utility owns its section content and picks the wrap
width that fits its help layout (with the two-space indent, a width of
76 stays within a 78-column help gate).
"""

import textwrap
from typing import NamedTuple


class ReferenceSection(NamedTuple):
    """One `--help` reference section: a heading, optional lead
    paragraph, and `(label, description)` items."""

    title: str
    items: list[tuple[str, str]]
    lead: str = ""


def definition_list(
    items: list[tuple[str, str]], label_width: int, *, width: int
) -> str:
    """Render `(label, description)` pairs as a `--help` definition
    list: each label in a left column, its description wrapped at
    `width` and hanging-indented beside it. Every description must be
    non-empty (an empty one would silently drop its label from the
    rendered text)."""
    out: list[str] = []
    for label, description in items:
        if not description:
            raise ValueError(f"empty description for label: {label!r}")
        out.append(
            textwrap.fill(
                description,
                width=width,
                initial_indent=f"{label:<{label_width}}",
                subsequent_indent=" " * label_width,
                break_on_hyphens=False,
                break_long_words=False,
            )
        )
    return "\n".join(out)


def value_reference(items: list[tuple[str, str]], *, width: int) -> str:
    """A `--help` value reference -- a `<value>  <description>`
    definition list with the label column sized to the widest value.
    `items` must be non-empty (an empty reference has no width to size
    the label column from, and would render as an empty section)."""
    if not items:
        raise ValueError("items must be non-empty")
    label_width = max(len(label) for label, _ in items) + 2
    return definition_list(items, label_width, width=width)


def reference_section_text(section: ReferenceSection, *, width: int) -> str:
    """A reference section as `--help` text: a `Title:` header with the
    body indented two spaces beneath it. The body is an optional lead
    wrapped at `width`, then the value reference."""
    body: list[str] = []
    if section.lead:
        body.append(
            textwrap.fill(
                section.lead,
                width=width,
                break_on_hyphens=False,
                break_long_words=False,
            )
        )
        body.append("")
    body.append(value_reference(section.items, width=width))
    return f"{section.title}:\n" + textwrap.indent("\n".join(body), "  ")
