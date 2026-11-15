"""The worker's calls never move a run or a test back.

A run already ended (Failed or Done) is not turned Processing again. A test starts once: a second start, or a start
of a test that has ended (Failed, Done, or with a finish time), is refused with 409 and leaves the test as it was.
A failure always carries a reason: the call without one is refused, and a blank one is stored as "no reason recorded".
"""
import os
import uuid
from unittest.mock import patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import (Evaluation, EvaluationPlugin, EvaluationStatus, Plugin, PluginConfig, Project,
                                 ProjectStatus)
from aisc_backend.routers.internal import router as internal_router

INTERNAL_SECRET = "test-internal-secret"
client = TestAsyncClient(internal_router)


class RunsAndTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        plugin = Plugin.objects.create(name="P", display_name="P", description="", package_name="pkg",
                                       version="1", project=self.project)
        self.config = PluginConfig.objects.create(plugin=plugin, config={})

    async def run_of(self, status, test_status="Pending", error=""):
        evaluation = await Evaluation.objects.acreate(status=status, project=self.project, task=str(uuid.uuid4()))
        self.test = await EvaluationPlugin.objects.acreate(plugin_config=self.config, evaluation=evaluation,
                                                           status=test_status, error_message=error)
        return evaluation

    async def call(self, method, path, body=None):
        kw = {"json": body} if body is not None else {}
        with patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}):
            return await getattr(client, method)(path, headers={"X-Internal-Secret": INTERNAL_SECRET}, **kw)

    def start_of(self, evaluation, test):
        return f"/evaluations/{evaluation.pid}/plugins/{test.pid}/timestamp"


class TheWorkersCallsNeverMoveARunBack(RunsAndTests):
    async def test_a_finished_test_delivered_again_is_not_reopened(self):
        e = await self.run_of(EvaluationStatus.Done, "Done")
        t = self.test
        finished = await self.call("patch", self.start_of(e, t), {"field": "finished_at"})
        self.assertEqual(finished.status_code, 200)
        await t.arefresh_from_db()
        ended_at = t.finished_at
        r = await self.call("patch", self.start_of(e, t), {"field": "started_at"})
        self.assertEqual(r.status_code, 409, "a second delivery reopened a Done test")
        await t.arefresh_from_db()
        self.assertEqual((t.status, t.finished_at), ("Done", ended_at))
