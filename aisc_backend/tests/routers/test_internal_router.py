"""
Regression tests for the internal router.

`upload_evaluation_artifact` derives the stored object's extension with
`Path(file.name).suffix`, which requires `pathlib.Path`. Importing ninja's
`Path` (the path-parameter helper) into this module shadows it and makes every
artifact upload fail with `'Path' object has no attribute 'suffix'`. The eval
worker does not check the response status, so such a failure is silent and the
artifact is lost while the evaluation still reports success.
"""
import os
import pathlib
import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import EvaluationPlugin, Plugin, PluginConfig, EvaluationInput, AIComponent, AIComponentType, ProjectConfig, ProjectConfigCategory
from aisc_backend.models.artifact import Artifact
from aisc_backend.models.ai_system import AISystem
from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers import internal
from aisc_backend.routers.internal import router as internal_router

client = TestAsyncClient(internal_router)

# The internal router is gated by InternalSharedKeyAuth, which reads this at
# request time; callers must present it as X-Internal-Secret.
INTERNAL_SECRET = "test-internal-secret"


class InternalRouterPathImportTestCase(TestCase):
    def test_path_is_pathlib_not_ninja(self):
        """ninja's `Path` has no `.suffix`; only pathlib's does."""
        self.assertIs(internal.Path, pathlib.Path)


class UploadEvaluationArtifactTestCase(TestCase):
    def setUp(self):
        project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        self.evaluation = Evaluation.objects.create(
            status=EvaluationStatus.Pending, project=project
        )
        plugin = Plugin.objects.create(
            name="DemoPlugin",
            display_name="Demo",
            package_name="demo-pkg",
            version="0.1.0",
            project=project,
        )
        plugin_config = PluginConfig.objects.create(plugin=plugin, config={})
        self.evaluation_plugin = EvaluationPlugin.objects.create(
            evaluation=self.evaluation, plugin_config=plugin_config
        )

    async def test_upload_artifact_stores_file_with_original_suffix(self):
        upload = SimpleUploadedFile(
            "plugin_execution.log", b"log body", content_type="text/plain"
        )

        with (
            patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}),
            patch.object(internal.file_repository, "upload_file", return_value=True),
        ):
            response = await client.post(
                f"/evaluations/{self.evaluation.pid}/artifacts",
                data={"evaluation_plugin_uuid": str(self.evaluation_plugin.pid)},
                FILES={"file": upload},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
            )

        self.assertEqual(200, response.status_code)

        # Stored under a fresh UUID that keeps the original extension.
        stored_name = response.data["file_name"]
        self.assertTrue(stored_name.endswith(".log"))
        uuid.UUID(stored_name.removesuffix(".log"))

        # And the Artifact row keeps the human-readable original name.
        artifact = await Artifact.objects.aget(data=stored_name)
        self.assertEqual("plugin_execution.log", artifact.name)


class InternalProjectConfigsTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)

    async def test_get_project_configs_typed(self):
        await ProjectConfig.objects.acreate(
            project=self.project,
            name="Max tokens",
            key="max_tokens",
            category=ProjectConfigCategory.VARIABLES,
            json_value={"type": "number", "value": 42},
        )
        with patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}):
            response = await client.get(
                f"/projects/settings/{self.project.pid}",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
            )
        self.assertEqual(200, response.status_code)
        body = response.data
        self.assertEqual(len(body), 1)
        self.assertIn("encrypted_value", body[0])
        self.assertEqual(body[0]["json_value"], {"type": "number", "value": 42})


class InternalEvaluationInputsTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)
        self.system = AISystem.objects.create(project=self.project, name="sys", description="")
        self.llm = AIComponent.objects.create(
            name="llm", description="", data="",
            component_type=AIComponentType.LLM, system=self.system,
            json_value={"endpoint_url": "http://example.com", "secret_key": "k"},
        )
        ProjectConfig.objects.create(
            project=self.project, name="Key", key="k",
            category=ProjectConfigCategory.SECRETS, encrypted_value="cipher",
        )
        self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=self.project)
        self.plugin = Plugin.objects.create(
            name="DemoPlugin", display_name="Demo",
            package_name="demo-pkg", version="0.1.0", project=self.project,
        )
        self.plugin_config = PluginConfig.objects.create(plugin=self.plugin, config={})
        self.evaluation_plugin = EvaluationPlugin.objects.create(
            evaluation=self.evaluation, plugin_config=self.plugin_config
        )
        self.evaluation_input = EvaluationInput.objects.create(
            evaluation_plugin=self.evaluation_plugin, name="llm", component=self.llm, value={}
        )

    async def test_get_evaluation_inputs_returns_typed_entries(self):
        with patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}):
            response = await client.get(
                f"/evaluations/{self.evaluation.pid}/inputs",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
            )
        self.assertEqual(200, response.status_code)
        body = response.data
        entry = body[str(self.evaluation_plugin.pid)][0]
        self.assertEqual(entry["name"], "llm")
        self.assertEqual(entry["component_type"], "llm")
        self.assertEqual(entry["endpoint_url"], "http://example.com")
        self.assertEqual(entry["secret_key"], "k")
        self.assertEqual(entry["secret_encrypted_value"], "cipher")
