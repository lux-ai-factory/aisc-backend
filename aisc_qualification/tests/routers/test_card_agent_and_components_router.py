"""
The card agent and the card's links to the engine's components.

The agent is exercised through the router with a *model double* patched at the
model boundary (`card_agent.resolve_agent`) — every rule under test (quote
validation, correctable properties, nothing stored, the preview) is our code.
The component links are exercised end to end against real `AIComponent` rows.
"""
from unittest.mock import patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.ai_system import AIComponent, AISystem
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_qualification.models import Qualification
from aisc_qualification.routers.qualification import router as qual_router
from aisc_qualification.services import card_agent

qual_client = TestAsyncClient(qual_router)

CARD = {
    "nodes": [
        {"id": "n1", "label": "Credit scoring model", "type": "model"},
        {"id": "n2", "label": "Training data", "type": "dataset"},
    ],
    "card": {"summary": {"label": "credit scoring assistant"}},
}


def _reply(**kwargs):
    """A model double: it answers `kwargs`, e.g. `text=..., changes=[...]`."""

    async def resolve(project):
        async def ask(card, message, patch=None):
            return kwargs

        return ask

    return resolve


async def _project(name="agent"):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=MembershipRole.Owner,
    )
    return project


async def _qualification(project, answers=None, card=None, status="completed"):
    return await Qualification.objects.acreate(
        project=project,
        questionnaire_id="",
        answers=answers or {},
        system_card=card if card is not None else CARD,
        status=status,
    )


class CardAgentTurnTest(TestCase):

    async def test_no_model_configured_answers_503(self):
        project = await _project()
        qualification = await _qualification(project)
        resp = await qual_client.post(
            f"/{project.pid}/qualification/{qualification.pid}/agent",
            json={"message": "what is this card about?"},
        )
        self.assertEqual(resp.status_code, 503)
        self.assertIn("no model is configured", resp.json()["detail"])

    async def test_a_valid_proposal_is_returned_with_the_card_it_would_make(self):
        project = await _project()
        qualification = await _qualification(project)
        reply = {
            "text": "I would name the model after what you said.",
            "changes": [{
                "node_id": "n1",
                "property": "label",
                "value": "Loan scoring model",
                "quote": "the model should be called the loan scoring model",
            }],
        }
        with patch.object(card_agent, "resolve_agent", _reply(**reply)):
            resp = await qual_client.post(
                f"/{project.pid}/qualification/{qualification.pid}/agent",
                json={"message": "the model should be called the loan scoring model"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["text"], "I would name the model after what you said.")
        self.assertEqual(body["proposals"], [{
            "node_id": "n1", "property": "label", "value": "Loan scoring model",
            "quote": "the model should be called the loan scoring model", "reason": "",
        }])
        self.assertEqual(body["dropped"], [])
        labels = {n["id"]: n["label"] for n in body["card"]["nodes"]}
        self.assertEqual(labels["n1"], "Loan scoring model")

    async def test_a_proposal_quoting_nothing_is_dropped(self):
        project = await _project("agent-quote")
        qualification = await _qualification(project)
        reply = {
            "text": "sure",
            "changes": [{"node_id": "n1", "property": "label", "value": "Something else",
                         "quote": "a sentence that is nowhere"}],
        }
        with patch.object(card_agent, "resolve_agent", _reply(**reply)):
            body = (await qual_client.post(
                f"/{project.pid}/qualification/{qualification.pid}/agent",
                json={"message": "rename the model please"},
            )).json()
        self.assertEqual(body["proposals"], [])
        self.assertEqual(
            body["dropped"][0]["reason"], "the quote is neither the person's words nor in the card"
        )
        # the preview is unchanged
        labels = {n["id"]: n["label"] for n in body["card"]["nodes"]}
        self.assertEqual(labels["n1"], "Credit scoring model")

    async def test_a_property_that_cannot_be_corrected_is_dropped(self):
        project = await _project("agent-property")
        qualification = await _qualification(project)
        reply = {
            "text": "",
            "changes": [{"node_id": "n1", "property": "destroy", "value": "x", "quote": "rename"}],
        }
        with patch.object(card_agent, "resolve_agent", _reply(**reply)):
            body = (await qual_client.post(
                f"/{project.pid}/qualification/{qualification.pid}/agent",
                json={"message": "rename it"},
            )).json()
        self.assertEqual(body["proposals"], [])
        self.assertIn("not correctable", body["dropped"][0]["reason"])

    async def test_a_change_proposed_twice_in_one_turn_is_dropped_once(self):
        project = await _project("agent-twice")
        qualification = await _qualification(project)
        change = {"node_id": "n1", "property": "label", "value": "A", "quote": "rename"}
        with patch.object(card_agent, "resolve_agent", _reply(text="", changes=[change, change])):
            body = (await qual_client.post(
                f"/{project.pid}/qualification/{qualification.pid}/agent",
                json={"message": "rename it"},
            )).json()
        self.assertEqual(len(body["proposals"]), 1)
        self.assertEqual(body["dropped"][0]["reason"], "proposed twice in one turn")

    async def test_the_turn_stores_nothing(self):
        project = await _project("agent-store")
        qualification = await _qualification(project)
        with patch.object(card_agent, "resolve_agent", _reply(text="hello", changes=[])):
            await qual_client.post(
                f"/{project.pid}/qualification/{qualification.pid}/agent",
                json={"message": "hello"},
            )
        self.assertEqual(await Qualification.objects.filter(project=project).acount(), 1)
        row = await Qualification.objects.filter(project=project).afirst()
        self.assertEqual(row.answers, {})

    async def test_a_draft_has_no_card_to_talk_about_and_an_empty_message_is_refused(self):
        project = await _project("agent-draft")
        draft = await _qualification(project, status="draft")
        resp = await qual_client.post(
            f"/{project.pid}/qualification/{draft.pid}/agent", json={"message": "hi"}
        )
        self.assertEqual(resp.status_code, 400)

        done = await _qualification(project, card=CARD)
        resp = await qual_client.post(
            f"/{project.pid}/qualification/{done.pid}/agent", json={"message": "  "}
        )
        self.assertEqual(resp.status_code, 400)

    async def test_a_stranger_gets_404(self):
        project = await Project.objects.acreate(
            name="agent-stranger", description="", status="Ready"
        )
        qualification = await _qualification(project)
        resp = await qual_client.post(
            f"/{project.pid}/qualification/{qualification.pid}/agent", json={"message": "hi"}
        )
        self.assertEqual(resp.status_code, 404)


class CardComponentLinkTest(TestCase):

    async def _engine(self, project, name="Risk model", data="risk.pkl", file_size=100,
                      component_type="model"):
        system = await AISystem.objects.acreate(project=project)
        return await AIComponent.objects.acreate(
            system=system, name=name, component_type=component_type,
            data=data, file_size=file_size,
        )

    async def _save(self, project, components):
        resp = await qual_client.post(
            f"/{project.pid}/qualification",
            json={"questionnaire_id": "", "answers": {"__blocks": {"components": components}}},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    async def test_a_save_records_the_link_and_the_snapshot(self):
        project = await _project("links")
        component = await self._engine(project)
        saved = await self._save(project, [
            {"key": "c1", "name": "Risk model", "type": "model", "role": "in-house"},
        ])
        rows = (await qual_client.get(
            f"/{project.pid}/qualification/{saved['pid']}/card-components"
        )).json()
        self.assertEqual([r["component_key"] for r in rows], ["c1"])
        self.assertEqual(rows[0]["engine_component_pid"], str(component.pid))
        self.assertEqual(rows[0]["snapshot"]["data"], "risk.pkl")
        self.assertFalse(rows[0]["changed"])
        self.assertFalse(rows[0]["missing"])

    async def test_a_component_the_engine_changed_since_is_flagged(self):
        project = await _project("links-changed")
        component = await self._engine(project)
        saved = await self._save(project, [{"key": "c1", "name": "Risk model"}])

        component.file_size = 999
        await component.asave()

        rows = (await qual_client.get(
            f"/{project.pid}/qualification/{saved['pid']}/card-components"
        )).json()
        self.assertTrue(rows[0]["changed"])

    async def test_a_component_the_engine_does_not_have_is_marked_missing(self):
        project = await _project("links-missing")
        saved = await self._save(project, [{"key": "c1", "name": "A model we never registered"}])
        rows = (await qual_client.get(
            f"/{project.pid}/qualification/{saved['pid']}/card-components"
        )).json()
        self.assertEqual(rows[0]["engine_component_pid"], "")
        self.assertTrue(rows[0]["missing"])

    async def test_the_card_component_key_survives_a_resave(self):
        project = await _project("links-key")
        await self._engine(project)
        await self._save(project, [{"key": "c1", "name": "Risk model", "role": "in-house"}])
        # each save is its own version, and it records its own link
        second = await self._save(project, [
            {"key": "c1", "name": "Risk model", "role": "third party"},
        ])
        rows = (await qual_client.get(
            f"/{project.pid}/qualification/{second['pid']}/card-components"
        )).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["component_key"], "c1")
        self.assertEqual(rows[0]["role"], "third party")

    async def test_a_component_the_card_drops_loses_its_link(self):
        project = await _project("links-dropped")
        await self._engine(project)
        await self._save(project, [
            {"key": "c1", "name": "Risk model"},
            {"key": "c2", "name": "Training data", "type": "dataset"},
        ])
        second = await self._save(
            project, [{"key": "c2", "name": "Training data", "type": "dataset"}]
        )
        rows = (await qual_client.get(
            f"/{project.pid}/qualification/{second['pid']}/card-components"
        )).json()
        self.assertEqual([r["component_key"] for r in rows], ["c2"])
