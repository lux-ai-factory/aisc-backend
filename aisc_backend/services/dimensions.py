"""
The catalogue's trustworthiness dimensions, and the objectives that carry them.

A **dimension** is the catalogue's own idea: each of its items is tagged with a
`section: dimension` slug, and a sub-dimension (`controls_subdim`,
`testing_subdim`) names the dimension it belongs to in `parent_dimension_slug`.
The **objectives** are ours: each of the eleven AI Act macro-requirements
REQ1..REQ11 is one dimension.

This module is the single place those two vocabularies are tied together. The
slugs are the catalogue's, taken from its own taxonomy (its site's sidebar
carries the same eleven) — `diversity-non-discrimination-fairness`,
`societal-environmental-wellbeing` and `record-keeping` are the catalogue's own
even where our objective titles differ ("Fairness", "…Well-being",
"Record-keeping and Documentation Retention"). The labels are the catalogue's
too, so the filter a person sees names the same thing the catalogue tags. The
pilot's control-objectives service keeps the same table as its `DIMENSIONS`
registry.

Nothing else may invent a dimension: `services.evidence_map` reads
`dimension_of` from here, the catalogue surface reads the tags the catalogue
serves, and the objective filters read `vocabulary()`. Asserted by
`tests/test_dimensions.py`, which pins every slug against the taxonomy above and
keeps the table in step with the seeded objectives.
"""
from __future__ import annotations

from collections.abc import Iterable

#: Macro requirement -> the catalogue's dimension slug, in REQ order.
BY_MACRO: dict[str, str] = {
    "REQ1": "human-agency-oversight",
    "REQ2": "technical-robustness-safety",
    "REQ3": "privacy-data-governance",
    "REQ4": "transparency",
    "REQ5": "diversity-non-discrimination-fairness",
    "REQ6": "societal-environmental-wellbeing",
    "REQ7": "accountability",
    "REQ8": "quality-management",
    "REQ9": "risk-management",
    "REQ10": "technical-documentation",
    "REQ11": "record-keeping",
}

#: The catalogue's own labels for those slugs (its taxonomy, not our titles).
LABELS: dict[str, str] = {
    "human-agency-oversight": "Human Agency and Oversight",
    "technical-robustness-safety": "Technical Robustness and Safety",
    "privacy-data-governance": "Privacy and Data Governance",
    "transparency": "Transparency",
    "diversity-non-discrimination-fairness": "Diversity, Non-discrimination and Fairness",
    "societal-environmental-wellbeing": "Societal and Environmental Wellbeing",
    "accountability": "Accountability",
    "quality-management": "Quality Management",
    "risk-management": "Risk Management",
    "technical-documentation": "Technical Documentation",
    "record-keeping": "Record-keeping",
}


def macro_id(macro_requirement: str) -> str:
    """`REQ2` from `REQ2 Technical Robustness and Safety`."""
    return (macro_requirement or "").split(None, 1)[0] if macro_requirement else ""


def dimension_of(macro_requirement: str) -> str:
    """The catalogue's dimension slug for an objective, or "" if it has none.

    An objective outside the eleven (a project's own row) has no dimension, and
    the callers treat that as "carries no dimension" rather than guessing one.
    """
    return BY_MACRO.get(macro_id(macro_requirement), "")


def dimensions_of(macro_requirements: Iterable[str]) -> list[str]:
    """The distinct dimension slugs of these objectives, in REQ order."""
    found = {dimension_of(m) for m in macro_requirements}
    return [slug for slug in BY_MACRO.values() if slug in found]


def vocabulary() -> list[dict]:
    """The eleven dimensions, in REQ order: `{requirement, slug, label}`."""
    return [
        {"requirement": macro, "slug": slug, "label": LABELS[slug]}
        for macro, slug in BY_MACRO.items()
    ]
