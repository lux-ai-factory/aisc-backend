from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import AIComponent, AIComponentType, Project, ProjectConfig, ProjectConfigCategory
from aisc_evaluation.models import Evaluation, EvaluationPlugin, EvaluationStatus
from aisc_plugins.models import Plugin, PluginConfig
from aisc_backend.models.ai_system import AISystem
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.project import router as project_router
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.project import ProjectInSchema, ProjectOutSchema

project_repository = ProjectRepository()


async def owned_project():
    project = await project_repository.create("p")
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=MembershipRole.Owner
    )
    return project
client = TestAsyncClient(project_router)

class ProjectRouterTestCase(TestCase):

    async def test_get_projects(self):
        # A project list is membership-scoped; give the (dev-bypass) caller a
        # membership so the project is visible.
        project = await project_repository.create("test")
        await ProjectMember.objects.acreate(
            project=project, subject="development", role=MembershipRole.Owner
        )

        response = await client.get('')

        self.assertEqual(200, response.status_code)
        self.assertEqual(len(response.data), 1)

    async def test_create_project_makes_caller_owner(self):
        data = ProjectInSchema()
        data.name = "test"

        response = await client.post('', json=data.dict())
        self.assertEqual(200, response.status_code)
        self.assertIsNotNone(response.data)

        project = ProjectOutSchema.model_validate(response.data)
        self.assertIsNotNone(project.pid)
        self.assertEqual(project.name, data.name)
        # The person who starts a project owns it.
        owner = await ProjectMember.objects.filter(
            project__pid=project.pid, role=MembershipRole.Owner
        ).afirst()
        self.assertIsNotNone(owner)

    async def test_owner_write_failure_rolls_back_project(self):
        import unittest.mock as mock

        data = ProjectInSchema()
        data.name = "atomic"
        with mock.patch(
            "aisc_backend.routers.project.ProjectMember.objects"
        ) as member_objects:
            member_objects.create.side_effect = RuntimeError("boom")
            # the test client re-raises view exceptions; the DB write is what matters
            with self.assertRaises(RuntimeError):
                await client.post('', json=data.dict())
        # the project must not be left behind ownerless
        self.assertEqual(await Project.objects.filter(name="atomic").acount(), 0)


class ProjectMembershipRouterTestCase(TestCase):

    async def _owned_project(self, subject="development"):
        project = await project_repository.create("owned-by-caller")
        await ProjectMember.objects.acreate(
            project=project, subject=subject, role=MembershipRole.Owner
        )
        return project

    async def test_owner_can_add_and_remove_members(self):
        project = await self._owned_project()
        resp = await client.post(f"/{project.pid}/members", json={
            "subject": "alice", "role": "viewer",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["role"], "viewer")
        resp = await client.get(f"/{project.pid}/members")
        self.assertEqual(resp.status_code, 200)
        subjects = {m["subject"] for m in resp.json()}
        self.assertIn("development", subjects)
        self.assertIn("alice", subjects)
        resp = await client.delete(f"/{project.pid}/members/alice")
        self.assertEqual(resp.status_code, 204)
        resp = await client.get(f"/{project.pid}/members")
        self.assertNotIn("alice", {m["subject"] for m in resp.json()})

    async def test_non_member_gets_404_for_project(self):
        project = await project_repository.create("stranger-project")
        resp = await client.get(f"/{project.pid}")
        self.assertEqual(resp.status_code, 404)
        resp = await client.get(f"/{project.pid}/members")
        self.assertEqual(resp.status_code, 404)

    async def test_last_owner_cannot_be_removed_or_demoted(self):
        project = await self._owned_project()
        await ProjectMember.objects.acreate(
            project=project, subject="alice", role=MembershipRole.Editor
        )
        resp = await client.delete(f"/{project.pid}/members/development")
        self.assertEqual(resp.status_code, 409)
        resp = await client.put(f"/{project.pid}/members/development", json={"role": "editor"})
        self.assertEqual(resp.status_code, 409)

    async def test_editor_cannot_manage_members(self):
        project = await project_repository.create("editor-project")
        await ProjectMember.objects.acreate(
            project=project, subject="development", role=MembershipRole.Editor
        )
        resp = await client.post(f"/{project.pid}/members", json={
            "subject": "bob", "role": "viewer",
        })
        self.assertEqual(resp.status_code, 403)

    async def test_invalid_role_is_422_not_500(self):
        project = await self._owned_project()
        resp = await client.post(f"/{project.pid}/members", json={
            "subject": "bob", "role": "superuser",
        })
        self.assertEqual(resp.status_code, 422)
        resp = await client.put(
            f"/{project.pid}/members/alice", json={"role": "superuser"}
        )
        self.assertEqual(resp.status_code, 422)

    async def test_memberless_projects_only_visible_to_admin(self):
        # A repository-created project with no members must not leak to another
        # user's list; the dev caller sees only their own projects.
        await project_repository.create("no-members")
        mine = await self._owned_project()
        resp = await client.get("")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual({p["pid"] for p in resp.json()}, {str(mine.pid)})


class ProjectComponentRouterTestCase(TestCase):
    async def test_get_aisystem_get_or_creates(self):
        project = await owned_project()
        response = await client.get(f"/{project.pid}/aisystem")
        self.assertEqual(200, response.status_code)
        body = response.data
        self.assertEqual(body["name"], f"{project.name} system")
        self.assertEqual(body["components"], [])

    async def test_create_endpoint_component_requires_existing_secret_reference(self):
        project = await owned_project()
        payload = {
            "name": "llm",
            "component_type": "endpoint",
            "protocol": "openai-chat",
            "adapter": "openai",
            "url": "http://example.com",
            "secret_key": "k",
        }
        response = await client.post(f"/{project.pid}/components", json=payload)
        self.assertEqual(400, response.status_code)

        await ProjectConfig.objects.acreate(
            project=project, name="Key", key="k",
            category=ProjectConfigCategory.SECRETS, encrypted_value="cipher",
        )
        response = await client.post(f"/{project.pid}/components", json=payload)
        self.assertEqual(200, response.status_code, response.text)
        body = response.data
        self.assertEqual(body["component_type"], "endpoint")
        self.assertEqual(body["secret_key"], "k")

    async def test_create_endpoint_component_stores_a_typed_secret(self):
        project = await owned_project()
        payload = {
            "name": "llm",
            "component_type": "endpoint",
            "protocol": "openai-chat",
            "adapter": "openai",
            "url": "http://example.com",
            "secret": "sk-typed",
        }
        response = await client.post(f"/{project.pid}/components", json=payload)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(response.data["has_secret"])
        # the key value is never read back
        self.assertNotIn("sk-typed", response.text)

    async def test_evaluation_inputs_template_empty_and_prefilled(self):
        project = await owned_project()

        response = await client.get(f"/{project.pid}/evaluation-inputs-template")
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.data, {})

        system = await AISystem.objects.acreate(project=project, name="sys", description="")
        dataset = await AIComponent.objects.acreate(
            name="ds", description="", data="ds.csv",
            component_type=AIComponentType.DATASET, system=system,
        )
        plugin = await Plugin.objects.acreate(
            name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
            version="0.1.0", project=project,
        )
        plugin_config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        evaluation = await Evaluation.objects.acreate(status=EvaluationStatus.Pending, project=project, task=None)
        evaluation_plugin = await EvaluationPlugin.objects.acreate(evaluation=evaluation, plugin_config=plugin_config)
        await evaluation_plugin.evaluation_inputs.acreate(name="ds", component=dataset, value={})

        response = await client.get(f"/{project.pid}/evaluation-inputs-template")
        self.assertEqual(200, response.status_code, response.text)
        entries = response.data["DemoPlugin"]["ds"]
        self.assertEqual(entries["component_pid"], str(dataset.pid))
