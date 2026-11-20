"""The prefill never writes the literal "no term" into a term field: an unmatched
term (who is affected, the impact areas) is left empty for the person to choose.
"""
from aisc_qualification.prefill.fields import proposals_from_text
from aisc_qualification.prefill.risks import risks_from_text

UNMATCHED = """\
Risks

Risk: A wrong score.
Source: Stale data.
Consequence: A refused loan.
Affected: no term
Impact areas: no term, something nobody lists
Control: Review.

Risk: A second one.
Affected: the wider public, somehow
Impact areas: vibes
Control: Audit.
"""


def _values(row):
    for value in row.values():
        if isinstance(value, list):
            yield from value
        else:
            yield value


def test_an_unmatched_affected_is_left_empty():
    rows = risks_from_text(UNMATCHED)
    assert [r["affected"] for r in rows] == ["", ""]


def test_unmatched_impact_areas_are_left_out():
    rows = risks_from_text(UNMATCHED)
    assert [r["areas"] for r in rows] == [[], []]


def test_the_literal_no_term_never_appears_in_a_risk_row():
    for row in risks_from_text(UNMATCHED):
        assert all("no term" not in str(v).lower() for v in _values(row)), row


def test_the_prefill_proposes_no_tags():
    text = (
        "System name: MCAS\nMarket form: software\nSector: finance\n"
        "Target system tags: tabular\nLocality: workplace\n"
    )
    proposed = proposals_from_text(text)
    assert not any(k.endswith("Tags") for k in proposed), proposed
