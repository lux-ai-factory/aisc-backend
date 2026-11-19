"""
Tests for controls / compliance checklists + the catalogue surface.

Covers: catalogue list (fixture templates, data normally synced from the hosted
catalogue), install into a project's library (idempotent), the reference export
package shape, the submissions lifecycle (draft -> answers -> close ->
reopen-as-new-version), coverage/readiness scoring, and project scoping (a
stranger gets 404).
"""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_controls.models import ControlQuestion, ControlSource, ControlTemplate, ProjectChecklist
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.catalogue import router as catalogue_router
from aisc_backend.routers.control import router as control_router

catalogue_client = TestAsyncClient(catalogue_router)
control_client = TestAsyncClient(control_router)


async def _project(name="controls-project"):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=MembershipRole.Owner,
    )
    return project


async def _seed_controls():
    """Two catalogue checklists, as `manage.py sync_controls_catalogue` would."""
    source, _ = await ControlSource.objects.aupdate_or_create(
        name="AESIA",
        defaults={"slug": "aesia", "url": "https://aesia.digital.gob.es/en/guides"},
    )
    for catalogue_id, title in [
        ("risk-management-checklist", "Risk Management Checklist"),
        ("cybersecurity-checklist", "Cybersecurity Checklist"),
    ]:
        template, _ = await ControlTemplate.objects.aupdate_or_create(
            catalogue_id=catalogue_id,
            defaults={
                "name": title,
                "description": "fixture",
                "source": source,
                "control_topic": "Risk Management" if "risk" in catalogue_id else "Cybersecurity",
                "country_ids": ["ES"],
                "regulation_ids": ["ai-act"],
            },
        )
        for order, text in enumerate(["question one", "question two"], start=1):
            await ControlQuestion.objects.aupdate_or_create(
                template=template, order=order,
                defaults={"text": text, "article": "Article 9", "category": "MG1"},
            )


async def _install(project, catalogue_id="risk-management-checklist"):
    return (await catalogue_client.post(
        f"/projects/{project.pid}/catalogue/installs"
        f"?catalogue_id={catalogue_id}&kind=control",
    ))


class CatalogueTest(TestCase):

    async def test_catalogue_lists_online_controls(self):
        import unittest.mock as mock

        from aisc_backend.services import controls_catalogue

        controls_catalogue._list_cache = None
        tools = [
            {"slug": "risk-management-checklist", "name": "Risk Management Checklist",
             "metadata": {"provider": "AESIA", "control_topic": "Risk Management",
                          "country_ids": ["ES"], "regulation_ids": ["ai-act"]},
             "questions": [{"text": "q1"}, {"text": "q2"}]},
            {"slug": "cybersecurity-checklist", "name": "Cybersecurity Checklist",
             "metadata": {"provider": "AESIA", "control_topic": "Cybersecurity"},
             "questions": [{"text": "q1"}]},
        ]
        with mock.patch.object(
            controls_catalogue, "_get_json",
            new=mock.AsyncMock(side_effect=[tools]),
        ):
            resp = await catalogue_client.get("/catalogue")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            {c["catalogue_id"] for c in body["controls"]},
            {"risk-management-checklist", "cybersecurity-checklist"},
        )
        first = resp.json()["controls"][0] if resp.json()["controls"] else {}
        self.assertIn("title", first)
        # the consolidated tags (topic/source/scope) are surfaced
        self.assertIn("tags", first)
        self.assertTrue(first["tags"])
        # listing must not store anything
        self.assertEqual(await ControlTemplate.objects.acount(), 0)
        # plugins come from the loader; at worst it is empty here
        self.assertIn("plugins", body)

    async def test_install_is_idempotent_and_lands_in_library(self):
        await _seed_controls()
        project = await _project()
        first = await _install(project)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "installed")

        again = await _install(project)
        self.assertEqual(again.json()["status"], "already-installed")

        # the template is now in the project's checklist library
        resp = await control_client.get(f"/{project.pid}/controls")
        self.assertEqual(resp.status_code, 200)
        checklists = resp.json()
        self.assertEqual(len(checklists), 1)
        self.assertEqual(
            checklists[0]["template"]["catalogue_id"], "risk-management-checklist"
        )

    async def test_create_custom_checklist(self):
        project = await _project()
        resp = await control_client.post(
            f"/{project.pid}/controls",
            json={
                "name": "My Checklist",
                "control_topic": "Custom",
                "questions": [
                    {"text": "Is there logging?", "article": "Article 9", "category": "MG"},
                    {"text": "Are logs kept?"},
                ],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["template"]["title"], "My Checklist")
        self.assertEqual(body["question_count"], 2)
        self.assertEqual(
            [q["text"] for q in body["questions"]], ["Is there logging?", "Are logs kept?"]
        )
        # it lands in the project's library like any other checklist
        listing = await control_client.get(f"/{project.pid}/controls")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(
            [c["template"]["title"] for c in listing.json()], ["My Checklist"]
        )

    async def test_create_custom_checklist_requires_name_and_question(self):
        project = await _project()
        no_name = await control_client.post(
            f"/{project.pid}/controls",
            json={"name": "  ", "questions": [{"text": "q"}]},
        )
        self.assertEqual(no_name.status_code, 400)
        no_question = await control_client.post(
            f"/{project.pid}/controls",
            json={"name": "X", "questions": [{"text": "  "}]},
        )
        self.assertEqual(no_question.status_code, 400)

    async def test_unknown_control_is_404(self):
        import unittest.mock as mock

        from aisc_backend.services import controls_catalogue

        project = await _project()
        # unknown id: no local template, hosted catalogue refuses -> 404 (no network)
        with mock.patch.object(
            controls_catalogue, "_get_json",
            new=mock.AsyncMock(
                side_effect=controls_catalogue.CatalogueUnavailable("stubbed")
            ),
        ):
            resp = await _install(project, catalogue_id="does-not-exist")
        self.assertEqual(resp.status_code, 404)


class ExportContractTest(TestCase):

    async def test_export_matches_install_package_shape(self):
        import unittest.mock as mock

        from aisc_backend.services import controls_catalogue

        project = await _project()
        export = {
            "meta": {
                "catalogueId": "risk-management-checklist",
                "title": "Risk Management Checklist",
                "sourceName": "AESIA",
                "sourceUrl": "https://aesia.digital.gob.es/en/guides",
                "controlTopic": "Risk Management",
                "countryIds": ["ES"],
                "regulationIds": ["ai-act"],
            },
            "questions": [{"text": "a question", "article": "Article 9", "category": "MG1"}],
        }
        with mock.patch.object(
            controls_catalogue, "_get_json",
            new=mock.AsyncMock(side_effect=[export]),
        ):
            resp = await catalogue_client.get(
                f"/projects/{project.pid}/catalogue/control/risk-management-checklist/export"
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # the InstallPackage shape the reference /api/install consumed
        self.assertEqual(body["meta"]["catalogueId"], "risk-management-checklist")
        self.assertIn("sourceName", body["meta"])
        self.assertIn("title", body["meta"])
        self.assertTrue(isinstance(body["questions"], list))
        self.assertTrue(all(q.get("text") for q in body["questions"]))


class ControlsLifecycleTest(TestCase):

    async def test_submissions_draft_close_reopen(self):
        await _seed_controls()
        project = await _project()
        await _install(project, "risk-management-checklist")
        cl = await ProjectChecklist.objects.select_related("template").aget(project=project)
        cl_pid = str(cl.pid)

        # create a draft
        created = (await control_client.post(
            f"/{project.pid}/controls/{cl_pid}/submissions", json={"label": "Round 1"}
        )).json()
        self.assertEqual(created["status"], "Draft")
        self.assertEqual(created["version"], 1)
        self.assertIn("coverage", created)
        self.assertIn("readiness", created)

        # answer one question with a score (1-5 readiness scale)
        question = await cl.template.questions.afirst()
        await control_client.post(
            f"/{project.pid}/controls/submissions/{created['pid']}/answers",
            json=[{"question_order": question.order, "answer": "yes", "score": 4}],
        )

        # close, then reopen -> new version in the chain
        closed = (await control_client.post(
            f"/{project.pid}/controls/submissions/{created['pid']}/close"
        )).json()
        self.assertEqual(closed["status"], "Closed")

        await control_client.post(
            f"/{project.pid}/controls/submissions/{created['pid']}/reopen"
        )
        subs = (await control_client.get(
            f"/{project.pid}/controls/{cl_pid}/submissions"
        )).json()
        self.assertEqual(len(subs), 2)          # the reopen made a new Draft
        self.assertEqual({s["status"] for s in subs}, {"Draft", "Closed"})
        versions = sorted(s["version"] for s in subs)
        self.assertEqual(versions, [1, 2])

    async def test_get_submission_returns_its_saved_answers(self):
        await _seed_controls()
        project = await _project()
        await _install(project, "risk-management-checklist")
        cl = await ProjectChecklist.objects.select_related("template").aget(project=project)
        created = (await control_client.post(
            f"/{project.pid}/controls/{cl.pid}/submissions", json={"label": "Round 1"}
        )).json()
        question = await cl.template.questions.afirst()
        await control_client.post(
            f"/{project.pid}/controls/submissions/{created['pid']}/answers",
            json=[{"question_order": question.order, "answer": "kept", "score": 3}],
        )
        resp = await control_client.get(
            f"/{project.pid}/controls/submissions/{created['pid']}"
        )
        self.assertEqual(resp.status_code, 200)
        answers = resp.json()["answers"]
        self.assertEqual(
            answers,
            [{"question_order": question.order, "answer": "kept", "score": 3}],
        )
        missing = await control_client.get(
            f"/{project.pid}/controls/submissions/00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(missing.status_code, 404)

    async def test_report_payload_built(self):
        await _seed_controls()
        project = await _project()
        await _install(project, "risk-management-checklist")
        cl = await ProjectChecklist.objects.aget(project=project)
        created = (await control_client.post(
            f"/{project.pid}/controls/{cl.pid}/submissions", json={"label": "L"}
        )).json()
        payload = (await control_client.post(
            f"/{project.pid}/controls/submissions/{created['pid']}/report"
        )).json()
        self.assertEqual(payload["checklist_id"], "risk-management-checklist")
        self.assertIn("items", payload)
        self.assertTrue(payload["items"])

    async def test_stranger_cannot_see_catalogue_or_controls(self):
        project = await Project.objects.acreate(name="x", description="", status="Ready")
        resp = await catalogue_client.get(
            f"/projects/{project.pid}/catalogue/control/x/export"
        )
        self.assertEqual(resp.status_code, 404)
        resp = await control_client.get(f"/{project.pid}/controls")
        self.assertEqual(resp.status_code, 404)

    async def test_uninstall_removes_checklist_from_library(self):
        await _seed_controls()
        project = await _project()
        await _install(project)
        library = (await control_client.get(f"/{project.pid}/controls")).json()
        self.assertEqual(len(library), 1)

        resp = await control_client.delete(
            f"/{project.pid}/controls/{library[0]['pid']}"
        )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            (await control_client.get(f"/{project.pid}/controls")).json(), []
        )

        resp = await control_client.delete(
            f"/{project.pid}/controls/{library[0]['pid']}"
        )
        self.assertEqual(resp.status_code, 404)

    async def test_stranger_cannot_uninstall(self):
        project = await Project.objects.acreate(name="y", description="", status="Ready")
        resp = await control_client.delete(
            f"/{project.pid}/controls/00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(resp.status_code, 404)

    async def test_scoring_service_labels_exposed_via_api(self):
        """The webapp needs the 1-5 readiness vocabulary; it is exposed through
        the submissions' coverage/readiness (computed on read)."""
        from aisc_backend.services.controls_scoring import SCORE_LABELS, readiness_percent

        self.assertEqual(SCORE_LABELS[1], "Not started")
        self.assertEqual(SCORE_LABELS[5], "Optimized")
        self.assertEqual(readiness_percent([1, 5]), 50)  # (0 + 1) / 2 = 50%
        self.assertIsNone(readiness_percent([]))
