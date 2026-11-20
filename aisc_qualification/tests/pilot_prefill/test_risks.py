"""The risk rows: question 15, one full risk chain per row.

A document lists its risks under a Risks heading, one block per risk, each
field on a labelled line. The words for who and what is affected are matched
to the form's vocabulary; anything else is left for the person to choose.
"""
import pytest

from aisc_qualification.prefill.fields import annex_sections
from aisc_qualification.prefill.risks import merge_risks, risks_from_text

TWO_RISKS = """\
Annex IV(2)(h)
Cybersecurity measures.

## Risks

### Risk 1
Risk: An applicant is wrongly ranked as high risk.
Source: Bureau coverage is stale.
Vulnerability: Features assume complete history.
Consequence: A creditworthy applicant is refused.
Affected: User
Impact areas: Fundamental rights
Control: A loan officer reviews every Reject.
Follow-up control: The officer may override.

### Risk 2
Risk: A model update degrades accuracy unnoticed.
Source: Drift after recalibration.
Consequence: More wrong decisions.
Affected: Operator
Impact areas: Fundamental rights, Safety
Control: A holdout gate before release.
"""


class TestReadingRisks:
    def test_one_row_per_block(self):
        rows = risks_from_text(TWO_RISKS)
        assert [r["risk"] for r in rows] == [
            "An applicant is wrongly ranked as high risk.",
            "A model update degrades accuracy unnoticed.",
        ]

    def test_every_field_of_a_row(self):
        first = risks_from_text(TWO_RISKS)[0]
        assert first == {
            "risk": "An applicant is wrongly ranked as high risk.",
            "source": "Bureau coverage is stale.",
            "vulnerability": "Features assume complete history.",
            "consequence": "A creditworthy applicant is refused.",
            "affected": "user",
            "areas": ["Right"],
            # the VAIR selects: the document names no term, so each is left open
            "sourceTerm": "",
            "consequenceTerm": "",
            "impactTerm": "",
        }

    def test_a_measure_line_is_skipped_not_glued_to_the_field_above(self):
        """The card asks no measure for a risk. A document that still writes one under each risk
        does not lose the row, and the measure's words land in no other field."""
        rows = risks_from_text(TWO_RISKS)
        for row in rows:
            for k in ("control", "controlTerm", "followUpControl", "followUpControlTerm"):
                assert k not in row
        text = " ".join(str(v) for row in rows for v in row.values())
        assert "loan officer" not in text and "may override" not in text and "holdout" not in text
        assert rows[1]["areas"] == ["Right", "Safety"]

    def test_areas_are_matched_to_the_vocabulary(self):
        # VAIR's areas; "Fundamental rights" is the older word for vair:Right
        assert risks_from_text(TWO_RISKS)[1]["areas"] == ["Right", "Safety"]

    def test_who_is_affected_in_other_words(self):
        text = "Risks\nRisk: x\nAffected: the applicants using it\n"
        assert risks_from_text(text)[0]["affected"] == "user"
        text = "Risks\nRisk: x\nWho is affected: the bank operating the system\n"
        assert risks_from_text(text)[0]["affected"] == "operator"

    def test_an_unknown_affected_is_left_to_choose(self):
        """A guess here would be an answer nobody gave."""
        text = "Risks\nRisk: x\nAffected: society at large\n"
        assert risks_from_text(text)[0]["affected"] == ""

    @pytest.mark.parametrize("said, expected", [
        ("job applicants", "JobApplicant"), ("Job Applicant", "JobApplicant"),
        ("Asylum seekers.", "AsylumSeeker"), ("user", "user"), ("the operator", "operator"),
        ("shareholders", "")])
    def test_who_is_affected_can_be_one_of_vair_s_groups(self, said, expected):
        text = f"Risks\nRisk: x\nAffected: {said}\n"
        assert risks_from_text(text)[0]["affected"] == expected

    def test_the_form_s_own_question_labels_work_too(self):
        text = (
            "Risks\n"
            "What could go wrong: A shelf is flagged empty when it is full.\n"
            "What causes it: Poor lighting.\n"
            "What happens as a result: Staff check a full shelf.\n"
        )
        row = risks_from_text(text)[0]
        assert row["risk"] == "A shelf is flagged empty when it is full."
        assert row["source"] == "Poor lighting."
        assert row["consequence"] == "Staff check a full shelf."

    def test_a_new_risk_line_starts_a_new_row_without_a_heading(self):
        text = "Risks\nRisk: one\nSource: s1\n\nRisk: two\nSource: s2\n"
        assert [(r["risk"], r["source"]) for r in risks_from_text(text)] == [("one", "s1"), ("two", "s2")]

    def test_a_value_may_run_onto_the_lines_under_it(self):
        text = "Risks\nRisk: An applicant is\nwrongly ranked.\nSource: stale data\n"
        assert risks_from_text(text)[0]["risk"] == "An applicant is wrongly ranked."

    def test_no_risks_heading_means_no_risks(self):
        """Labels like Risk: or Source: elsewhere in a document are prose."""
        assert risks_from_text("Annex IV(1)(a)\nRisk: this is prose\nSource: also prose\n") == []

    def test_a_block_that_says_nothing_is_not_a_row(self):
        assert risks_from_text("Risks\n\n### Risk 1\n\n### Risk 2\nRisk: real\n") == [
            {
                "risk": "real",
                "source": "",
                "vulnerability": "",
                "consequence": "",
                "affected": "",
                "areas": [],
                "sourceTerm": "",
                "consequenceTerm": "",
                "impactTerm": "",
            }
        ]


def test_the_risks_heading_ends_the_annex_section_above_it():
    """Or the whole risk register would become the answer to 2(h)."""
    assert annex_sections(TWO_RISKS) == {"q:annex-2:2h": "Cybersecurity measures."}


class TestMergingRisks:
    """The rows are one list: they are filled or replaced together."""

    rows = [{"risk": "from the document"}]

    def test_an_empty_form_takes_the_document_s_rows(self):
        assert merge_risks([], self.rows, "empty") == (self.rows, False)

    def test_blank_rows_on_the_form_count_as_empty(self):
        blank = [{"risk": " ", "source": "", "areas": []}]
        assert merge_risks(blank, self.rows, "empty") == (self.rows, False)

    def test_fill_only_empty_keeps_rows_somebody_wrote(self):
        assert merge_risks([{"risk": "typed"}], self.rows, "empty") == (None, True)

    def test_replace_takes_the_document_s_rows(self):
        assert merge_risks([{"risk": "typed"}], self.rows, "replace") == (self.rows, False)

    def test_a_document_without_risks_never_empties_the_rows(self):
        assert merge_risks([{"risk": "typed"}], [], "replace") == (None, True)
        assert merge_risks([], [], "empty") == (None, False)


# The risk section ends at an Annex heading, as the Components section does. A .docx or .txt
# has no '#' headings, so without this every line after "Risks" was a risk line.
AFTER_THE_RISKS = """Risks
Risk: Biased scoring
Source: Proxy features

Annex IV(2)(b)
Measure: the model is retrained every month on fresh data.

System type: Narrow AI
"""


@pytest.mark.parametrize("annex", ["Annex IV(2)(b)", "Annex IV 2(b)", "2(b)", "IV(2)(b)"])
def test_an_annex_heading_ends_the_risk_section(annex):
    rows = risks_from_text(AFTER_THE_RISKS.replace("Annex IV(2)(b)", annex))
    assert len(rows) == 1
    assert rows[0]["risk"] == "Biased scoring"
    assert rows[0]["source"] == "Proxy features"


def test_an_annex_iv_5_heading_still_opens_it():
    rows = risks_from_text("Annex IV(2)(b)\nSome design text.\n\nAnnex IV(5) Risk management\nRisk: Drift\nSource: Seasonality\n")
    assert [(r["risk"], r["source"]) for r in rows] == [("Drift", "Seasonality")]
