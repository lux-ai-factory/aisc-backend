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

from aisc_backend.models import EvaluationPlugin, Plugin, PluginConfig
from aisc_backend.models.artifact import Artifact
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
