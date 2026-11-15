"""A Stop, or a sibling's failure, that commits while the worker's report is on its way is never overwritten by that
report: the worker's write only applies to a row that has not failed in the meantime.
"""
from unittest import mock

from aisc_backend.models import Evaluation, EvaluationPlugin, EvaluationStatus
from aisc_backend.routers import internal
from aisc_backend.routers.evaluation import STOPPED_BY_A_PERSON
from aisc_backend.tests.routers.test_run_states import RunsAndTests


class AStopThatLandsDuringAWorkerReportIsKept(RunsAndTests):
    """The Stop commits after the worker's report has read the row and before it writes it back."""

    def stop_lands_before_the_write(self, repository):
        original = repository.save

        async def save_after_a_stop(instance, *a, **kw):
            await EvaluationPlugin.objects.filter(evaluation=self.evaluation).aupdate(
                status="Failed", error_message=STOPPED_BY_A_PERSON)
            await Evaluation.objects.filter(pk=self.evaluation.pk).aupdate(status=EvaluationStatus.Failed)
            return await original(instance, *a, **kw)

        return mock.patch.object(repository, "save", new=save_after_a_stop)

    async def test_a_tests_finish_report_does_not_overwrite_a_stop(self):
        self.evaluation = await self.run_of(EvaluationStatus.Processing, "Running")
        with self.stop_lands_before_the_write(internal.evaluation_plugin_repository):
            await self.call("patch", self.start_of(self.evaluation, self.test), {"field": "finished_at"})
        await self.test.arefresh_from_db()
        self.assertEqual((self.test.status, self.test.error_message), ("Failed", STOPPED_BY_A_PERSON))

    async def test_the_runs_done_report_does_not_overwrite_a_stop(self):
        self.evaluation = await self.run_of(EvaluationStatus.Processing, "Done")
        with self.stop_lands_before_the_write(internal.evaluation_repository):
            await self.call("put", f"/evaluations/{self.evaluation.pid}?status=Done")
        await self.evaluation.arefresh_from_db()
        self.assertEqual(self.evaluation.status, EvaluationStatus.Failed)
