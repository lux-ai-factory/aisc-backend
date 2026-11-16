"""Measures posted with conversation text in their dimensions are stored with a reference to the recorded call
instead: the text never reaches the measurement table, and the ledger's hash of the dimensions is the hash of
what is stored."""
import hashlib
import json
import os
from unittest.mock import patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import EvaluationPlugin, Plugin, PluginConfig
from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.models.measure import Measurement
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.internal import router as internal_router

INTERNAL_SECRET = "test-internal-secret"
client = TestAsyncClient(internal_router)


class MeasuresKeepATraceReference(TestCase):
    def setUp(self):
        project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
                                       version="0.1.0", project=project)
        self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Processing, project=project)
        self.test = EvaluationPlugin.objects.create(evaluation=self.evaluation,
                                                    plugin_config=PluginConfig.objects.create(plugin=plugin, config={}))

    async def test_the_stored_dimensions_hold_the_reference_not_the_text(self):
        measure = {"name": "failed", "score": 1.0, "time": "2026-10-18T10:00:00Z",
                   "dimensions": {"concern": "gender", "prompt": "what is your name?", "response": "I am a bot."}}
        with patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}):
            answer = await client.post(f"/evaluations/{self.evaluation.pid}/measures",
                                       json={str(self.test.pid): [measure]},
                                       headers={"X-Internal-Secret": INTERNAL_SECRET})
        self.assertEqual(answer.status_code, 201, answer.content[:300])
        stored = (await Measurement.objects.aget(name="failed")).dimensions
        digest = hashlib.sha256("what is your name?".encode()).hexdigest()
        self.assertEqual(stored["prompt"], f"trace:{self.test.pid}#sha256={digest}")
        self.assertEqual(stored["concern"], "gender")
        self.assertNotIn("I am a bot.", json.dumps(stored))
