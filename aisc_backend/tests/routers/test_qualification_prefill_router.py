"""
Pre-filling a questionnaire from an uploaded document (pilot parity).

Real documents: a text file, a Markdown file and a Word document built with
python-docx. The rules are text rules — no model — and nothing is stored.
"""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.models.qualification import Questionnaire
from aisc_backend.routers.qualification import router as qual_router
from aisc_backend.services import qualification_prefill

qual_client = TestAsyncClient(qual_router)

DEFINITION = {
    "questions": [
        {"field": "system_name", "text": "What is the name of the system?", "required": True},
        {"field": "provider", "text": "Who provides the system?", "required": True},
        {"field": "purpose", "text": "What is the intended purpose?", "required": False},
    ]
}

DOCUMENT = """\
System assessment
-----------------
What is the name of the system?: Credit scoring assistant
Who provides the system?
ACME Financial Services

What is the intended purpose? Nothing here matches this question.
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

    async def test_a_colon_value_on_the_label_line_is_taken(self):
        self.assertEqual(
            qualification_prefill.propose(DEFINITION, DOCUMENT)[0],
            {"field": "system_name", "value": "Credit scoring assistant",
             "line": 3, "matched": "What is the name of the system?"},
        )

    async def test_the_next_non_empty_line_is_taken(self):
        proposals = {
            p["field"]: p["value"] for p in qualification_prefill.propose(DEFINITION, DOCUMENT)
        }
        self.assertEqual(proposals["provider"], "ACME Financial Services")
        self.assertNotIn("purpose", proposals)

    async def test_a_short_label_would_match_anything_so_it_is_skipped(self):
        definition = {"questions": [{"field": "x", "text": "Use"}]}
        self.assertEqual(qualification_prefill.propose(definition, "Use: whatever"), [])

    async def test_an_opaque_definition_has_nothing_to_match(self):
        self.assertEqual(qualification_prefill.propose({"blocks": []}, DOCUMENT), [])

    async def test_an_unknown_suffix_is_refused(self):
        with self.assertRaises(qualification_prefill.UnsupportedDocument):
            qualification_prefill.extract_text("system.exe", b"\x00\x01")


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
        self.assertEqual(
            [p["field"] for p in body["proposals"]], ["system_name", "provider"]
        )
        self.assertEqual(body["unmatched"], ["purpose"])

    async def test_a_word_document_prefills_the_form(self):
        project = await _project("prefill-docx")
        await _questionnaire(project)
        blob = _docx_bytes([
            "Who provides the system?",
            "ACME Financial Services",
        ])
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload(
                "system.docx", blob,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        proposals = resp.json()["proposals"]
        self.assertEqual([p["field"] for p in proposals], ["provider"])
        self.assertEqual(proposals[0]["value"], "ACME Financial Services")

    async def test_a_markdown_upload_is_read(self):
        project = await _project("prefill-md")
        await _questionnaire(project)
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload("notes.md", b"# Notes\n\nWhat is the intended purpose?\nScreen loan applications\n", "text/markdown")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["proposals"][0]["value"], "Screen loan applications")

    async def test_an_unreadable_file_is_refused_with_415(self):
        project = await _project("prefill-bad")
        await _questionnaire(project)
        resp = await qual_client.post(
            f"/{project.pid}/qualification/prefill",
            FILES={"file": _upload("system.exe", b"\x00\x01", "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 415)
        self.assertIn("not readable", resp.json()["detail"])

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
        self.assertEqual(resp.json()["proposals"], [])
        self.assertIn("no answerable questions", resp.json()["note"])

    async def test_nothing_is_stored(self):
        from aisc_backend.models.qualification import Qualification

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
