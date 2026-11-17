"""Prefilling a custom form: its questions are matched by their own wording.

proposals_for_questions(text, questions) -> {field: answer}, where each
question is {"field", "text", "citation", "annexPoint"}. Deterministic: the
question's text or citation named on a line, or the Annex heading of its tag.
"""
import pytest

try:
    from aisc_backend.prefill.fields import proposals_for_questions
except ImportError as _missing:  # if the function is missing, each test fails instead of the whole suite
    def proposals_for_questions(*_args, _error=_missing, **_kwargs):
        raise _error


Q1 = {"field": "q:f-x:q1", "text": "Who signs off a model release?", "citation": "Acme AI Policy §4.2", "annexPoint": None}
Q2 = {"field": "q:f-x:q2", "text": "How are incidents reported?", "citation": "", "annexPoint": None}


def test_the_spec_example():
    doc = (
        "## Acme AI Policy §4.2\n"
        "The head of data science signs off every release.\n"
        "## How are incidents reported?\n"
        "Through the risk desk, within 24 hours.\n"
    )
    assert proposals_for_questions(doc, [Q1, Q2]) == {
        "q:f-x:q1": "The head of data science signs off every release.",
        "q:f-x:q2": "Through the risk desk, within 24 hours.",
    }


def test_the_question_text_names_it():
    doc = "Who signs off a model release?\nThe CTO.\n"
    assert proposals_for_questions(doc, [Q1]) == {"q:f-x:q1": "The CTO."}


@pytest.mark.parametrize(
    "line",
    [
        "**1. Who signs off a model release**",
        "- who signs off a MODEL release:",
        "4.2 Who signs off a model release?",
        "1) Who   signs off a model release .",
        "# Who signs off a model release?",
        "• Who signs off a model release?",
        "Ｗho signs off a model release?",  # full-width W, NFKC makes it W
    ],
)
def test_normalisation(line):
    doc = f"{line}\nThe CTO.\n"
    assert proposals_for_questions(doc, [Q1]) == {"q:f-x:q1": "The CTO."}


def test_whitespace_around_the_section_sign_does_not_matter():
    doc = "Acme AI Policy § 4.2\nThe CTO.\n"
    assert proposals_for_questions(doc, [Q1]) == {"q:f-x:q1": "The CTO."}


def test_the_citation_then_the_text_names_it():
    doc = "Acme AI Policy §4.2 Who signs off a model release?\nThe CTO.\n"
    assert proposals_for_questions(doc, [Q1]) == {"q:f-x:q1": "The CTO."}


def test_a_citation_shorter_than_3_characters_names_nothing():
    q = {**Q2, "citation": "§7"}
    assert proposals_for_questions("§7\nThrough the risk desk.\n", [q]) == {}


def test_a_label_with_its_value_on_the_line():
    doc = "Acme AI Policy §4.2: The CTO signs off.\nAfter the review board.\n"
    assert proposals_for_questions(doc, [Q1]) == {"q:f-x:q1": "The CTO signs off. After the review board."}


def test_a_label_with_nothing_after_it_takes_the_lines_below():
    doc = "How are incidents reported:\nThrough the risk desk.\n"
    assert proposals_for_questions(doc, [Q2]) == {"q:f-x:q2": "Through the risk desk."}


@pytest.mark.parametrize(
    "stop",
    ["How are incidents reported?", "Annex IV(2)(b)", "2(c)", "### Anything else", "Risks"],
)
def test_an_answer_ends_at_the_next_line_that_names_something(stop):
    doc = f"Who signs off a model release?\nThe CTO.\n{stop}\nNot part of the answer.\n"
    assert proposals_for_questions(doc, [Q1, Q2])["q:f-x:q1"] == "The CTO."


def test_the_answer_lines_are_joined_with_single_spaces():
    doc = "Who signs off a model release?\nThe CTO,\n\n   after   review.\n"
    assert proposals_for_questions(doc, [Q1]) == {"q:f-x:q1": "The CTO, after review."}


def test_a_tagged_question_is_also_filled_by_its_annex_heading():
    q3 = {"field": "q:f-x:q3", "text": "How was it built?", "citation": "Acme §2", "annexPoint": "2a"}
    doc = "Annex IV(2)(a)\nFrom a pre-trained model.\n"
    assert proposals_for_questions(doc, [q3]) == {"q:f-x:q3": "From a pre-trained model."}


@pytest.mark.parametrize("heading", ["1(d)", "1(e)"])
def test_both_letters_of_a_merged_point_fill_a_question_tagged_with_it(heading):
    q = {"field": "q:f-x:q4", "text": "How is it supplied?", "citation": "", "annexPoint": "1de"}
    assert proposals_for_questions(f"{heading}\nAs a download.\n", [q]) == {"q:f-x:q4": "As a download."}


def test_the_first_match_in_the_document_wins():
    doc = (
        "Who signs off a model release?\nThe CTO.\n"
        "Acme AI Policy §4.2\nThe board.\n"
    )
    assert proposals_for_questions(doc, [Q1]) == {"q:f-x:q1": "The CTO."}


def test_two_questions_with_the_same_wording_both_get_it():
    twin = {**Q1, "field": "q:f-y:q1", "citation": "Governance §1"}
    doc = "Who signs off a model release?\nThe CTO.\n"
    assert proposals_for_questions(doc, [Q1, twin]) == {"q:f-x:q1": "The CTO.", "q:f-y:q1": "The CTO."}


def test_a_question_the_document_does_not_answer_is_absent_not_empty():
    doc = "Who signs off a model release?\n\nHow are incidents reported?\nThrough the risk desk.\n"
    assert proposals_for_questions(doc, [Q1, Q2]) == {"q:f-x:q2": "Through the risk desk."}


def test_a_mention_inside_a_sentence_names_nothing():
    doc = "We describe who signs off a model release? in chapter 3.\nThe CTO.\n"
    assert proposals_for_questions(doc, [Q1]) == {}
