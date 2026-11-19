"""
Tests for the qualification backend: AI-card versioning (the platform
contract), questionnaire management + per-project selection, and the
qualification orchestration (answers in -> card out -> version + audit).

The headless service is stubbed: `build_card` is patched so these tests run
hermetically (no running service, no network). Auth is the dev-bypass mode, so
`caller_of` returns the development caller (subject "development").
"""
import unittest.mock as mock

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.models.system import SystemVersion
from aisc_qualification.models import Qualification, Questionnaire
from aisc_backend.routers.qualification import router as qualification_router
from aisc_backend.routers.questionnaire import router as questionnaire_router

qualification_client = TestAsyncClient(qualification_router)
questionnaire_client = TestAsyncClient(questionnaire_router)


async def _project():
    project = await Project.objects.acreate(name="qual-project", description="", status="Ready")
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=MembershipRole.Owner
    )
    return project


# --- AI-card versioning (the platform contract) ---------------------------------


class SystemVersionsTest(TestCase):

    async def test_create_and_read_versions(self):
        project = await _project()
        resp = await qualification_client.post(
            f"/{project.pid}/system-versions",
            json={"name": "Credit scorer", "version": "1.0"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["number"], 1)
        self.assertEqual(resp.json()["project_id"], str(project.pid))

        resp = await qualification_client.get(f"/{project.pid}/system-versions/latest")
        self.assertEqual(resp.json()["number"], 1)

        # next save is version 2
        await qualification_client.post(
            f"/{project.pid}/system-versions", json={"name": "Credit scorer v2"}
        )
        versions = (await qualification_client.get(
            f"/{project.pid}/system-versions")).json()
        self.assertEqual([v["number"] for v in versions], [2, 1])

    async def test_latest_is_null_when_no_versions(self):
        project = await _project()
        resp = await qualification_client.get(f"/{project.pid}/system-versions/latest")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json())


# --- Questionnaire management + selection ---------------------------------------


class QuestionnaireTest(TestCase):

    async def test_crud(self):
        resp = await questionnaire_client.post("", json={
            "name": "Annex IV", "definition": {"blocks": [{"id": "b1"}]},
        })
        self.assertEqual(resp.status_code, 200)
        qid = resp.json()["pid"]
        self.assertEqual(resp.json()["name"], "Annex IV")

        resp = await questionnaire_client.get("")
        self.assertEqual(len(resp.json()), 1)

        # creation seeds version 1
        versions = (await questionnaire_client.get(f"/{qid}/versions")).json()
        self.assertEqual([v["number"] for v in versions], [1])
        self.assertEqual(versions[0]["definition"], {"blocks": [{"id": "b1"}]})

        resp = await questionnaire_client.put(f"/{qid}", json={
            "name": "Annex IV (2026)", "definition": {"blocks": []},
        })
        self.assertEqual(resp.json()["name"], "Annex IV (2026)")

        # every edit appends a version; the head is the latest definition
        versions = (await questionnaire_client.get(f"/{qid}/versions")).json()
        self.assertEqual([v["number"] for v in versions], [1, 2])
        self.assertEqual(versions[1]["definition"], {"blocks": []})
        old = await questionnaire_client.get(f"/{qid}/versions/1")
        self.assertEqual(old.json()["definition"], {"blocks": [{"id": "b1"}]})
        missing = await questionnaire_client.get(f"/{qid}/versions/9")
        self.assertEqual(missing.status_code, 404)

        resp = await questionnaire_client.delete(f"/{qid}")
        self.assertEqual(resp.status_code, 204)

    async def test_select_and_read_project_questionnaire(self):
        project = await _project()
        created = (await questionnaire_client.post("", json={
            "name": "Annex IV", "definition": {},
        })).json()

        # selection is a project property -> the qualification router serves it
        resp = await qualification_client.put(
            f"/{project.pid}/questionnaire",
            json={"questionnaire_id": created["pid"]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["questionnaire_id"], created["pid"])

        resp = await qualification_client.get(f"/{project.pid}/questionnaire")
        self.assertEqual(resp.json()["questionnaire_id"], created["pid"])


# --- Qualification orchestration (service stubbed) ------------------------------


class QualificationOrchestrationTest(TestCase):

    async def test_save_stores_draft_and_complete_versions(self):
        project = await _project()
        questionnaire = await Questionnaire.objects.acreate(
            name="Annex IV", definition={"blocks": [{"id": "b1"}]}, project=None,
        )
        with mock.patch(
            "aisc_backend.services.qualification_service.build_card",
            new=mock.AsyncMock(return_value={"x": 1}),
        ) as build:
            resp = await qualification_client.post(
                f"/{project.pid}/qualification",
                json={"questionnaire_id": str(questionnaire.pid), "name": "My system",
                      "answers": {"q1": "yes"}},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["questionnaire_id"], str(questionnaire.pid))
        self.assertEqual(body["status"], "draft")
        self.assertIsNone(body["system_card"])
        self.assertIsNone(body["system_version"])
        # saving builds nothing and bumps no version
        build.assert_not_awaited()
        self.assertEqual(await SystemVersion.objects.acount(), 0)

        card = {"format": "airo-min", "card": {"summary": {"label": "L"}}}
        with mock.patch(
            "aisc_backend.services.qualification_service.build_card",
            new=mock.AsyncMock(return_value=card),
        ) as build:
            resp = await qualification_client.post(
                f"/{project.pid}/qualification/{body['pid']}/complete",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["system_card"]["format"], "airo-min")
        self.assertIsNotNone(body["system_version"])
        # the service was told which questionnaire the answers are against
        build.assert_awaited_once()
        args = build.await_args.args
        self.assertEqual(args[1], str(questionnaire.pid))   # questionnaire_id
        self.assertEqual(args[2], {"blocks": [{"id": "b1"}]})  # questionnaire definition
        self.assertEqual(args[3], {"q1": "yes"})  # answers
        self.assertEqual(build.await_args.kwargs["system"]["name"], project.name)

        saved = await Qualification.objects.aget(pid=body["pid"])
        self.assertEqual(saved.answers, {"q1": "yes"})
        self.assertEqual(saved.system_card, card)
        self.assertEqual(saved.status, "completed")
        version = await SystemVersion.objects.aget(pk=saved.system_version_id)
        self.assertEqual(version.number, 1)

        # completing twice is refused: versions bump exactly once
        again = await qualification_client.post(
            f"/{project.pid}/qualification/{body['pid']}/complete",
        )
        self.assertEqual(again.status_code, 400)
        self.assertEqual(await SystemVersion.objects.acount(), 1)

    async def test_list_and_get_qualifications(self):
        project = await _project()
        created = (await qualification_client.post(
            f"/{project.pid}/qualification",
            json={"questionnaire_id": "q-1", "answers": {"a": 1}},
        )).json()

        listing = (await qualification_client.get(
            f"/{project.pid}/qualification")).json()
        self.assertEqual(len(listing), 1)

        detail = (await qualification_client.get(
            f"/{project.pid}/qualification/{created['pid']}")).json()
        self.assertEqual(detail["pid"], created["pid"])

    async def test_stranger_cannot_write(self):
        project = await Project.objects.acreate(name="x", description="", status="Ready")
        # no member rows -> the dev caller has no membership
        resp = await qualification_client.post(
            f"/{project.pid}/qualification",
            json={"questionnaire_id": "q", "answers": {}},
        )
        self.assertEqual(resp.status_code, 404)


class QuestionnaireScopingTest(TestCase):

    async def test_defaults_and_project_questionnaires(self):
        project_a = await _project()
        project_b = await Project.objects.acreate(name="b", description="", status="Ready")
        await ProjectMember.objects.acreate(
            project=project_b, subject="development", role=MembershipRole.Owner,
        )

        # a default (project NULL) is visible to every project; a project-owned
        # one is visible only in its own project
        default = (await questionnaire_client.post("", json={
            "name": "Default", "definition": {"kind": "default"},
        })).json()
        owned = (await questionnaire_client.post(
            f"/projects/{project_a.pid}", json={
                "name": "Project A form", "definition": {"kind": "project"},
            })).json()

        a_list = (await questionnaire_client.get(
            f"/projects/{project_a.pid}")).json()
        ids = {q["pid"] for q in a_list}
        self.assertIn(default["pid"], ids)
        self.assertIn(owned["pid"], ids)

        b_list = (await questionnaire_client.get(
            f"/projects/{project_b.pid}")).json()
        b_ids = {q["pid"] for q in b_list}
        self.assertIn(default["pid"], b_ids)
        self.assertNotIn(owned["pid"], b_ids)

    async def test_save_uses_project_owned_questionnaire(self):
        project = await _project()
        owned = (await questionnaire_client.post(
            f"/projects/{project.pid}", json={
                "name": "Own form", "definition": {"kind": "project"},
            })).json()
        draft = (await qualification_client.post(
            f"/{project.pid}/qualification",
            json={"questionnaire_id": owned["pid"], "answers": {"a": 1}},
        )).json()
        with mock.patch(
            "aisc_backend.services.qualification_service.build_card",
            new=mock.AsyncMock(return_value={"format": "airo-min"}),
        ) as build:
            resp = await qualification_client.post(
                f"/{project.pid}/qualification/{draft['pid']}/complete",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        build.assert_awaited_once()
        self.assertEqual(build.await_args.args[1], owned["pid"])
        self.assertEqual(build.await_args.args[2], {"kind": "project"})

    async def test_project_questionnaire_versions_are_scoped(self):
        project = await _project()
        owned = (await questionnaire_client.post(
            f"/projects/{project.pid}", json={
                "name": "Own form", "definition": {"v": 1},
            })).json()
        await questionnaire_client.put(
            f"/projects/{project.pid}/{owned['pid']}",
            json={"name": "Own form", "definition": {"v": 2}},
        )
        versions = (await questionnaire_client.get(
            f"/projects/{project.pid}/{owned['pid']}/versions")).json()
        self.assertEqual([v["number"] for v in versions], [1, 2])
        one = await questionnaire_client.get(
            f"/projects/{project.pid}/{owned['pid']}/versions/1")
        self.assertEqual(one.json()["definition"], {"v": 1})

    async def test_card_edit_reversions_and_versions_chain(self):
        project = await _project()
        draft = (await qualification_client.post(
            f"/{project.pid}/qualification",
            json={"questionnaire_id": "", "answers": {"a": 1}},
        )).json()
        # a draft cannot be re-versioned
        resp = await qualification_client.put(
            f"/{project.pid}/qualification/{draft['pid']}/card",
            json={"system_card": {"edited": True}},
        )
        self.assertEqual(resp.status_code, 400)

        with mock.patch(
            "aisc_backend.services.qualification_service.build_card",
            new=mock.AsyncMock(return_value={"card": "v1"}),
        ):
            completed = (await qualification_client.post(
                f"/{project.pid}/qualification/{draft['pid']}/complete",
            )).json()
        self.assertEqual(completed["system_version"]["number"], 1)

        resp = await qualification_client.put(
            f"/{project.pid}/qualification/{completed['pid']}/card",
            json={"system_card": {"card": "edited"}},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        edited = resp.json()
        self.assertEqual(edited["system_card"], {"card": "edited"})
        self.assertEqual(edited["system_version"]["number"], 2)
        self.assertNotEqual(edited["pid"], completed["pid"])

        # the completed row is untouched
        old = await qualification_client.get(
            f"/{project.pid}/qualification/{completed['pid']}")
        self.assertEqual(old.json()["system_card"], {"card": "v1"})
        self.assertEqual(old.json()["system_version"]["number"], 1)

        chain = (await qualification_client.get(
            f"/{project.pid}/qualification/{completed['pid']}/versions")).json()
        self.assertEqual(
            [(q["status"], (q["system_version"] or {}).get("number")) for q in chain],
            [("completed", 1), ("completed", 2)],
        )

    async def test_stranger_cannot_complete_or_edit(self):
        project = await Project.objects.acreate(name="y", description="", status="Ready")
        resp = await qualification_client.post(
            f"/{project.pid}/qualification/00000000-0000-0000-0000-000000000000/complete",
        )
        self.assertEqual(resp.status_code, 404)
        resp = await qualification_client.put(
            f"/{project.pid}/qualification/00000000-0000-0000-0000-000000000000/card",
            json={"system_card": {}},
        )
        self.assertEqual(resp.status_code, 404)


class ServiceDefaultQuestionnaireTest(TestCase):

    async def test_default_endpoint_serves_the_service_shape(self):
        doc = {
            "questionnaireId": "annex-iv-default",
            "questionnaireName": "Annex IV default",
            "description": "Annex IV",
            "versionNumber": 1,
            "questions": [],
        }
        with mock.patch(
            "aisc_backend.services.qualification_service.get_default_questionnaire",
            new=mock.AsyncMock(return_value=doc),
        ):
            resp = await questionnaire_client.get("/default")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["id"], "annex-iv-default")
        self.assertEqual(body["name"], "Annex IV default")
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["definition"], doc)

    async def test_default_endpoint_is_404_without_a_service(self):
        with mock.patch(
            "aisc_backend.services.qualification_service.get_default_questionnaire",
            new=mock.AsyncMock(return_value={}),
        ):
            resp = await questionnaire_client.get("/default")
        self.assertEqual(resp.status_code, 404)

    async def test_default_endpoint_is_502_when_the_service_refuses(self):
        from aisc_backend.services.qualification_service import QualificationServiceError

        with mock.patch(
            "aisc_backend.services.qualification_service.get_default_questionnaire",
            new=mock.AsyncMock(side_effect=QualificationServiceError("refused")),
        ):
            resp = await questionnaire_client.get("/default")
        self.assertEqual(resp.status_code, 502)

    async def test_complete_without_questionnaire_uses_service_default(self):
        project = await _project()
        draft = (await qualification_client.post(
            f"/{project.pid}/qualification",
            json={"questionnaire_id": "", "answers": {"a": 1}},
        )).json()
        definition = {"questionnaireId": "annex-iv-default", "questions": []}
        with (
            mock.patch(
                "aisc_backend.services.qualification_service.get_default_questionnaire",
                new=mock.AsyncMock(return_value=definition),
            ),
            mock.patch(
                "aisc_backend.services.qualification_service.build_card",
                new=mock.AsyncMock(return_value={"format": "airo-min"}),
            ) as build,
        ):
            resp = await qualification_client.post(
                f"/{project.pid}/qualification/{draft['pid']}/complete",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(build.await_args.args[2], definition)

    async def test_completed_can_be_reopened_as_draft(self):
        project = await _project()
        draft = (await qualification_client.post(
            f"/{project.pid}/qualification",
            json={"questionnaire_id": "", "answers": {"a": 1}},
        )).json()
        with mock.patch(
            "aisc_backend.services.qualification_service.build_card",
            new=mock.AsyncMock(return_value={"card": "v1"}),
        ):
            completed = (await qualification_client.post(
                f"/{project.pid}/qualification/{draft['pid']}/complete",
            )).json()
        self.assertEqual(completed["status"], "completed")

        resp = await qualification_client.post(
            f"/{project.pid}/qualification/{completed['pid']}/reopen",
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "draft")

        # reopening a draft is refused
        again = await qualification_client.post(
            f"/{project.pid}/qualification/{completed['pid']}/reopen",
        )
        self.assertEqual(again.status_code, 400)


class QualificationVocabulariesTest(TestCase):
    async def test_vocabularies_proxied(self):
        project = await _project()
        with mock.patch(
            "aisc_backend.services.qualification_service.get_vocabularies",
            new=mock.AsyncMock(return_value={"AISubject": ["Children"]}),
        ):
            resp = await qualification_client.get(
                f"/{project.pid}/qualification/vocabularies",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {"AISubject": ["Children"]})

    async def test_vocabularies_empty_without_a_service(self):
        project = await _project()
        with mock.patch(
            "aisc_backend.services.qualification_service.get_vocabularies",
            new=mock.AsyncMock(return_value={}),
        ):
            resp = await qualification_client.get(
                f"/{project.pid}/qualification/vocabularies",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {})

    async def test_stranger_cannot_read_vocabularies(self):
        project = await Project.objects.acreate(name="y", description="", status="Ready")
        resp = await qualification_client.get(
            f"/{project.pid}/qualification/vocabularies",
        )
        self.assertEqual(resp.status_code, 404)


_RICH_CARD = {
    "view": {"system": {"id": "system", "label": "S"}},
    "jsonld": "{}",
    "turtle": "",
    "problems": [],
    "digest": "d",
}


async def _completed(project, card=None):
    draft = (await qualification_client.post(
        f"/{project.pid}/qualification",
        json={"questionnaire_id": "", "answers": {"q:annex-1:1a": "x"}},
    )).json()
    with mock.patch(
        "aisc_backend.services.qualification_service.build_card",
        new=mock.AsyncMock(return_value=card or _RICH_CARD),
    ):
        completed = (await qualification_client.post(
            f"/{project.pid}/qualification/{draft['pid']}/complete",
        )).json()
    return completed


class QualificationNodePatchTest(TestCase):

    async def test_patch_stores_and_rebuilds_as_new_version(self):
        project = await _project()
        completed = await _completed(project)
        with mock.patch(
            "aisc_backend.services.qualification_service.build_card",
            new=mock.AsyncMock(return_value=_RICH_CARD),
        ) as build:
            resp = await qualification_client.put(
                f"/{project.pid}/qualification/{completed['pid']}/nodes/risk0",
                json={"label": "Fixed risk"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        edited = resp.json()
        self.assertNotEqual(edited["pid"], completed["pid"])
        self.assertEqual(edited["system_version"]["number"], 2)
        saved = edited["answers"]
        self.assertEqual(saved["__patch"], {"risk0": {"label": "Fixed risk"}})
        rebuilt = build.await_args.args[3]
        self.assertEqual(rebuilt["__patch"], {"risk0": {"label": "Fixed risk"}})
        self.assertEqual(rebuilt["q:annex-1:1a"], "x")
        chain = (await qualification_client.get(
            f"/{project.pid}/qualification/{completed['pid']}/versions")).json()
        self.assertEqual(len(chain), 2)

    async def test_patch_merges_per_node_and_empty_removes(self):
        project = await _project()
        completed = await _completed(project)
        with mock.patch(
            "aisc_backend.services.qualification_service.build_card",
            new=mock.AsyncMock(return_value=_RICH_CARD),
        ):
            first = (await qualification_client.put(
                f"/{project.pid}/qualification/{completed['pid']}/nodes/risk0",
                json={"label": "Fixed risk"},
            )).json()
            second = (await qualification_client.put(
                f"/{project.pid}/qualification/{first['pid']}/nodes/risk0",
                json={"note": "checked"},
            )).json()
            self.assertEqual(
                second["answers"]["__patch"],
                {"risk0": {"label": "Fixed risk", "note": "checked"}},
            )
            third = (await qualification_client.put(
                f"/{project.pid}/qualification/{second['pid']}/nodes/risk0",
                json={},
            )).json()
            self.assertNotIn("__patch", third["answers"])

    async def test_patch_refuses_unknown_keys_and_empty_label(self):
        project = await _project()
        completed = await _completed(project)
        resp = await qualification_client.put(
            f"/{project.pid}/qualification/{completed['pid']}/nodes/risk0",
            json={"color": "red"},
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        resp = await qualification_client.put(
            f"/{project.pid}/qualification/{completed['pid']}/nodes/risk0",
            json={"label": "  "},
        )
        self.assertEqual(resp.status_code, 400, resp.text)

    async def test_patch_refused_when_ontology_refuses(self):
        project = await _project()
        completed = await _completed(project)
        stub = {"view": {"summary": {"label": "stub"}}, "jsonld": "{}"}
        with mock.patch(
            "aisc_backend.services.qualification_service.build_card",
            new=mock.AsyncMock(return_value=stub),
        ):
            resp = await qualification_client.put(
                f"/{project.pid}/qualification/{completed['pid']}/nodes/risk0",
                json={"label": "Fixed risk"},
            )
        self.assertEqual(resp.status_code, 422, resp.text)
        chain = (await qualification_client.get(
            f"/{project.pid}/qualification/{completed['pid']}/versions")).json()
        self.assertEqual(len(chain), 1)

    async def test_patch_requires_completed_and_member(self):
        project = await _project()
        draft = (await qualification_client.post(
            f"/{project.pid}/qualification",
            json={"questionnaire_id": "", "answers": {"a": 1}},
        )).json()
        resp = await qualification_client.put(
            f"/{project.pid}/qualification/{draft['pid']}/nodes/risk0",
            json={"label": "x"},
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        stranger = await Project.objects.acreate(name="y", description="", status="Ready")
        resp = await qualification_client.put(
            f"/{stranger.pid}/qualification/{draft['pid']}/nodes/risk0",
            json={"label": "x"},
        )
        self.assertEqual(resp.status_code, 404)


class QuestionnaireVersionDefinitionTest(TestCase):
    async def test_only_a_definition_change_makes_a_version(self):
        project = await _project()
        owned = (
            await questionnaire_client.post(
                f"/projects/{project.pid}",
                json={
                    "name": "Form",
                    "definition": {"q": 1},
                },
            )
        ).json()
        path = f"/projects/{project.pid}/{owned['pid']}"
        await questionnaire_client.put(path, json={"name": "Form", "definition": {"q": 1}})
        await questionnaire_client.put(
            path, json={"name": "Renamed", "definition": {"q": 1}}
        )
        versions = (await questionnaire_client.get(f"{path}/versions")).json()
        self.assertEqual([v["number"] for v in versions], [1])
        await questionnaire_client.put(path, json={"name": "Renamed", "definition": {"q": 2}})
        versions = (await questionnaire_client.get(f"{path}/versions")).json()
        self.assertEqual([v["number"] for v in versions], [1, 2])


class QualificationQuestionnaireVersionTest(TestCase):
    async def test_answers_record_the_questionnaire_version(self):
        project = await _project()
        owned = (
            await questionnaire_client.post(
                f"/projects/{project.pid}", json={"name": "Form", "definition": {"q": 1}}
            )
        ).json()
        path = f"/projects/{project.pid}/{owned['pid']}"
        saved = (
            await qualification_client.post(
                f"/{project.pid}/qualification",
                json={"questionnaire_id": owned["pid"], "answers": {"q:a:1": "x"}},
            )
        ).json()
        self.assertEqual(saved["questionnaire_version"], 1)
        await questionnaire_client.put(path, json={"name": "Form", "definition": {"q": 2}})
        later = (
            await qualification_client.post(
                f"/{project.pid}/qualification",
                json={"questionnaire_id": owned["pid"], "answers": {"q:a:1": "y"}},
            )
        ).json()
        self.assertEqual(later["questionnaire_version"], 2)
        earlier = (
            await qualification_client.post(
                f"/{project.pid}/qualification",
                json={
                    "questionnaire_id": owned["pid"],
                    "answers": {"q:a:1": "z"},
                    "questionnaire_version": 1,
                },
            )
        ).json()
        self.assertEqual(earlier["questionnaire_version"], 1)
