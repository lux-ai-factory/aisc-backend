from django.test import TestCase, TransactionTestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import (
    AIComponent,
    AIComponentType,
    Evaluation,
    EvaluationPlugin,
    EvaluationStatus,
    Plugin,
    PluginConfig,
    ProjectConfig,
    ProjectConfigCategory,
)
from aisc_backend.models.ai_system import AISystem
from aisc_backend.routers.project import router as project_router
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.project import ProjectInSchema, ProjectOutSchema

project_repository = ProjectRepository()
client = TestAsyncClient(project_router)

class ProjectRouterTestCase(TestCase):

    async def test_get_projects(self):
        await project_repository.create("test")

        response = await client.get('')

        self.assertEqual(200, response.status_code)
        self.assertEqual(len(response.data), 1)

    async def test_create_project(self):
        data = ProjectInSchema()
        data.name = "test"

        response = await client.post('', json=data.dict())
        self.assertEqual(200, response.status_code)
        self.assertIsNotNone(response.data)

        project = ProjectOutSchema.model_validate(response.data)
        self.assertIsNotNone(project.pid)
        self.assertEqual(project.name, data.name)


class ProjectComponentRouterTestCase(TestCase):
    async def test_get_aisystem_get_or_creates(self):
        project = await project_repository.create("p")
        response = await client.get(f"/{project.pid}/aisystem")
        self.assertEqual(200, response.status_code)
        body = response.data
        self.assertEqual(body["name"], f"{project.name} system")
        self.assertEqual(body["components"], [])

    async def test_create_llm_component_requires_existing_secret(self):
        project = await project_repository.create("p")
        payload = {
            "name": "llm",
            "component_type": "llm",
            "json_value": {"endpoint_url": "http://example.com", "secret_key": "k"},
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
        self.assertEqual(body["component_type"], "llm")
        self.assertEqual(body["json_value"], payload["json_value"])

    async def test_evaluation_inputs_template_empty_and_prefilled(self):
        project = await project_repository.create("p")

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


# The uniqueness of project names is enforced by a DB constraint on Project.name.
# The API must turn the resulting IntegrityError into a 409, not a 500 (and not
# leak a 500). Exception handlers live on the NinjaAPI, not on a Router, so this
# exercises a real NinjaAPI with the production handlers registered. The
# project's own LoggingNinjaAPI is not used here because its create_response()
# reads ASGI scope, which the test client does not provide.
from ninja import NinjaAPI

from aisc_backend.routers.project import router as project_router
from aisc_backend.utils.exception_handlers import register_exception_handlers

uniqueness_api = NinjaAPI()
register_exception_handlers(uniqueness_api)
uniqueness_api.add_router("/projects", project_router)
uniqueness_client = TestAsyncClient(uniqueness_api)


class ProjectNameUniquenessTestCase(TransactionTestCase):
    async def test_duplicate_name_on_create_returns_409(self):
        await project_repository.create("dup")

        response = await uniqueness_client.post("/projects", json={"name": "dup"})

        self.assertEqual(409, response.status_code, response.content)

    async def test_duplicate_name_on_rename_returns_409(self):
        await project_repository.create("first")
        other = await project_repository.create("second")

        response = await uniqueness_client.patch(
            f"/projects/{other.pid}", json={"name": "first"}
        )

        self.assertEqual(409, response.status_code, response.content)

