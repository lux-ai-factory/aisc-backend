"""
"Suggest with AI" on the risk & control matrix (pilot parity), through the router.

The model itself is a test double (there is no model in CI) and it is patched at
the *model boundary* only: `risk_mapper.resolve_proposer`, the function that
would reach the project's helper. Every rule under test — the 503 with no model,
the profile scope, the AI/person source, the replacement of an earlier AI run —
is our own code.
"""
from unittest.mock import patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.control_objective import (
    ControlObjective,
    Risk,
    RiskObjectiveMapping,
)
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.control_objectives import router as co_router
from aisc_backend.services import risk_mapper

co_client = TestAsyncClient(co_router)


async def _project(name="risk-mapper", role=MembershipRole.Owner):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    if role is not None:
        await ProjectMember.objects.acreate(
            project=project, subject="development", role=role,
        )
    return project


async def _risk(project, name="Wrongly rejected", airo="stale data → loan refused"):
    return await Risk.objects.acreate(
        project=project, position=1, name=name, airo_text=airo,
    )


def _proposer(proposals):
    """A `resolve_proposer` double: it resolves to a model that answers `proposals`."""

    async def resolve(project):
        async def propose(risk, offered):
            return proposals

        return propose

    return resolve


class RiskMapperRouterTest(TestCase):

    async def test_no_model_configured_answers_503(self):
        project = await _project()
        await _risk(project)
        resp = await co_client.post(
            f"/{project.pid}/control-objectives/risks/mappings/suggest", json={}
        )
        self.assertEqual(resp.status_code, 503)
        self.assertIn("no model is configured", resp.json()["detail"])
        self.assertEqual(await RiskObjectiveMapping.objects.acount(), 0)

    async def test_a_run_links_the_proposals_and_reports_what_it_dropped(self):
        project = await _project()
        await _risk(project)
        proposals = [
            {"objective_id": "O1", "quote": "loan refused"},
            {"objective_id": "O999", "quote": "loan refused"},  # not in the profile
        ]
        with patch.object(risk_mapper, "resolve_proposer", _proposer(proposals)):
            resp = await co_client.post(
                f"/{project.pid}/control-objectives/risks/mappings/suggest", json={}
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["attempts"], 1)
        self.assertEqual([m["objective_id"] for m in body["created"]], ["O1"])
        self.assertEqual(body["created"][0]["source"], "ai")
        self.assertEqual(body["created"][0]["quote"], "loan refused")
        self.assertEqual(
            body["dropped"][0]["reason"], "objective O999 is not in the profile"
        )

    async def test_only_the_profiles_objectives_are_offered(self):
        project = await _project()
        await _risk(project)
        seen = {}

        async def resolve(project):
            async def propose(risk, offered):
                seen["ids"] = [o["id"] for o in offered]
                return []

            return propose

        with patch.object(risk_mapper, "resolve_proposer", resolve):
            await co_client.post(
                f"/{project.pid}/control-objectives/risks/mappings/suggest", json={}
            )
        # a fresh project is on "Fairness and oversight": the 10 of REQ1/REQ5/REQ7
        self.assertEqual(
            sorted(seen["ids"]),
            sorted(["O1", "O2", "O21", "O22", "O23", "O26", "O27", "O28", "O3", "O4"]),
        )

    async def test_a_risk_with_a_hand_link_is_left_alone(self):
        project = await _project()
        risk = await _risk(project)
        hand = await ControlObjective.objects.aget(objective_id="O1")
        await RiskObjectiveMapping.objects.acreate(
            project=project, risk=risk, objective=hand, quote="by hand", source="person",
        )
        proposals = [{"objective_id": "O21", "quote": "loan refused"}]
        with patch.object(risk_mapper, "resolve_proposer", _proposer(proposals)):
            resp = await co_client.post(
                f"/{project.pid}/control-objectives/risks/mappings/suggest", json={}
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["created"], [])
        rows = {
            m.objective.objective_id: m
            async for m in RiskObjectiveMapping.objects.filter(
                project=project
            ).select_related("objective")
        }
        self.assertEqual(set(rows), {"O1"})
        self.assertEqual(rows["O1"].source, "person")
        self.assertEqual(rows["O1"].quote, "by hand")

    async def test_an_earlier_ai_link_is_replaced_by_the_new_run(self):
        project = await _project()
        risk = await _risk(project)
        stale = await ControlObjective.objects.aget(objective_id="O21")
        await RiskObjectiveMapping.objects.acreate(
            project=project, risk=risk, objective=stale, quote="old run", source="ai",
        )
        proposals = [{"objective_id": "O22", "quote": "stale data"}]
        with patch.object(risk_mapper, "resolve_proposer", _proposer(proposals)):
            resp = await co_client.post(
                f"/{project.pid}/control-objectives/risks/mappings/suggest", json={}
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        rows = {
            m.objective.objective_id: m
            async for m in RiskObjectiveMapping.objects.filter(
                project=project
            ).select_related("objective")
        }
        self.assertEqual(set(rows), {"O22"})
        self.assertEqual(rows["O22"].source, "ai")

    async def test_also_replace_manual_replaces_hand_links(self):
        project = await _project()
        risk = await _risk(project)
        hand = await ControlObjective.objects.aget(objective_id="O1")
        await RiskObjectiveMapping.objects.acreate(
            project=project, risk=risk, objective=hand, quote="by hand", source="person",
        )
        proposals = [{"objective_id": "O21", "quote": "loan refused"}]
        with patch.object(risk_mapper, "resolve_proposer", _proposer(proposals)):
            resp = await co_client.post(
                f"/{project.pid}/control-objectives/risks/mappings/suggest",
                json={"also_replace_manual": True},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        rows = {
            m.objective.objective_id: m
            async for m in RiskObjectiveMapping.objects.filter(
                project=project
            ).select_related("objective")
        }
        self.assertEqual(set(rows), {"O21"})

    async def test_a_viewer_may_not_run_it_and_a_stranger_gets_404(self):
        viewer = await _project("viewer-mapper", role=MembershipRole.Viewer)
        await _risk(viewer)
        resp = await co_client.post(
            f"/{viewer.pid}/control-objectives/risks/mappings/suggest", json={}
        )
        self.assertEqual(resp.status_code, 403)

        stranger = await _project("stranger-mapper", role=None)
        resp = await co_client.post(
            f"/{stranger.pid}/control-objectives/risks/mappings/suggest", json={}
        )
        self.assertEqual(resp.status_code, 404)
