#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for common.helpref (shared --help reference formatting)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.helpref import (  # noqa: E402
    ReferenceSection,
    definition_list,
    reference_section_text,
    value_reference,
)

_ITEMS = [
    ("alpha", "Short description."),
    (
        "beta-longer",
        "A description long enough that it must wrap onto a "
        "second line when rendered at a narrow width.",
    ),
]


class TestDefinitionList:
    def test_labels_pad_to_column_and_descriptions_hang(self) -> None:
        text = definition_list(_ITEMS, 14, width=60)
        lines = text.splitlines()
        assert lines[0].startswith("alpha         Short")
        assert lines[1].startswith("beta-longer   A description")
        # Continuation lines hang-indent under the description column.
        assert lines[2].startswith(" " * 14)
        assert lines[2].strip()

    def test_wraps_at_width(self) -> None:
        text = definition_list(_ITEMS, 14, width=60)
        assert all(len(line) <= 60 for line in text.splitlines())

    def test_rejects_empty_description(self) -> None:
        # An empty description would silently drop its label from the
        # rendered text, so it is rejected instead.
        with pytest.raises(ValueError, match="empty description.*alpha"):
            definition_list([("alpha", "")], 14, width=60)


class TestValueReference:
    def test_label_column_sized_to_widest_value(self) -> None:
        text = value_reference(_ITEMS, width=76)
        # Widest label is "beta-longer" (11 chars) + 2 spaces of gap.
        assert text.splitlines()[0].startswith("alpha" + " " * 8 + "Short")

    def test_rejects_empty_items(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            value_reference([], width=76)


class TestReferenceSectionText:
    def test_title_header_and_two_space_indented_body(self) -> None:
        section = ReferenceSection("Sample Values", _ITEMS)
        text = reference_section_text(section, width=76)
        lines = text.splitlines()
        assert lines[0] == "Sample Values:"
        assert all(line.startswith("  ") for line in lines[1:] if line)

    def test_lead_paragraph_wraps_before_the_items(self) -> None:
        lead = (
            "A lead sentence that is long enough to need wrapping when "
            "the section renders at a narrow width like this test uses."
        )
        section = ReferenceSection("Sample Values", _ITEMS, lead=lead)
        text = reference_section_text(section, width=40)
        lines = text.splitlines()
        assert lines[1].startswith("  A lead sentence")
        # Blank separator between the lead and the definition list.
        blank = lines.index("")
        assert lines[blank + 1].startswith("  alpha")

    def test_body_stays_within_width_plus_indent(self) -> None:
        section = ReferenceSection("Sample Values", _ITEMS, lead="Lead.")
        text = reference_section_text(section, width=76)
        assert all(len(line) <= 78 for line in text.splitlines())
