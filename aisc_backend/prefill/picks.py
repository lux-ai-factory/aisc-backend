"""The VAIR picks a document names: the system's type and purpose, the kind of provider and deployer, and the four tag sets.

The form offers VAIR's own lists for these (src/data/vair_vocab.json), so a document names them on a
labelled line, each value a VAIR id or label, several separated by commas:

    System type: Narrow AI
    VAIR purpose: Assessing Creditworthiness
    Kind of provider: Public Authority
    Kind of deployer: Educational Institution
    Capabilities: Profiling, Natural Language Generation
    Application domains: Private Service
    Market form: Software, Service
    Locality: Workplace

A value that names no VAIR term of its field's class is left out, never guessed. "Purpose:" is not one
of these labels: documents mean the use case by it, which fields.py reads as text. Lines inside the
Components and Risks sections are the parts' and the risks', not the system's.
"""
from __future__ import annotations

import re

from aisc_backend.prefill.components import section_lines as component_lines
from aisc_backend.prefill.headings import heading
from aisc_backend.prefill.risks import RISKS_HEADING, ends_risk_section
from aisc_backend.prefill.vair import match_term

#: field -> (the VAIR class of its terms, whether it holds several, the labels that name it)
PICKS: dict[str, tuple[str, bool, tuple[str, ...]]] = {
    "systemType": ("AISystem", False, ("system type", "type of ai system", "vair system type")),
    "purpose": ("Purpose", False, ("vair purpose", "purpose term", "intended purpose term")),
    "providerTerm": ("AIOperator", False, ("provider term", "kind of provider")),
    "deployerTerm": ("AIOperator", False, ("deployer term", "kind of deployer")),
    "targetSystemTags": ("AICapability", True, ("capabilities", "capability", "ai capabilities")),
    "sectorTags": ("Domain", True, ("application domains", "application domain", "domains", "domain",
                                    "sectors", "sector")),
    "marketFormTags": ("Modality", True, ("market form", "market forms", "how the system reaches the market")),
    "localityTags": ("LocalityOfUse", True, ("locality", "locality of use", "where the system is used")),
}
_BY_LABEL = {label: field for field, (_, _, labels) in PICKS.items() for label in labels}
_LINE = re.compile(r"^\s*(?:[-*•]\s*)?([A-Za-z][A-Za-z ]{1,40}?)\s*:\s*(.+)$")


def _risk_lines(lines: list[str]) -> set[int]:
    inside, taken = False, set()
    for index, line in enumerate(lines):
        if heading(RISKS_HEADING, line):
            inside = True
        elif inside and ends_risk_section(line):
            inside = False
        if inside:
            taken.add(index)
    return taken


def picks_from_text(text: str) -> dict:
    """The picks the document names, as VAIR ids; a field it names no term for is absent."""
    lines = text.splitlines()
    skip = component_lines(lines) | _risk_lines(lines)
    found: dict = {}
    for index, line in enumerate(lines):
        if index in skip:
            continue
        m = _LINE.match(line)
        field = _BY_LABEL.get(m.group(1).strip().lower()) if m else None
        if field is None or field in found:
            continue
        cls, several, _ = PICKS[field]
        # split on the separators alone and strip each value: "\s*[,;]\s*" would be retried
        # at every position of a long blank run
        values = [v.strip() for v in re.split(r"[,;]", m.group(2))] if several else [m.group(2).strip()]
        terms: list[str] = []
        for value in values:
            term = match_term(cls, value.rstrip("."))
            if term and term not in terms:
                terms.append(term)
        if terms:
            found[field] = terms if several else terms[0]
    return found
