"""A plugin's artifact has a size cap, and the run page's artifact list always answers.

An artifact above the cap is refused before it is stored. In the list, a preview reads only the head of a
file, a csv that is not UTF-8 is shown with replacement characters, and one artifact that cannot be read
gives no preview while the others still show."""
import os
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import EvaluationPlugin, Plugin, PluginConfig
from aisc_backend.models.artifact import Artifact
from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers import evaluation as evaluation_routes
from aisc_backend.routers import internal
from aisc_backend.routers.evaluation import router as evaluation_router
from aisc_backend.routers.internal import router as internal_router

internal_client = TestAsyncClient(internal_router)
public_client = TestAsyncClient(evaluation_router)
INTERNAL_SECRET = "fake-internal-secret"


class _Body:
    def __init__(self, data: bytes, reads: list):
        self.data, self.reads = data, reads

    def read(self, amt=None):
        self.reads.append(amt)
        return self.data if amt is None or amt < 0 else self.data[:amt]


class _RunWithArtifacts(TestCase):
    def setUp(self):
        project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=project)
        plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
                                       version="0.1.0", project=project)
        config = PluginConfig.objects.create(plugin=plugin, config={})
        self.evaluation_plugin = EvaluationPlugin.objects.create(evaluation=self.evaluation, plugin_config=config)


class ArtifactCapTestCase(_RunWithArtifacts):
    async def test_an_artifact_above_the_cap_is_refused_and_not_stored(self):
        with patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET, "TARGET_FILE_MAX_MB": "1"}), \
             patch.object(internal.file_repository, "upload_file", return_value=True) as stored:
            response = await internal_client.post(
                f"/evaluations/{self.evaluation.pid}/artifacts",
                data={"evaluation_plugin_uuid": str(self.evaluation_plugin.pid)},
                FILES={"file": SimpleUploadedFile("big.log", b"x" * (1024 * 1024 + 1))},
                headers={"X-Internal-Secret": INTERNAL_SECRET})
        self.assertEqual(413, response.status_code, response.content[:200])
        stored.assert_not_called()


class ArtifactPreviewsTestCase(_RunWithArtifacts):
    async def _list(self, objects, reads=None):
        reads = [] if reads is None else reads

        def get_object(container, key):
            found = objects[key]
            if isinstance(found, Exception):
                raise found
            return {"Body": _Body(found, reads)}

        with patch.object(evaluation_routes.file_repository, "get_object", side_effect=get_object):
            try:
                return await public_client.get(
                    f"/{self.evaluation.pid}/artifacts",
                    query_params={"evaluation_plugin_uuid": str(self.evaluation_plugin.pid)})
            except Exception as exc:  # the test client re-raises what a server answers as a 500
                self.fail(f"the artifact list answered 500: {exc!r}")

    async def test_a_csv_that_is_not_utf8_is_previewed_with_replacement_characters(self):
        await Artifact.objects.acreate(name="rows.csv", description="", data="rows.csv",
                                       evaluation_plugin=self.evaluation_plugin)
        response = await self._list({"rows.csv": b"a,b\n\xff\xfe,2\n"})
        self.assertEqual(200, response.status_code, response.content[:300])
        self.assertEqual([["a", "b"], ["��", "2"]], response.json()[0]["preview"]["data"])

    async def test_one_unreadable_artifact_gives_none_and_the_others_still_show(self):
        await Artifact.objects.acreate(name="gone.log", description="", data="a-gone.log",
                                       evaluation_plugin=self.evaluation_plugin)
        await Artifact.objects.acreate(name="run.log", description="", data="b-run.log",
                                       evaluation_plugin=self.evaluation_plugin)
        response = await self._list({"a-gone.log": RuntimeError("object store did not answer"),
                                     "b-run.log": b"log body"})
        self.assertEqual(200, response.status_code, response.content[:300])
        previews = {a["data"]: a["preview"]["data"] for a in response.json()}
        self.assertEqual({"a-gone.log": None, "b-run.log": "log body"}, previews)

    async def test_a_preview_reads_only_the_head_of_a_large_text_file(self):
        await Artifact.objects.acreate(name="big.log", description="", data="big.log",
                                       evaluation_plugin=self.evaluation_plugin)
        reads = []
        response = await self._list({"big.log": b"y" * (4 * 1024 * 1024)}, reads)
        self.assertEqual(200, response.status_code, response.content[:300])
        limit = getattr(evaluation_routes, "PREVIEW_BYTES", None) or 256 * 1024
        self.assertTrue(reads and all(r is not None and 0 < r <= limit + 1 for r in reads), reads)
        self.assertLessEqual(len(response.json()[0]["preview"]["data"]), limit)
