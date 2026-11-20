"""Reading the form's fields out of a document, with no model at all.

The form asks for seven pieces of metadata and fourteen questions, and the
fourteen are the points of EU AI Act Annex IV. A technical documentation file
is usually written against the same Annex, so its own headings are the signal:
"Annex IV(1)(a)", "1(a)", or the labels a person writes, "System name:".

This is what runs on every install. A model can propose more where one is
configured, but nothing here depends on that.
"""
from aisc_qualification.prefill.fields import METADATA_FIELDS, metadata_from_text, proposals_from_text


def proposal_for(text, field):
    return proposals_from_text(text).get(field)


class TestTheMetadataPeopleLabel:
    def test_a_label_on_its_own_line(self):
        assert proposal_for("System name: MCAS\n", "systemName") == "MCAS"

    def test_a_label_with_the_value_underneath(self):
        assert proposal_for("System name\nMCAS\n", "systemName") == "MCAS"

    def test_the_labels_it_knows(self):
        text = (
            "System name: MCAS\n"
            "Version: 1.2.0\n"
            "Provider: Example Bank\n"
            "Intended purpose: Scoring microcredit applications\n"
            "Intended users: Loan officers\n"
            "Deployers: Partner banks\n"
        )
        found = proposals_from_text(text)
        assert found["systemName"] == "MCAS"
        assert found["systemVersion"] == "1.2.0"
        assert found["company"] == "Example Bank"
        assert found["targetUseCase"] == "Scoring microcredit applications"
        assert found["targetUsers"] == "Loan officers"
        assert found["intendedDeployers"] == "Partner banks"

    def test_the_wording_may_vary(self):
        for label in ("Name of the system", "AI system name", "System"):
            assert proposal_for(f"{label}: MCAS\n", "systemName") == "MCAS"
        for label in ("Provider", "Company", "Organisation", "Organization"):
            assert proposal_for(f"{label}: Example Bank\n", "company") == "Example Bank"

    def test_a_label_inside_a_sentence_is_not_a_label(self):
        """"...the system name is chosen by the provider" is prose, not a field."""
        assert proposal_for("We think the system name: is a detail.\n", "systemName") != "is a detail."

    def test_nothing_found_is_nothing_proposed(self):
        assert proposals_from_text("An essay with no labels in it at all.") == {}

    def test_every_metadata_field_the_form_has_is_known_here(self):
        assert set(METADATA_FIELDS) == {
            "systemName",
            "systemVersion",
            "company",
            "description",
            "targetUseCase",
            "targetUsers",
            "intendedDeployers",
        }


class TestTheAnnexQuestions:
    def test_a_citation_heading_carries_the_paragraphs_under_it(self):
        text = (
            "Annex IV(1)(a)\n"
            "This release replaces 1.1.0. The scoring model was retrained.\n"
            "\n"
            "Annex IV(1)(b)\n"
            "It reads the core banking system over an API.\n"
        )
        found = proposals_from_text(text)
        assert "retrained" in found["q:annex-1:1a"]
        assert "core banking" in found["q:annex-1:1b"]

    def test_the_short_form_works_too(self):
        found = proposals_from_text("1(c)\nPython 3.12 and the vendor runtime 4.x.\n")
        assert "Python 3.12" in found["q:annex-1:1c"]

    def test_the_merged_questions_answer_to_either_half(self):
        """The form merges 1(d) with 1(e) and 1(g) with 1(h); a document that
        uses either letter is talking about the same question."""
        assert "download" in proposals_from_text("1(d)\nSupplied as a download.\n")["q:annex-1:1de"]
        assert "portal" in proposals_from_text("1(e)\nRuns on the portal.\n")["q:annex-1:1de"]
        assert "manual" in proposals_from_text("1(h)\nA manual is provided.\n")["q:annex-1:1gh"]

    def test_a_section_ends_at_the_next_heading(self):
        found = proposals_from_text(
            "2(a)\nBuilt from a pre-trained model.\n2(b)\nThe design choices were these.\n"
        )
        assert "design choices" not in found["q:annex-2:2a"]
        assert "design choices" in found["q:annex-2:2b"]

    def test_a_citation_nobody_asks_about_is_ignored(self):
        """Annex IV(3) exists; this form does not ask about it."""
        assert proposals_from_text("3(a)\nNot a question on this form.\n") == {}

    def test_blank_sections_are_not_proposals(self):
        """An empty heading is not an answer, and overwriting a typed answer
        with nothing would be the worst outcome of this whole feature."""
        assert proposals_from_text("Annex IV(1)(a)\n\nAnnex IV(1)(b)\nSomething.\n") == {
            "q:annex-1:1b": "Something."
        }


def test_the_shared_file_is_what_it_reads():
    """The mapping lives with the form, in src/data/prefillFields.json, and is
    copied in beside this package at build time. If it is not found the service
    fails at import rather than proposing nothing for every question."""
    from aisc_qualification.prefill.fields import ANNEX_FIELDS, METADATA_FIELDS

    assert ANNEX_FIELDS[("1", "d")] == ANNEX_FIELDS[("1", "e")]
    assert len(set(ANNEX_FIELDS.values())) == 14
    assert "systemName" in METADATA_FIELDS


def test_the_container_layout_has_no_repo_above_the_package(monkeypatch):
    """In the image the package is /app/prefill, with no src/data three levels
    up. Asking for that parent by index would raise IndexError while the list
    of places to look is still being built, and the service would fail at start
    before looking in the right one.

    The image sets no PREFILL_FIELDS_PATH, so neither does this test: an
    explicit path goes first."""
    from pathlib import Path

    monkeypatch.delenv("PREFILL_FIELDS_PATH", raising=False)

    from aisc_qualification.prefill.fields import candidate_paths

    places = candidate_paths(Path("/app/prefill/fields.py"))
    assert places, "no candidates at all"
    assert str(places[0]).endswith("/app/prefill/prefill_fields.json")


def test_the_repo_layout_still_finds_the_shared_file():
    from pathlib import Path

    from aisc_qualification.prefill.fields import candidate_paths

    places = [
        str(p)
        for p in candidate_paths(Path("/w/apps/qualification/services/prefill/prefill/fields.py"))
    ]
    assert "/w/apps/qualification/src/data/prefillFields.json" in places


# The default form through the custom-form reader.


def _default_questions():
    """The pilot's `tests/test_app.py::default_questions`, reading the list it
    extracts from `src/data/keyQuestions.ts`. The extraction is done once, in
    `default_questions.json` beside this file, because the TypeScript it reads
    is not vendored: the values are the pilot's own, question for question."""
    import json
    from pathlib import Path

    here = Path(__file__).resolve().parent / "default_questions.json"
    return json.loads(here.read_text(encoding="utf-8"))


def test_the_default_questions_propose_what_the_annex_headings_do():
    from aisc_qualification.prefill.fields import annex_sections

    try:
        from aisc_qualification.prefill.fields import proposals_for_questions
    except ImportError as exc:
        raise AssertionError(f"prefill.fields.proposals_for_questions is missing: {exc}")
    text = (
        "Annex IV(1)(a)\nThis release replaces 1.1.0. The scoring model was retrained.\n\n"
        "Annex IV(1)(b)\nIt reads the core banking system over an API.\n"
        "1(d)\nSupplied as a download.\n1(e)\nRuns on the portal.\n"
        "2(a)\nBuilt from a pre-trained model.\n2(b)\nThe design choices were these.\n"
    )
    assert proposals_for_questions(text, _default_questions()) == annex_sections(text)


# The 14 Annex IV point ids in the prefill service.


def test_annex_point_ids_equal_the_repo_json_in_order():
    import json
    from pathlib import Path

    try:
        from aisc_qualification.prefill.fields import ANNEX_POINT_IDS
    except ImportError as exc:
        raise AssertionError(f"prefill.fields.ANNEX_POINT_IDS is missing: {exc}")
    # the pilot read src/data/annexPoints.json; here it is the copy vendored beside the package
    from aisc_backend import prefill

    points = Path(prefill.__file__).resolve().parent / "annexPoints.json"
    ids = [p["id"] for p in json.loads(points.read_text(encoding="utf-8"))["points"]]
    assert len(ids) == 14
    assert list(ANNEX_POINT_IDS) == ids


def test_a_hyphen_inside_a_word_is_not_a_label_separator():
    # "System-level logging" used to propose systemName = "level logging ...", and in replace mode
    # overwrote what the person typed. A label ends at a colon, or at a dash with space around it.
    text = (
        "System-level logging is enabled for every decision.\n"
        "Release-to-production happens monthly.\n"
    )
    assert metadata_from_text(text) == {}


def test_a_spaced_dash_still_separates_a_label():
    assert metadata_from_text("System name - Credit scorer\n") == {"systemName": "Credit scorer"}
