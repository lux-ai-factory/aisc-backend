"""Headings that open a section of rows rather than an answer.

On their own so the readers that stop at them (fields, risks, components) can
all import them without importing each other.
"""
from __future__ import annotations

import re

#: The heading that opens the Components block's rows. Written so that no two
#: whitespace runs are adjacent: "\s*#*\s*" or "\s*:?\s*$" would backtrack in the
#: square of a long blank run's length.
COMPONENTS_HEADING = re.compile(
    r"^\s*(?:#+\s*)?(?:components|system components|components of the system)\s*(?::\s*)?$",
    re.IGNORECASE,
)

#: A heading naming an Annex point: "Annex IV(1)(a)", "IV(1)(a)", "1(a)", "1.a",
#: optionally numbered or bulleted. It has to be the whole line: a citation in
#: the middle of a sentence is prose about the Annex, not a section of it.
ANNEX_HEADING = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:annex\s+)?(?:iv\s*)?\(?([123])\)?\s*(?:[.\)]\s*)?\(?([a-h])\)?\s*(?:[:.\)]\s*)?$",
    re.IGNORECASE,
)

#: A line that opens with "Annex IV": the start of another Annex section.
ANNEX_LINE = re.compile(r"^\s*(?:#+\s*)?annex\s+iv\b", re.IGNORECASE)

#: A heading is a short line. A longer one is never matched as a heading, so no
#: heading pattern ever runs on a paragraph or on a crafted megabyte of blanks.
HEADING_MAX = 500


def heading(pattern: re.Pattern, line: str) -> re.Match | None:
    """pattern.match on the stripped line, or None for a line too long to be a heading."""
    stripped = line.strip()
    return pattern.match(stripped) if len(stripped) <= HEADING_MAX else None
