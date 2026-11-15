"""A test's measures are recorded once, and a metric name is one metric.

A second post of measures for a test that already has them (a redelivered task, a retried call) is refused with 409
and records nothing more. The database itself refuses a second observation of one test and a second metric of one
name, so two concurrent posts cannot both land. The migration that adds those rules names the duplicates it finds
instead of failing with a bare integrity error.
"""
import importlib
import os
import pathlib
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import EvaluationPlugin, Plugin, PluginConfig
from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.models.measure import Measurement
from aisc_backend.models.metric import Metric
from aisc_backend.models.observation import Observation
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers import internal
from aisc_backend.routers.internal import router as internal_router

INTERNAL_SECRET = "test-internal-secret"
TIME = "2026-10-18T10:00:00Z"
client = TestAsyncClient(internal_router)
MIGRATIONS = pathlib.Path(importlib.import_module("aisc_backend.migrations").__file__).parent


def MEASURE(name, score):
    return {"name": name, "score": score, "time": TIME}


class MeasuresAreRecordedOnce(TestCase):
    def setUp(self):
        project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
                                       version="0.1.0", project=project)
        self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Processing, project=project)
        self.test_a = EvaluationPlugin.objects.create(evaluation=self.evaluation,
                                                      plugin_config=PluginConfig.objects.create(plugin=plugin, config={}))
        self.test_b = EvaluationPlugin.objects.create(evaluation=self.evaluation,
                                                      plugin_config=PluginConfig.objects.create(plugin=plugin, config={}))

    async def post(self, test, measures):
        with patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}):
            return await client.post(f"/evaluations/{self.evaluation.pid}/measures", json={str(test.pid): measures},
                                     headers={"X-Internal-Secret": INTERNAL_SECRET})

    async def test_a_tests_measures_are_recorded_once(self):
        first = await self.post(self.test_a, [MEASURE("score", 0.5), MEASURE("rate", 0.1)])
        again = await self.post(self.test_a, [MEASURE("score", 0.9)])
        self.assertEqual((first.status_code, again.status_code), (201, 409))
        self.assertEqual(again.json()["detail"], getattr(internal, "ALREADY_RECORDED", "<ALREADY_RECORDED undefined>"))
        self.assertEqual(await Observation.objects.acount(), 1)
        self.assertEqual(await Measurement.objects.acount(), 2)

    async def test_two_tests_naming_one_metric_share_it(self):
        await self.post(self.test_a, [MEASURE("score", 0.5)])
        await self.post(self.test_b, [MEASURE("score", 0.7)])
        self.assertEqual(await Metric.objects.filter(name="score").acount(), 1)

    def test_the_database_refuses_a_second_observation_of_a_test(self):
        Observation.objects.create(observer="x", tool="t", evaluation=self.evaluation, evaluation_plugin=self.test_a)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Observation.objects.create(observer="x", tool="t", evaluation=self.evaluation,
                                       evaluation_plugin=self.test_a)

    def test_observations_of_no_test_are_not_limited(self):
        for _ in range(2):
            Observation.objects.create(observer="x", tool="t", evaluation=self.evaluation, evaluation_plugin=None)
        self.assertEqual(Observation.objects.filter(evaluation_plugin=None).count(), 2)

    def test_the_database_refuses_two_metrics_of_one_name(self):
        Metric.objects.create(name="score")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Metric.objects.create(name="score")

    def test_the_migration_names_the_duplicates_it_cannot_make_unique(self):
        found = sorted(MIGRATIONS.glob("*_measures_recorded_once.py"))
        self.assertTrue(found, "no migration makes a test's measures and a metric's name unique")
        migration = importlib.import_module(f"aisc_backend.migrations.{found[-1].stem}")
        with self.assertRaisesRegex(RuntimeError, "metric names.*'score'"):
            migration.refuse_duplicates(["score", "rate", "score"], "metric names")
