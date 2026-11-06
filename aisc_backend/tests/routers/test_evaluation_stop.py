"""A run still going can be stopped from the progress bar. Its tests not yet finished are marked failed with the
reason that a person stopped them (a test already done keeps its result), the run is failed, and the worker is told
to end the run's tasks: the one running ends its plugin, those queued never start. The calls already made stay in
the run's record. Every stop is in the audit log. A run already finished cannot be stopped.

The worker's own handler marks the running test failed once more when it ends; the first reason is kept, so the
run says it was stopped by a person and not that its time ran out.
"""
import os
import unittest.mock as mock
import uuid
from unittest.mock import patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.auth import keycloak
from aisc_backend.models import (Evaluation, EvaluationPlugin, EvaluationStatus, Plugin, PluginConfig, Project,
                                 ProjectStatus)
from aisc_backend.routers import evaluation as evaluation_routes
from aisc_backend.routers.internal import router as internal_router
from aisc_backend.services import celery_service

SIGNED_IN = {"Authorization": "Bearer development"}
evaluations = TestAsyncClient(evaluation_routes.router)
internal = TestAsyncClient(internal_router)
INTERNAL_SECRET = "test-internal-secret"

_auth_off = None


def setUpModule():
    global _auth_off
    _auth_off = mock.patch.object(keycloak, "AUTH_ENABLED", False)
    _auth_off.start()


def tearDownModule():
    _auth_off.stop()


class StopARun(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Mijke", status=ProjectStatus.Ready)
        plugin = Plugin.objects.create(name="LangBiTePlugin", display_name="LangBiTe (Mijke)", description="",
                                       package_name="aisc-plugin-langbite-mijke", version="0.3.3",
                                       project=self.project)
        self.config = PluginConfig.objects.create(plugin=plugin, config={})

    def run_of(self, status, *test_statuses):
        evaluation = Evaluation.objects.create(status=status, project=self.project, task=str(uuid.uuid4()))
        for test_status in test_statuses:
            EvaluationPlugin.objects.create(plugin_config=self.config, evaluation=evaluation, status=test_status)
        return evaluation

    async def stop(self, pid, sent=None):
        sent = sent if sent is not None else mock.Mock()
        with mock.patch.object(evaluation_routes.celery_service, "stop_run", new=mock.AsyncMock(side_effect=sent)), \
             mock.patch.object(evaluation_routes, "log_action") as audit:
            response = await evaluations.post(f"/{pid}/stop", headers=SIGNED_IN)
        return response, audit, sent

    async def statuses_of(self, evaluation):
        return [(t.status, t.error_message, t.finished_at is not None)
                async for t in EvaluationPlugin.objects.filter(evaluation=evaluation).order_by("id")]

    async def test_a_running_run_is_failed_and_its_unfinished_tests_say_a_person_stopped_them(self):
        evaluation = await sync(self.run_of, EvaluationStatus.Processing, "Running", "Pending", "Done")
        response, _, _ = await self.stop(evaluation.pid)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"pid": str(evaluation.pid), "status": "Failed"})
        await evaluation.arefresh_from_db()
        self.assertEqual(evaluation.status, EvaluationStatus.Failed)
        reason = evaluation_routes.STOPPED_BY_A_PERSON
        self.assertEqual(await self.statuses_of(evaluation),
                         [("Failed", reason, True), ("Failed", reason, True), ("Done", "", False)])

    async def test_a_pending_run_can_be_stopped_too(self):
        evaluation = await sync(self.run_of, EvaluationStatus.Pending, "Pending")
        response, _, _ = await self.stop(evaluation.pid)
        self.assertEqual(response.status_code, 200, response.content)

    async def test_the_worker_is_told_to_end_the_runs_tasks(self):
        evaluation = await sync(self.run_of, EvaluationStatus.Processing, "Running")
        _, _, sent = await self.stop(evaluation.pid)
        await evaluation.arefresh_from_db()
        sent.assert_called_once_with(evaluation.pid, evaluation.task)

    async def test_every_stop_is_in_the_audit_log(self):
        evaluation = await sync(self.run_of, EvaluationStatus.Processing, "Running")
        _, audit, _ = await self.stop(evaluation.pid)
        audit.assert_called_once()
        kwargs = audit.call_args.kwargs
        self.assertEqual((kwargs["action"], kwargs["resource_type"], kwargs["resource_id"]),
                         ("stop", "evaluation", str(evaluation.pid)))

    async def test_a_run_already_finished_cannot_be_stopped(self):
        for status in (EvaluationStatus.Done, EvaluationStatus.Failed):
            evaluation = await sync(self.run_of, status, "Done")
            response, audit, sent = await self.stop(evaluation.pid)
            self.assertEqual(response.status_code, 409, response.content)
            sent.assert_not_called()
            audit.assert_not_called()
            self.assertEqual(await self.statuses_of(evaluation), [("Done", "", False)])

    async def test_stopping_a_run_that_does_not_exist_is_404(self):
        response, audit, sent = await self.stop(uuid.uuid4())
        self.assertEqual(response.status_code, 404, response.content)
        sent.assert_not_called()
        audit.assert_not_called()

    async def test_when_the_worker_cannot_be_told_the_person_is_told_and_the_run_stays_failed(self):
        evaluation = await sync(self.run_of, EvaluationStatus.Processing, "Running")
        response, _, _ = await self.stop(evaluation.pid, sent=mock.Mock(side_effect=ConnectionError("broker down")))
        self.assertEqual(response.status_code, 502, response.content)
        self.assertIn("worker", response.json()["detail"])
        await evaluation.arefresh_from_db()
        # queued tests read the run's status before they start, so they never start
        self.assertEqual(evaluation.status, EvaluationStatus.Failed)


class TheFirstReasonIsKept(TestCase):
    """The worker's fail call for a test that is already failed (stopped by a person, or by a sibling's failure)
    leaves the first reason in place."""

    async def test_a_test_already_failed_keeps_its_reason(self):
        project = await Project.objects.acreate(name="p", status=ProjectStatus.Ready)
        plugin = await Plugin.objects.acreate(name="P", display_name="P", description="", package_name="pkg",
                                              version="1", project=project)
        config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        evaluation = await Evaluation.objects.acreate(status=EvaluationStatus.Failed, project=project)
        test = await EvaluationPlugin.objects.acreate(plugin_config=config, evaluation=evaluation, status="Failed",
                                                      error_message="Stopped by a person")
        with patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}):
            response = await internal.patch(f"/evaluations/{evaluation.pid}/plugins/{test.pid}/fail",
                                            json={"error_message": "SoftTimeLimitExceeded"},
                                            headers={"X-Internal-Secret": INTERNAL_SECRET})
        self.assertEqual(response.status_code, 200, response.content)
        await test.arefresh_from_db()
        self.assertEqual(test.error_message, "Stopped by a person")

    async def test_a_test_not_yet_failed_takes_the_reason_as_before(self):
        project = await Project.objects.acreate(name="p", status=ProjectStatus.Ready)
        plugin = await Plugin.objects.acreate(name="P", display_name="P", description="", package_name="pkg",
                                              version="1", project=project)
        config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        evaluation = await Evaluation.objects.acreate(status=EvaluationStatus.Processing, project=project)
        test = await EvaluationPlugin.objects.acreate(plugin_config=config, evaluation=evaluation, status="Running")
        with patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}):
            await internal.patch(f"/evaluations/{evaluation.pid}/plugins/{test.pid}/fail",
                                 json={"error_message": "Plugin failed: boom"},
                                 headers={"X-Internal-Secret": INTERNAL_SECRET})
        await test.arefresh_from_db()
        self.assertEqual((test.status, test.error_message), ("Failed", "Plugin failed: boom"))


class TheWorkerIsTold(TestCase):
    """stop_run revokes the run's dispatcher and every install and test the worker listed for the run. The one
    running gets SIGUSR1, which Celery turns into SoftTimeLimitExceeded inside the task: the worker's own failure
    handler then ends the plugin's processes and its run pass, as for a test that failed."""

    def fake_celery(self, listed):
        revoked = []
        keys = {"eval_tasks:e1": listed} if listed is not None else {}
        fake = mock.Mock()
        fake.backend.client.get = keys.get
        fake.control.revoke = lambda task_id, **kw: revoked.append((task_id, kw))
        return fake, revoked

    async def test_the_dispatcher_and_every_listed_task_are_revoked_with_the_soft_limit_signal(self):
        fake, revoked = self.fake_celery('["install-1", "test-1"]')
        with mock.patch.object(celery_service, "celery", fake):
            await celery_service.stop_run("e1", "dispatch-1")
        self.assertEqual([task for task, _ in revoked], ["dispatch-1", "install-1", "test-1"])
        self.assertTrue(all(kw == {"terminate": True, "signal": "SIGUSR1"} for _, kw in revoked))

    async def test_a_run_whose_tasks_are_not_listed_yet_has_only_its_dispatcher_revoked(self):
        fake, revoked = self.fake_celery(None)
        with mock.patch.object(celery_service, "celery", fake):
            await celery_service.stop_run("e1", "dispatch-1")
        self.assertEqual([task for task, _ in revoked], ["dispatch-1"])

    async def test_a_run_with_no_dispatcher_recorded_revokes_what_is_listed(self):
        fake, revoked = self.fake_celery('["test-1"]')
        with mock.patch.object(celery_service, "celery", fake):
            await celery_service.stop_run("e1", None)
        self.assertEqual([task for task, _ in revoked], ["test-1"])


async def sync(fn, *args):
    from asgiref.sync import sync_to_async
    return await sync_to_async(fn)(*args)
