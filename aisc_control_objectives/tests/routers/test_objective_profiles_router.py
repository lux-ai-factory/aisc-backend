"""
The two built-in objective profiles (pilot parity).

A fresh project is on "Fairness and oversight" (the 10 objectives of the
fairness-and-oversight dimensions); switching to "Full AI Act" selects all 50;
switching back returns the 10. The selection is what the evidence map shows.
"""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_control_objectives.models import ObjectiveProfile
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_control_objectives.routers.control_objectives import router as co_router

co_client = TestAsyncClient(co_router)

#: the catalogue's fairness-and-oversight objectives (REQ1 + REQ5 + REQ7), in the
#: catalogue's own (objective_id) order — the order every reader uses.
FAIRNESS_OVERSIGHT = ["O1", "O2", "O21", "O22", "O23", "O26", "O27", "O28", "O3", "O4"]


async def _project(name="profiles", role=MembershipRole.Owner):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    if role is not None:
        await ProjectMember.objects.acreate(
            project=project, subject="development", role=role,
        )
    return project


class ObjectiveProfilesRouterTest(TestCase):

    async def test_a_fresh_project_is_on_the_fairness_and_oversight_profile(self):
        project = await _project()
        body = (await co_client.get(f"/{project.pid}/control-objectives/profiles")).json()
        self.assertEqual([p["key"] for p in body["profiles"]],
                         ["full-ai-act", "fairness-oversight"])
        by_key = {p["key"]: p for p in body["profiles"]}
        self.assertEqual(by_key["full-ai-act"]["name"], "Full AI Act")
        self.assertEqual(len(by_key["full-ai-act"]["objectives"]), 50)
        self.assertEqual(by_key["fairness-oversight"]["name"], "Fairness and oversight")
        self.assertEqual(by_key["fairness-oversight"]["objectives"], FAIRNESS_OVERSIGHT)
        self.assertEqual(body["current"], "fairness-oversight")
        self.assertEqual(body["selected"], FAIRNESS_OVERSIGHT)

    async def test_switching_profile_fills_the_selection(self):
        project = await _project()
        resp = await co_client.put(
            f"/{project.pid}/control-objectives/profile",
            json={"name": "", "profile_key": "full-ai-act"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["profile_key"], "full-ai-act")
        self.assertEqual(len(resp.json()["objectives"]), 50)

        body = (await co_client.get(f"/{project.pid}/control-objectives/profiles")).json()
        self.assertEqual(body["current"], "full-ai-act")
        self.assertEqual(len(body["selected"]), 50)

        resp = await co_client.put(
            f"/{project.pid}/control-objectives/profile",
            json={"name": "", "profile_key": "fairness-oversight"},
        )
        self.assertEqual(resp.json()["objectives"], FAIRNESS_OVERSIGHT)

    async def test_a_stored_profile_is_the_selection(self):
        project = await _project()
        await ObjectiveProfile.objects.acreate(
            project=project, name="", profile_key="", objectives=["O1"],
        )
        body = (await co_client.get(f"/{project.pid}/control-objectives/profiles")).json()
        self.assertEqual(body["selected"], ["O1"])
        # no key stored: the reported profile falls back to the default
        self.assertEqual(body["current"], "fairness-oversight")

    async def test_an_unknown_profile_is_refused(self):
        project = await _project()
        resp = await co_client.put(
            f"/{project.pid}/control-objectives/profile",
            json={"name": "", "profile_key": "no-such-profile"},
        )
        self.assertEqual(resp.status_code, 400)

    async def test_a_viewer_may_read_but_not_switch(self):
        project = await _project("viewer-project", role=MembershipRole.Viewer)
        read = await co_client.get(f"/{project.pid}/control-objectives/profiles")
        self.assertEqual(read.status_code, 200)
        write = await co_client.put(
            f"/{project.pid}/control-objectives/profile",
            json={"name": "", "profile_key": "full-ai-act"},
        )
        self.assertEqual(write.status_code, 403)

    async def test_a_stranger_gets_404(self):
        project = await _project("stranger-project", role=None)
        resp = await co_client.get(f"/{project.pid}/control-objectives/profiles")
        self.assertEqual(resp.status_code, 404)
