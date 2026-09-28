"""What the worker is sent when a run starts (adapt plan 2026-09-28, items 5 and 6).

Both modes send Sean's message: run_evaluation with args=[evaluation_uuid]. Standalone
sends nothing else (it equals master). The configurator adds the run in the Celery
message headers, under aisc_run: the platform project, the evaluation and the ticket
the door checks on every internal call. The worker's aisc_eval.run_context copies that
header onto every task it publishes while the run is running.

Moved here from StartingARun in test_modes_routes.py, which pinned the old arguments.
"""
import unittest
import unittest.mock as mock
import uuid

from django.conf import settings
from django.test import TestCase

CONFIGURATOR = settings.AISC_DEPLOYMENT == "configurator"
standalone_run = unittest.skipIf(CONFIGURATOR, "standalone run")
configurator_run = unittest.skipUnless(CONFIGURATOR, "configurator run")

A_PID = "00000000-0000-0000-0000-000000000001"
RUN_EVAL_TASK = "aisc_eval.celery_tasks.run_evaluation"


class StartingARun(TestCase):
    """What the worker is sent."""

    async def _dispatch(self):
        from aisc_backend.models import Evaluation, EvaluationStatus, Project, ProjectStatus
        from aisc_backend.services import celery_service

        project = await Project.objects.acreate(name="p", status=ProjectStatus.Created,
                                                platform_project_id=A_PID)
        evaluation = await Evaluation.objects.acreate(project=project, status=EvaluationStatus.Pending)
        send = mock.Mock(return_value=mock.Mock(task_id=str(uuid.uuid4())))
        with mock.patch.object(celery_service.celery, "send_task", new=send):
            await celery_service.run_evaluation(evaluation.pid)
        send.assert_called_once()
        return evaluation.pid, send.call_args

    @standalone_run
    async def test_standalone_sends_exactly_masters_message(self):
        evaluation_pid, call = await self._dispatch()
        self.assertEqual(call.args, (RUN_EVAL_TASK,))
        self.assertEqual(call.kwargs, {"args": [evaluation_pid]})

    @configurator_run
    async def test_the_configurator_sends_masters_arguments_and_the_run_in_the_headers(self):
        from aisc_backend import projectdb

        evaluation_pid, call = await self._dispatch()
        pid = projectdb.normalise(A_PID)
        evaluation = projectdb.normalise(evaluation_pid)
        self.assertEqual(call.args, (RUN_EVAL_TASK,))
        self.assertEqual(call.kwargs, {
            "args": [evaluation_pid],
            "headers": {"aisc_run": {"project": pid, "evaluation": evaluation,
                                     "ticket": projectdb.run_ticket(pid, evaluation)}},
        })
