"""
Tests for stopping a running evaluation.

Stop sets the evaluation/plugins to Failed (there is no Cancelled status) and
revokes the celery tasks. The celery layer is mocked so tests never touch a
broker; only the DB side effects are asserted.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_evaluation.models import EvaluationPlugin
from aisc_evaluation.models import Evaluation, EvaluationStatus
from aisc_backend.models.project import Project, ProjectStatus
from aisc_evaluation.routers.task import router as task_router

task_client = TestAsyncClient(task_router)


class StopEvaluationTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="stopper", status=ProjectStatus.Ready)
        self.evaluation = Evaluation.objects.create(
            status=EvaluationStatus.Processing, project=self.project, task=uuid.uuid4()
        )
        self.running_plugin = EvaluationPlugin.objects.create(
            evaluation=self.evaluation, status="Running"
        )
        self.pending_plugin = EvaluationPlugin.objects.create(
            evaluation=self.evaluation, status="Pending"
        )

    async def _plugins(self):
        return [p async for p in EvaluationPlugin.objects.filter(evaluation=self.evaluation)]

    async def test_stop_evaluation_revokes_and_marks_failed(self):
        stopped = [str(self.evaluation.task), str(uuid.uuid4())]
        with (
            patch(
                "aisc_evaluation.routers.task.celery_service.stop_evaluation",
                new=AsyncMock(return_value=stopped),
            ) as mock_stop,
            patch("aisc_evaluation.routers.task.log_action", new=MagicMock()) as mock_log,
        ):
            resp = await task_client.post(f"/{self.evaluation.task}/stop")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["stopped"], stopped)
        mock_stop.assert_awaited_once_with(self.evaluation.pid, self.evaluation.task)

        evaluation = await Evaluation.objects.aget(pid=self.evaluation.pid)
        self.assertEqual(evaluation.status, EvaluationStatus.Failed)
        for plugin in await self._plugins():
            self.assertEqual(plugin.status, "Failed")
            self.assertEqual(plugin.error_message, "Stopped by user")
            self.assertIsNotNone(plugin.finished_at)

        # the immudb audit event records who stopped it and what was stopped
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["action"], "stop")
        self.assertEqual(kwargs["resource_type"], "evaluation")
        self.assertEqual(kwargs["resource_id"], str(self.evaluation.pid))
        self.assertEqual(kwargs["metadata"]["projectPid"], str(self.project.pid))
        self.assertEqual(kwargs["metadata"]["status"], "Failed")
        self.assertEqual(
            set(kwargs["metadata"]["pluginsStopped"]),
            {str(self.running_plugin.pid), str(self.pending_plugin.pid)},
        )
        self.assertEqual(kwargs["metadata"]["stoppedTaskIds"], stopped)

    async def test_stop_unknown_evaluation_404(self):
        resp = await task_client.post(f"/{uuid.uuid4()}/stop")
        self.assertEqual(resp.status_code, 404)
