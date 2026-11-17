"""A document's VAIR terms: the form speaks VAIR, so the upload reads VAIR too.

A term is matched by its VAIR id or its VAIR label, whatever the case and spacing; a word that is
neither is left for the person to choose, never guessed. The lists are the form's own, read from
src/data/vair_vocab.json, so the upload cannot propose a term the form would refuse.
"""
from aisc_backend.prefill.risks import risks_from_text
from aisc_backend.prefill.vair import match_term, terms_of


def test_the_lists_are_the_form_s():
    assert "DecisionTree" in terms_of("AIComponent")
    assert "DataPoisoning" in terms_of("RiskSource")
    assert "finance-and-insurance" not in terms_of("Domain")


def test_a_term_is_matched_by_id_or_label_whatever_the_case():
    assert match_term("RiskSource", "DataPoisoning") == "DataPoisoning"
    assert match_term("RiskSource", "data poisoning") == "DataPoisoning"
    assert match_term("AreaOfImpact", "Right To Non-discrimination") == "RightToNondiscrimination"
    assert match_term("AIComponent", "  Decision   Tree ") == "DecisionTree"


def test_anything_else_is_left_open():
    assert match_term("RiskSource", "an attacker on the bureau feed") == ""
    assert match_term("RiskSource", "Harm") == ""  # a VAIR term, of another class
    assert match_term("RiskSource", "") == ""


RISKS = """Risks

Risk 1
Risk: An applicant is wrongly refused
Source: Thin bureau data
Source term: Erroneous Input Data
Consequence: A creditworthy applicant is refused
Consequence term: ImpairedDecisionMaking
Kind of harm: Unfavourable Treatment
Affected: user
Impact areas: Right, Right To Non-discrimination
"""


def test_each_risk_field_s_term_is_read_beside_its_text():
    [row] = risks_from_text(RISKS)
    assert row["source"] == "Thin bureau data"
    assert row["sourceTerm"] == "ErroneousInputData"
    assert row["consequence"] == "A creditworthy applicant is refused"
    assert row["consequenceTerm"] == "ImpairedDecisionMaking"
    assert row["impactTerm"] == "UnfavourableTreatment"


def test_the_areas_are_vair_s_in_the_document_s_order():
    [row] = risks_from_text(RISKS)
    assert row["areas"] == ["Right", "RightToNondiscrimination"]


def test_the_old_area_words_still_name_vair_s_areas():
    [row] = risks_from_text("Risks\n\nRisk 1\nRisk: r\nImpact areas: fundamental rights, safety, freedom\n")
    assert row["areas"] == ["Right", "Safety", "Freedom"]


def test_a_term_that_is_not_vair_s_is_left_open_and_the_text_kept():
    [row] = risks_from_text("Risks\n\nRisk 1\nRisk: r\nSource: s\nSource term: an attacker\n")
    assert row["source"] == "s"
    assert row["sourceTerm"] == ""


def test_a_row_without_terms_has_them_empty():
    [row] = risks_from_text("Risks\n\nRisk 1\nRisk: r\nSource: s\n")
    assert all(row[f] == "" for f in ("sourceTerm", "consequenceTerm", "impactTerm"))
