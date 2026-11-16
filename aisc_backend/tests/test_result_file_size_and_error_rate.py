"""A result file keeps the size the ledger event states, and the project's measure error rate counts only real
errors: 'no error' is stored as NULL, '' or 'N/A' depending on who wrote the measure."""
import os
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from ninja.testing import TestAsyncClient

from aisc_backend.models import EvaluationPlugin, Measurement, Metric, Observation, Plugin, PluginConfig
from aisc_backend.models.artifact import Artifact
from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.repositories.stats_repository import StatsRepository
from aisc_backend.routers import internal
from aisc_backend.routers.internal import router as internal_router

internal_client = TestAsyncClient(internal_router)
INTERNAL_SECRET = "fake-internal-secret"


class ResultFileSizeTestCase(TestCase):
    def setUp(self):
        project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=project)
        plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
                                       version="0.1.0", project=project)
        config = PluginConfig.objects.create(plugin=plugin, config={})
        self.evaluation_plugin = EvaluationPlugin.objects.create(evaluation=self.evaluation, plugin_config=config)

    async def test_an_uploaded_result_file_keeps_its_size(self):
        body = b"a,b\n1,2\n" * 64
        with patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}), \
             patch.object(internal.file_repository, "upload_file", return_value=True):
            response = await internal_client.post(
                f"/evaluations/{self.evaluation.pid}/artifacts",
                data={"evaluation_plugin_uuid": str(self.evaluation_plugin.pid)},
                FILES={"file": SimpleUploadedFile("results.csv", body)},
                headers={"X-Internal-Secret": INTERNAL_SECRET})
        self.assertEqual(200, response.status_code, response.content[:200])
        stored = await Artifact.objects.aget(evaluation_plugin=self.evaluation_plugin)
        self.assertEqual(len(body), stored.file_size, "the stored result file has no size")


class MeasureErrorRateTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Done, project=self.project)
        self.observation = Observation.objects.create(evaluation=evaluation, name="o", description="",
                                                      observer="t", tool="t")
        self.metric = Metric.objects.create(name="accuracy", description="", type_spec="float")

    async def _measure(self, error):
        await Measurement.objects.acreate(observation=self.observation, metric=self.metric, name="accuracy",
                                          description="", score=0.5, time=timezone.now(), error=error)

    async def _rate(self):
        return (await StatsRepository().get_overview(self.project.pid))["error_rate"]

    async def test_measures_without_an_error_give_a_zero_error_rate_however_no_error_is_written(self):
        for error in (None, "", "N/A"):
            await self._measure(error)
        self.assertEqual(0.0, await self._rate(), "measures with no error are counted as errors")

    async def test_only_a_real_error_counts(self):
        for error in (None, "", "N/A", "timeout"):
            await self._measure(error)
        self.assertEqual(25.0, await self._rate())
