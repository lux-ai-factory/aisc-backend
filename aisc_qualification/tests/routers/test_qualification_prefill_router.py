"""
Pre-filling a questionnaire from an uploaded document (the pilot's rules).

The reading rules are vendored from the pilot (`aisc_backend/prefill/`, see
`tests/pilot_prefill/` for its own suite). What is checked here is this app's
side of it: the form's questions, the profile block, the tag sets and the risk
rows are filled from a real document, the merge the person chose is honoured,
and nothing is stored.
"""
import io
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_qualification.models import Questionnaire
from aisc_qualification.routers.qualification import router as qual_router
from aisc_qualification.services import qualification_prefill

qual_client = TestAsyncClient(qual_router)

DEFINITION = {
    "questions": [
        {"field": "q:data:data-source", "text": "Where does the data come from?", "required": True},
        {"field": "q:docs:logging", "text": "What does the system record in its logs?", "required": True},
        {"field": "q:oversight:human", "text": "How can a person stop the system?", "required": False},
    ]
}

DOCUMENT = """\
System name: Credit scoring assistant
Provider: ACME Financial Services
Description: Scores loan applications for the retail bank.
Intended purpose: assessing creditworthiness.
System type: Narrow AI

Where does the data come from?
The core banking system, over an API.

What does the system record in its logs?
Every decision, with the model version.

Risks
Risk: a borderline applicant is refused
Affected: user
Areas: Right
"""


async def _project(name="prefill"):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=MembershipRole.Owner,
    )
    return project


async def _questionnaire(project):
    """A project questionnaire, selected — prefill reads the form being filled."""
    row = await Questionnaire.objects.acreate(
        project=project, name="House form", definition=DEFINITION,
    )
    selected = await qual_client.put(
        f"/{project.pid}/questionnaire", json={"questionnaire_id": str(row.pid)}
    )
    assert selected.status_code == 200, selected.text
    return row


def _upload(name, blob, content_type="text/plain"):
    """The async test client takes already-parsed uploads under FILES."""
    return SimpleUploadedFile(name, blob, content_type=content_type)


def _docx_bytes(paragraphs):
    from docx import Document

    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class PrefillRulesTest(TestCase):
    """The app's own side of the rules: the form's fields, blocks and rows."""

    def test_a_question_is_answered_by_the_line_that_names_it(self):
        read = qualification_prefill.prefill(DEFINITION, DOCUMENT)
        self.assertEqual(read["values"]["q:data:data-source"], "The core banking system, over an API.")
        self.assertEqual(read["proposed"], ["q:data:data-source", "q:docs:logging"])
        self.assertEqual(read["unmatched"], ["q:oversight:human"])

    def test_the_metadata_lands_in_the_profile_block(self):
        read = qualification_prefill.prefill(DEFINITION, DOCUMENT)
        self.assertEqual(read["profile"], {
            "description": "Scores loan applications for the retail bank.",
            "targetUseCase": "assessing creditworthiness.",
        })
        self.assertEqual(read["profileFilled"], ["description", "targetUseCase"])

    def test_the_vair_picks_land_in_the_tag_sets(self):
        read = qualification_prefill.prefill(DEFINITION, DOCUMENT)
        self.assertEqual(read["tags"], {"targetSystem": ["NarrowAI"]})
        self.assertEqual(read["tagsFilled"], ["targetSystem"])
        self.assertEqual(read["picksProposed"], 1)

    def test_a_document_naming_no_vair_term_leaves_the_tag_sets_empty(self):
        read = qualification_prefill.prefill(DEFINITION, "Application domains: banking, of course\n")
        self.assertEqual(read["tags"], {})

    def test_the_risk_rows_arrive_in_this_form_s_shape(self):
        read = qualification_prefill.prefill(DEFINITION, DOCUMENT)
        self.assertEqual(read["risksProposed"], 1)
        self.assertFalse(read["risksKept"])
        row = read["risks"][0]
        self.assertEqual(row["risk"], "a borderline applicant is refused")
        self.assertEqual(row["affected"], "user")
        self.assertEqual(row["impactAreas"], ["Right"])
        # the measure the pilot's card does not ask about is left for the person
        self.assertEqual(row["control"], "")

    def test_what_the_form_has_no_field_for_is_reported_not_dropped(self):
        read = qualification_prefill.prefill(DEFINITION, DOCUMENT)
        self.assertEqual(read["unplaced"]["systemName"], "Credit scoring assistant")
        self.assertEqual(read["unplaced"]["company"], "ACME Financial Services")

    def test_empty_mode_keeps_what_the_person_typed(self):
        current = {"q:data:data-source": "I already answered this.", "__blocks": {
            "profile": {"description": "mine"},
            "tags": {"sector": ["Health"]},
            "risks": [{"risk": "mine", "impactAreas": ["Right"]}],
        }}
        read = qualification_prefill.prefill(DEFINITION, DOCUMENT, current, "empty")
        self.assertEqual(read["values"]["q:data:data-source"], "I already answered this.")
        self.assertEqual(read["kept"], ["q:data:data-source"])
        self.assertEqual(read["profile"]["description"], "mine")
        self.assertEqual(read["tags"]["sector"], ["Health"])
        self.assertEqual(read["tagsFilled"], ["targetSystem"])
        # rows somebody wrote are left as they are, and said so
        self.assertIsNone(read["risks"])
        self.assertTrue(read["risksKept"])

    def test_replace_takes_the_document_and_still_never_empties_a_block(self):
        current = {"q:data:data-source": "old", "__blocks": {"profile": {"description": "old"},
                                                             "tags": {"sector": ["Health"]}}}
        read = qualification_prefill.prefill(DEFINITION, DOCUMENT, current, "replace")
        self.assertEqual(read["values"]["q:data:data-source"], "The core banking system, over an API.")
        self.assertEqual(read["profile"]["description"], "Scores loan applications for the retail bank.")
        # a tag set the document says nothing about is kept, not emptied
        self.assertEqual(read["tags"]["sector"], ["Health"])

    def test_a_document_that_says_nothing_erases_nothing(self):
        current = {"q:data:data-source": "typed"}
        read = qualification_prefill.prefill(DEFINITION, "Notes\nUnrelated prose.\n", current, "replace")
        self.assertEqual(read["values"]["q:data:data-source"], "typed")
        self.assertEqual(read["proposed"], [])

    def test_an_unknown_suffix_is_refused(self):
        with self.assertRaises(qualification_prefill.UnsupportedDocument):
            qualification_prefill.extract_text("system.exe", b"\x00\x01")

    def test_an_upload_past_the_limit_is_refused_before_it_is_read(self):
        with self.assertRaises(qualification_prefill.UploadTooLarge):
            qualification_prefill.check_size(b"x" * (qualification_prefill.MAX_BYTES + 1))

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(qualification_prefill.UnknownMode):
            qualification_prefill.prefill(DEFINITION, DOCUMENT, {}, "sometimes")


class PrefillRouterTest(TestCase):

    async def test_a_text_file_prefills_the_form(self):
        project = await _project()
        await _questionnaire(project)
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload("system.txt", DOCUMENT.encode(), "text/plain")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["filename"], "system.txt")
        self.assertGreater(body["characters"], 0)
        self.assertEqual(body["source"], "document")
        self.assertIsNone(body["model"])
        self.assertEqual(body["filled"], ["q:data:data-source", "q:docs:logging"])
        self.assertEqual(body["proposed"], ["q:data:data-source", "q:docs:logging"])
        self.assertEqual(body["unmatched"], ["q:oversight:human"])
        self.assertEqual(body["profile"]["targetUseCase"], "assessing creditworthiness.")
        self.assertEqual(body["tags"], {"targetSystem": ["NarrowAI"]})
        self.assertEqual(body["risksProposed"], 1)
        self.assertEqual(body["picks"], {"systemType": "NarrowAI"})

    async def test_the_form_s_own_answers_are_kept_by_the_merge(self):
        project = await _project("prefill-merge")
        await _questionnaire(project)
        current = json.dumps({"q:data:data-source": "typed by me"})
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload("system.txt", DOCUMENT.encode(), "text/plain")},
            data={"current": current},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["values"]["q:data:data-source"], "typed by me")
        self.assertIn("q:data:data-source", body["kept"])

        replaced = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload("system.txt", DOCUMENT.encode(), "text/plain")},
            data={"current": current, "mode": "replace"},
        )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(
            replaced.json()["values"]["q:data:data-source"],
            "The core banking system, over an API.",
        )

    async def test_a_word_document_prefills_the_form(self):
        project = await _project("prefill-docx")
        await _questionnaire(project)
        blob = _docx_bytes([
            "What does the system record in its logs?",
            "Every decision, with the model version.",
        ])
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload(
                "system.docx", blob,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            resp.json()["values"]["q:docs:logging"], "Every decision, with the model version."
        )

    async def test_a_markdown_upload_is_read(self):
        project = await _project("prefill-md")
        await _questionnaire(project)
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload(
                "notes.md",
                b"# Notes\n\nHow can a person stop the system?\nThe red button in the console.\n",
                "text/markdown",
            )},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["values"]["q:oversight:human"], "The red button in the console.")

    async def test_an_unreadable_file_is_refused_with_415(self):
        project = await _project("prefill-bad")
        await _questionnaire(project)
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload("system.exe", b"\x00\x01", "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 415)

    async def test_an_upload_past_the_limit_is_refused_with_413(self):
        project = await _project("prefill-large")
        await _questionnaire(project)
        oversized = b"x" * (qualification_prefill.MAX_BYTES + 1)
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload("system.txt", oversized, "text/plain")},
        )
        self.assertEqual(resp.status_code, 413)

    async def test_a_current_value_that_is_not_an_object_is_refused(self):
        project = await _project("prefill-current")
        await _questionnaire(project)
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload("system.txt", DOCUMENT.encode(), "text/plain")},
            data={"current": "[1, 2]"},
        )
        self.assertEqual(resp.status_code, 422)

    async def test_an_unknown_mode_is_refused_with_422(self):
        project = await _project("prefill-mode")
        await _questionnaire(project)
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload("system.txt", DOCUMENT.encode(), "text/plain")},
            data={"mode": "whenever"},
        )
        self.assertEqual(resp.status_code, 422)

    async def test_an_opaque_questionnaire_says_there_is_nothing_to_fill(self):
        project = await _project("prefill-opaque")
        await Questionnaire.objects.acreate(
            project=project, name="Opaque", definition={"blocks": [{"id": "b1"}]},
        )
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload("system.txt", DOCUMENT.encode(), "text/plain")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["values"], {})
        self.assertIn("no answerable questions", resp.json()["note"])
        # the document's own blocks are still read: an opaque form is not a dead end
        self.assertEqual(resp.json()["profile"]["targetUseCase"], "assessing creditworthiness.")

    async def test_nothing_is_stored(self):
        from aisc_qualification.models import Qualification

        project = await _project("prefill-store")
        await _questionnaire(project)
        await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload("system.txt", DOCUMENT.encode(), "text/plain")},
        )
        self.assertEqual(await Qualification.objects.acount(), 0)

    async def test_a_viewer_is_refused_and_a_stranger_gets_404(self):
        viewer = await _project("prefill-viewer")
        await _questionnaire(viewer)
        await ProjectMember.objects.filter(project=viewer, subject="development").aupdate(
            role=MembershipRole.Viewer
        )
        resp = await qual_client.post(
            f"/{viewer.pid}/qualification/prefill",
            FILES={"file": _upload("system.txt", DOCUMENT.encode(), "text/plain")},
        )
        self.assertEqual(resp.status_code, 403)

        stranger = await Project.objects.acreate(
            name="prefill-stranger", description="", status="Ready"
        )
        resp = await qual_client.post(
            f"/{stranger.pid}/qualification/prefill",
            FILES={"file": _upload("system.txt", DOCUMENT.encode(), "text/plain")},
        )
        self.assertEqual(resp.status_code, 404)
