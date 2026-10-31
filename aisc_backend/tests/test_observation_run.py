"""Each observation names the run whose measures it holds.

An observation used to carry only its evaluation and the plugin's tag (`package::name (vX)`), so two runs of one
plugin in one evaluation could not be told apart. POST /internal/evaluations/{pid}/measures now records the run
on each observation it makes. Migration 0026 fills in the run of an older observation when its tag matches
exactly one run of its evaluation; an observation whose tag two runs share, or none, stays without one.
"""
import importlib
import os
from types import SimpleNamespace
from unittest import mock

from django.apps import apps
from django.db import connection
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import EvaluationPlugin, Plugin, PluginConfig
from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.models.observation import Observation
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.internal import router as internal_router

MIGRATION = importlib.import_module("aisc_backend.migrations.0026_observation_evaluation_plugin")
INTERNAL_SECRET = "test-internal-secret"
TIME = "2026-10-18T10:00:00Z"
internal = TestAsyncClient(internal_router)


class Runs(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        self.plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
                                            version="0.1.0", project=self.project)
        self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=self.project)
        self.first = self.make_run(self.evaluation)
        self.second = self.make_run(self.evaluation)

    def make_run(self, evaluation, plugin=None):
        config = PluginConfig.objects.create(plugin=plugin or self.plugin, config={})
        return EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=config)


class MeasuresNameTheirRun(Runs):
    async def test_two_runs_of_one_plugin_get_an_observation_each(self):
        body = {str(self.first.pid): [{"name": "Accuracy", "score": 0.9, "time": TIME}],
                str(self.second.pid): [{"name": "Accuracy", "score": 0.4, "time": TIME}]}
        with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}):
            res = await internal.post(f"/evaluations/{self.evaluation.pid}/measures", json=body,
                                      headers={"X-Internal-Secret": INTERNAL_SECRET})
        self.assertEqual(res.status_code, 201, res.text)
        scores = {row["evaluation_plugin_id"]: row["measurements__score"] async for row in
                  Observation.objects.values("evaluation_plugin_id", "measurements__score")}
        self.assertEqual(scores, {self.first.id: 0.9, self.second.id: 0.4})


class TheMigrationFillsInTheRun(Runs):
    def observe(self, evaluation, plugin=None, **kw):
        return Observation.objects.create(observer="AISC System", tool=str(plugin or self.plugin),
                                          evaluation=evaluation, **kw)

    def backfill(self):
        MIGRATION.backfill(apps, SimpleNamespace(connection=connection))

    def test_a_tag_one_run_has_gets_that_run(self):
        other = Plugin.objects.create(name="Other", display_name="Other", package_name="other-pkg",
                                      version="1.0", project=self.project)
        alone = self.make_run(self.evaluation, other)
        observation = self.observe(self.evaluation, other)
        self.backfill()
        observation.refresh_from_db()
        self.assertEqual(observation.evaluation_plugin_id, alone.id)

    def test_a_tag_two_runs_share_stays_without_a_run(self):
        observation = self.observe(self.evaluation)
        self.backfill()
        observation.refresh_from_db()
        self.assertIsNone(observation.evaluation_plugin_id)

    def test_only_runs_of_the_observations_own_evaluation_count(self):
        later = Evaluation.objects.create(status=EvaluationStatus.Pending, project=self.project)
        run = self.make_run(later)
        observation = self.observe(later)
        self.backfill()
        observation.refresh_from_db()
        self.assertEqual(observation.evaluation_plugin_id, run.id)

    def test_a_tag_no_run_has_stays_without_a_run(self):
        observation = Observation.objects.create(observer="AISC System", tool="gone::Gone (v1)",
                                                 evaluation=self.evaluation)
        self.backfill()
        observation.refresh_from_db()
        self.assertIsNone(observation.evaluation_plugin_id)

    def test_an_observation_that_names_its_run_keeps_it(self):
        observation = self.observe(self.evaluation, evaluation_plugin=self.second)
        self.backfill()
        observation.refresh_from_db()
        self.assertEqual(observation.evaluation_plugin_id, self.second.id)
