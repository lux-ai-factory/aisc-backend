"""
Tests for the B4 assessment-coverage endpoint: per-objective priority plus the
indicative checklist/plugin keyword matching, and project scoping.
"""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_controls.models import ControlSource, ControlTemplate, ProjectChecklist
from aisc_control_objectives.models import ControlObjective
from aisc_plugins.models import Plugin
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.assessment import router as assessment_router
from aisc_backend.services.assessment_coverage import keywords

assessment_client = TestAsyncClient(assessment_router)


async def _project(name="assessment-project"):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=MembershipRole.Owner
    )
    return project


async def _objective(objective_id, text):
    return await ControlObjective.objects.acreate(
        name=objective_id, objective_id=objective_id, macro_requirement="M",
        text=text, assessment_mode="Test",
    )


async def _installed_checklist(project, topic, title="T"):
    source = await ControlSource.objects.acreate(name="S", slug=f"s-{title}")
    template = await ControlTemplate.objects.acreate(
        name=title, catalogue_id=f"cat-{title}-{project.pid}",
        source=source, control_topic=topic,
    )
    return await ProjectChecklist.objects.acreate(
        project=project, template=template, name=title, control_topic=topic,
    )


class KeywordsTest(TestCase):

    async def test_keywords_drop_short_words_and_stopwords(self):
        self.assertEqual(
            keywords("The assessment of AI robustness and risk"),
            {"assessment", "robustness", "risk"},
        )
        self.assertEqual(keywords(""), set())


class AssessmentCoverageTest(TestCase):

    async def test_matching_lists_covering_items(self):
        project = await _project()
        await _objective("OT1", "Adversarial robustness assessment of the model")
        await _objective("OT2", "Documentation of training data provenance")
        cl = await _installed_checklist(project, "Adversarial robustness testing", title="Rob")
        await Plugin.objects.acreate(
            project=project, name="robustness-prober", package_name="prober",
            version="1.0", display_name="Robustness Prober", enabled=True,
        )
        await Plugin.objects.acreate(
            project=project, name="unrelated-thing", package_name="other",
            version="1.0", display_name="Unrelated Thing", enabled=True,
        )

        resp = await assessment_client.get(f"/{project.pid}/assessment-coverage")
        self.assertEqual(resp.status_code, 200, resp.text)
        by_id = {o["objective_id"]: o for o in resp.json()["objectives"]}
        matched = by_id["OT1"]
        self.assertEqual([c["pid"] for c in matched["checklists"]], [str(cl.pid)])
        self.assertEqual([p["name"] for p in matched["plugins"]], ["robustness-prober"])
        self.assertEqual(matched["mapped_risks"], 0)
        lonely = by_id["OT2"]
        self.assertEqual(lonely["checklists"], [])
        self.assertEqual(lonely["plugins"], [])

    async def test_stranger_gets_404(self):
        project = await Project.objects.acreate(name="z", description="", status="Ready")
        resp = await assessment_client.get(f"/{project.pid}/assessment-coverage")
        self.assertEqual(resp.status_code, 404)
