"""The VAIR picks a document names: the system's type and purpose, and the four tag sets.

The form offers VAIR's own lists for these, so a document can name them the way it names a risk's
terms: a labelled line, each value a VAIR id or label, several separated by commas. A value that names
no VAIR term of its field's class is left out, never guessed. "Purpose:" stays the use case's text,
which is what documents mean by it; the VAIR purpose has its own label.
"""
from aisc_qualification.prefill.picks import picks_from_text

HEADER = """MCAS: technical documentation

System name: MicroCredit Assist Score (MCAS)
Provider: Creditum AI SARL
System type: Narrow AI
VAIR purpose: Assessing Creditworthiness
Capabilities: Profiling, Natural Language Generation, QuestionAnswering, telepathy
Application domains: Private Service
Market form: Software, Service
Locality: Workplace

Intended purpose:
Retail banking and consumer micro-finance in the EU.
"""


def test_every_pick_is_read_as_vair_ids():
    assert picks_from_text(HEADER) == {
        "systemType": "NarrowAI",
        "purpose": "AssessingCreditworthiness",
        "targetSystemTags": ["Profiling", "NaturalLanguageGeneration", "QuestionAnswering"],
        "sectorTags": ["PrivateService"],
        "marketFormTags": ["Software", "Service"],
        "localityTags": ["Workplace"],
    }


def test_a_value_that_names_no_term_is_left_out_and_a_field_with_none_is_absent():
    picks = picks_from_text("Application domains: retail\nSystem type: a very clever box\nLocality: Workplace\n")
    assert picks == {"localityTags": ["Workplace"]}


def test_the_use_case_text_is_not_a_vair_purpose():
    assert "purpose" not in picks_from_text("Purpose: Assessing Creditworthiness\n")


def test_lines_inside_the_components_and_risks_sections_are_not_the_system_s():
    text = ("Components\n\nComponent 1\nName: m\nType: Decision Tree\n\n"
            "Risks\n\nRisk 1\nRisk: r\nMarket form: Software\n")
    assert picks_from_text(text) == {}


# The pilot's `test_the_upload_returns_them` drove its HTTP service; that path is
# this app's own router, covered by tests/routers/test_qualification_prefill.py.


def test_a_document_names_the_deployer_kind():
    assert picks_from_text("Kind of deployer: Educational Institution\n")["deployerTerm"] == "EducationalInstitution"


def test_a_document_names_the_provider_kind():
    assert picks_from_text("Provider term: Public Authority\n")["providerTerm"] == "PublicAuthority"


def test_an_unknown_deployer_kind_is_left_open():
    assert "deployerTerm" not in picks_from_text("Kind of deployer: Retail bank\n")


def test_a_pick_after_an_annex_heading_that_ends_the_risk_section_is_read():
    # The risk section used to end only at a '#' heading, so this line was skipped as a risk line
    text = (
        "Risks\nRisk: Biased scoring\nControl: Human review of every refusal\n\n"
        "Annex IV(2)(b)\nMeasure: the model is retrained every month on fresh data.\n\n"
        "System type: Narrow AI\n"
    )
    assert picks_from_text(text) == {"systemType": "NarrowAI"}
