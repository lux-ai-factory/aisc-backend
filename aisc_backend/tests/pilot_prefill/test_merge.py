"""What happens to answers that are already there.

Uploading onto an empty form is easy. Uploading onto a form somebody has been
typing into is the case worth being careful about, so the caller says which of
two things it wants, and neither of them can lose work silently:

* **empty**: fill the fields that are blank, leave every typed answer alone.
* **replace**: take the document's answer wherever it has one.

Under both, a field the document says nothing about keeps what it had.
"Replace everything" means replace it with the document, not with nothing.
"""
import pytest

from aisc_backend.prefill.merge import UnknownMode, merge


class TestFillingOnlyTheEmptyOnes:
    def test_a_blank_field_takes_the_proposal(self):
        result = merge({"systemName": ""}, {"systemName": "MCAS"}, "empty")
        assert result.values["systemName"] == "MCAS"
        assert result.filled == ["systemName"]

    def test_a_typed_answer_is_left_alone(self):
        result = merge({"systemName": "Mine"}, {"systemName": "MCAS"}, "empty")
        assert result.values["systemName"] == "Mine"
        assert result.kept == ["systemName"]
        assert result.filled == []

    def test_whitespace_is_not_an_answer(self):
        result = merge({"systemName": "   \n"}, {"systemName": "MCAS"}, "empty")
        assert result.values["systemName"] == "MCAS"

    def test_a_field_that_is_not_there_at_all_counts_as_empty(self):
        result = merge({}, {"systemName": "MCAS"}, "empty")
        assert result.values["systemName"] == "MCAS"


class TestReplacing:
    def test_a_typed_answer_gives_way_to_the_document(self):
        result = merge({"systemName": "Mine"}, {"systemName": "MCAS"}, "replace")
        assert result.values["systemName"] == "MCAS"
        assert result.filled == ["systemName"]

    def test_a_field_the_document_says_nothing_about_keeps_what_it_had(self):
        """The worst outcome of this whole feature would be a form emptied by
        an upload. "Replace everything" replaces it with the document, and the
        document is silent about this one."""
        result = merge({"company": "Example Bank"}, {"systemName": "MCAS"}, "replace")
        assert result.values["company"] == "Example Bank"
        assert result.kept == ["company"]

    def test_an_empty_proposal_never_erases(self):
        result = merge({"company": "Example Bank"}, {"company": "   "}, "replace")
        assert result.values["company"] == "Example Bank"


class TestBothWays:
    def test_the_answer_says_what_it_did(self):
        result = merge(
            {"systemName": "Mine", "company": ""},
            {"systemName": "MCAS", "company": "Example Bank", "targetUsers": "Officers"},
            "empty",
        )
        assert sorted(result.filled) == ["company", "targetUsers"]
        assert result.kept == ["systemName"]

    def test_a_mode_nobody_defined_is_refused_rather_than_guessed(self):
        for bad in ("overwrite", "", None, "EMPTY"):
            with pytest.raises(UnknownMode):
                merge({}, {}, bad)

    def test_nothing_proposed_changes_nothing(self):
        result = merge({"systemName": "Mine"}, {}, "replace")
        assert result.values == {"systemName": "Mine"}
        assert result.filled == []
