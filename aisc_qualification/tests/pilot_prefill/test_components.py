"""The Components block: one row per part of the system.

A document lists them under a Components heading, one block per part, each
field on a labelled line, the way it lists its risks. The type is matched to
the form's Type list: VAIR's AIComponent terms first, ours only
where VAIR has none; a word that matches none is left for the person to choose.
"""
from aisc_qualification.prefill.components import components_from_text, merge_components
from aisc_qualification.prefill.fields import annex_sections, metadata_from_text
from aisc_qualification.prefill.risks import risks_from_text

DOC = """\
System name: MCAS
Provider: Creditum AI SARL

Annex IV(2)(h)
Cybersecurity measures.

Components

Component 1
Name: Scoring model
Kind: Predictive model
Provider: In-house
Role: Estimates the probability of default
and maps it to a score.

Component 2
Name: Hosted LLM
Kind: llm
Provider: Third party
Provider name: A hosted model vendor
Role: Writes the plain-language explanation.

Component 3
Name: Something odd
Kind: quantum oracle

Risks

Risk 1
Risk: An applicant is wrongly refused.
Affected: user
"""


class TestReadingComponents:
    def test_one_row_per_block_in_order(self):
        assert [r["name"] for r in components_from_text(DOC)] == ["Scoring model", "Hosted LLM", "Something odd"]

    def test_a_row_has_the_form_s_fields(self):
        first = components_from_text(DOC)[0]
        assert first == {
            "key": "",
            "name": "Scoring model",
            "role": "Estimates the probability of default and maps it to a score.",
            "type": "Model",  # the older word "Predictive model" names VAIR's term
            "provider": "in_house",
            "providerName": "",
        }

    def test_a_type_is_matched_by_its_label_or_its_id(self):
        assert [r["type"] for r in components_from_text(DOC)][:2] == ["Model", "llm"]

    def test_an_unknown_type_is_left_for_the_person(self):
        assert components_from_text(DOC)[2]["type"] == ""

    def test_a_third_party_names_its_provider(self):
        second = components_from_text(DOC)[1]
        assert second["provider"] == "third_party"
        assert second["providerName"] == "A hosted model vendor"

    def test_every_one_of_our_own_types_is_readable(self):
        kinds = ["Rule engine", "Training data", "Validation data", "Other data", "Data pipeline",
                 "Interface or service", "Other", "LLM or foundation model"]
        text = "Components\n\n" + "\n\n".join(
            f"Component {i}\nName: part {i}\nKind: {k}" for i, k in enumerate(kinds, 1))
        assert [r["type"] for r in components_from_text(text)] == [
            "rule_engine", "training_data", "validation_data", "other_data", "pipeline",
            "interface", "other", "llm"]

    def test_every_vair_type_is_readable_by_its_label(self):
        from aisc_qualification.prefill.vair import _classes

        terms = _classes()["AIComponent"]
        text = "Components\n\n" + "\n\n".join(
            f"Component {i}\nName: part {i}\nType: {t['label']}" for i, t in enumerate(terms, 1))
        assert [r["type"] for r in components_from_text(text)] == [t["id"] for t in terms]

    def test_a_markdown_heading_works_too(self):
        text = "## Components\n\n### Component 1\nName: Rule engine\nKind: rule engine\n"
        assert [r["name"] for r in components_from_text(text)] == ["Rule engine"]

    def test_no_section_means_no_rows(self):
        assert components_from_text("System name: MCAS\n\nRisks\nRisk: x\n") == []

    def test_name_lines_elsewhere_are_not_components(self):
        assert components_from_text("Name: MCAS\nKind: whatever\n") == []


class TestTheSectionStaysItsOwn:
    def test_it_ends_the_annex_answer_before_it(self):
        assert annex_sections(DOC)["q:annex-2:2h"] == "Cybersecurity measures."

    def test_it_is_not_read_as_risks(self):
        rows = risks_from_text("Risks\n\nRisk 1\nRisk: x\n\nComponents\n\nComponent 1\nName: Scoring model\nRole: y\n")
        assert [r["risk"] for r in rows] == ["x"]
        assert all("Scoring model" not in str(r) for r in rows)

    def test_the_risks_after_it_are_still_read(self):
        assert [r["risk"] for r in risks_from_text(DOC)] == ["An applicant is wrongly refused."]

    def test_its_name_and_provider_lines_are_not_the_system_s(self):
        text = "Components\n\nComponent 1\nName: Scoring model\nProvider: Third party\n"
        assert metadata_from_text(text) == {}

    def test_the_system_s_own_labels_still_count(self):
        found = metadata_from_text(DOC)
        assert found["systemName"] == "MCAS"
        assert found["company"] == "Creditum AI SARL"


class TestMerging:
    ROWS = [{"key": "", "name": "Scoring model", "role": "", "type": "DecisionTree", "provider": "in_house", "providerName": ""}]

    def test_an_empty_block_takes_the_document_s_rows(self):
        assert merge_components([], self.ROWS, "empty") == (self.ROWS, False)

    def test_rows_somebody_wrote_are_kept_unless_they_asked_to_replace(self):
        mine = [{"name": "typed"}]
        assert merge_components(mine, self.ROWS, "empty") == (None, True)
        assert merge_components(mine, self.ROWS, "replace") == (self.ROWS, False)

    def test_a_document_without_components_leaves_the_rows_alone(self):
        assert merge_components([{"name": "typed"}], [], "replace") == (None, True)


def test_a_bare_annex_heading_ends_the_components_section():
    """A .docx or .txt writes Annex points as "2(b)"; that heading ends the Components
    section the way it ends the Risks one, so the metadata and picks after it are read."""
    from aisc_qualification.prefill.picks import picks_from_text

    text = "\n".join([
        "Components", "Component 1", "Name: Credit scorer", "Type: model", "",
        "2(b)", "Provider: Acme Bank", "System type: Narrow AI",
    ])
    assert metadata_from_text(text) == {"company": "Acme Bank"}
    assert picks_from_text(text) == {"systemType": "NarrowAI"}
    [row] = components_from_text(text)
    assert row["name"] == "Credit scorer"
