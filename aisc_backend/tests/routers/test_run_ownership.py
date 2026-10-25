"""A worker call names a plugin run in its path, body or form: the run must belong to the evaluation in the path.

The four internal routes that take an evaluation in the path and one of its plugin runs by its own id
(timestamp, fail, measures, artifacts) answer 404 for a run that is unknown or of another evaluation,
and write nothing. Unknown and foreign look the same to the caller.
"""
import os
import uuid
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import (
    EvaluationPlugin, Measurement, Metric, Observation, Plugin, PluginConfig,
)
from aisc_backend.models.artifact import Artifact
from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.repositories.plugin_repository import EvaluationPluginRepository
from aisc_backend.routers import internal
from aisc_backend.routers.internal import router as internal_router

client = TestAsyncClient(internal_router)

INTERNAL_SECRET = "test-internal-secret"

NO_EVALUATION = "No evaluation found"
NO_EVALUATION_PLUGIN = "No evaluation plugin of this evaluation found"

ACCURACY = {"name": "Accuracy", "score": 0.9, "time": "2026-10-18T10:00:00Z"}
F1 = {"name": "F1", "score": 0.8, "time": "2026-10-18T10:00:00Z"}


class TwoEvaluationsWithOneRunEach(TestCase):
    """Two evaluations of one project, each with one run of the same plugin config."""

    def setUp(self):
        project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        self.plugin = Plugin.objects.create(
            name="DemoPlugin", display_name="Demo", package_name="demo-pkg", version="0.1.0", project=project,
        )
        plugin_config = PluginConfig.objects.create(plugin=self.plugin, config={})
        self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=project)
        self.other_evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=project)
        self.run = EvaluationPlugin.objects.create(evaluation=self.evaluation, plugin_config=plugin_config)
        self.other_run = EvaluationPlugin.objects.create(
            evaluation=self.other_evaluation, plugin_config=plugin_config)
        self.created_status = self.run.status
        self.created_error_message = self.run.error_message

    async def _as_the_worker(self, method, path, **kwargs):
        with patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}):
            return await getattr(client, method)(path, headers={"X-Internal-Secret": INTERNAL_SECRET}, **kwargs)

    async def assertRunUnchanged(self, run):
        await run.arefresh_from_db()
        self.assertEqual(self.created_status, run.status)
        self.assertEqual(self.created_error_message, run.error_message)
        self.assertIsNone(run.started_at)
        self.assertIsNone(run.finished_at)

    async def assertEvaluationStatus(self, evaluation, status):
        await evaluation.arefresh_from_db()
        self.assertEqual(status, evaluation.status)


class PluginRunOfItsEvaluationTestCase(TwoEvaluationsWithOneRunEach):

    # timestamp

    def _timestamp(self, evaluation_pid, run_pid, field):
        return self._as_the_worker(
            "patch", f"/evaluations/{evaluation_pid}/plugins/{run_pid}/timestamp", json={"field": field})

    async def test_timestamp_on_a_run_of_another_evaluation_is_not_found(self):
        response = await self._timestamp(self.evaluation.pid, self.other_run.pid, "started_at")
        self.assertEqual(404, response.status_code)
        self.assertEqual(NO_EVALUATION_PLUGIN, response.json()["detail"])
        await self.assertRunUnchanged(self.other_run)
        await self.assertRunUnchanged(self.run)

    async def test_timestamp_on_an_unknown_run_is_not_found(self):
        response = await self._timestamp(self.evaluation.pid, uuid.uuid4(), "started_at")
        self.assertEqual(404, response.status_code)
        self.assertEqual(NO_EVALUATION_PLUGIN, response.json()["detail"])

    async def test_timestamp_on_an_unknown_evaluation_is_not_found(self):
        response = await self._timestamp(uuid.uuid4(), self.run.pid, "started_at")
        self.assertEqual(404, response.status_code)
        self.assertEqual(NO_EVALUATION, response.json()["detail"])
        await self.assertRunUnchanged(self.run)

    async def test_timestamp_of_another_evaluations_run_with_a_bad_field_is_not_found(self):
        response = await self._timestamp(self.evaluation.pid, self.other_run.pid, "bogus")
        self.assertEqual(404, response.status_code)
        self.assertEqual(NO_EVALUATION_PLUGIN, response.json()["detail"])

    async def test_timestamp_on_its_own_run_is_stored(self):
        response = await self._timestamp(self.evaluation.pid, self.run.pid, "started_at")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("ok", response.json())
        await self.run.arefresh_from_db()
        self.assertIsNotNone(self.run.started_at)
        self.assertEqual("Running", self.run.status)

        response = await self._timestamp(self.evaluation.pid, self.run.pid, "finished_at")
        self.assertEqual(200, response.status_code, response.text)
        await self.run.arefresh_from_db()
        self.assertIsNotNone(self.run.finished_at)
        self.assertEqual("Done", self.run.status)

    async def test_timestamp_with_a_bad_field_on_its_own_run_is_refused(self):
        response = await self._timestamp(self.evaluation.pid, self.run.pid, "bogus")
        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid field: bogus. Must be 'started_at' or 'finished_at'", response.json()["detail"])

    # fail

    def _fail(self, evaluation_pid, run_pid):
        return self._as_the_worker(
            "patch", f"/evaluations/{evaluation_pid}/plugins/{run_pid}/fail", json={"error_message": "boom"})

    async def test_failing_a_run_of_another_evaluation_is_not_found(self):
        response = await self._fail(self.evaluation.pid, self.other_run.pid)
        self.assertEqual(404, response.status_code)
        self.assertEqual(NO_EVALUATION_PLUGIN, response.json()["detail"])
        await self.assertRunUnchanged(self.other_run)
        await self.assertEvaluationStatus(self.evaluation, EvaluationStatus.Pending)
        await self.assertEvaluationStatus(self.other_evaluation, EvaluationStatus.Pending)

    async def test_failing_an_unknown_run_is_not_found(self):
        response = await self._fail(self.evaluation.pid, uuid.uuid4())
        self.assertEqual(404, response.status_code)
        self.assertEqual(NO_EVALUATION_PLUGIN, response.json()["detail"])
        await self.assertEvaluationStatus(self.evaluation, EvaluationStatus.Pending)

    async def test_failing_on_an_unknown_evaluation_is_not_found(self):
        response = await self._fail(uuid.uuid4(), self.run.pid)
        self.assertEqual(404, response.status_code)
        self.assertEqual(NO_EVALUATION, response.json()["detail"])
        await self.assertRunUnchanged(self.run)

    async def test_failing_its_own_run_fails_it_and_its_evaluation(self):
        response = await self._fail(self.evaluation.pid, self.run.pid)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("ok", response.json())
        await self.run.arefresh_from_db()
        self.assertEqual("Failed", self.run.status)
        self.assertEqual("boom", self.run.error_message)
        self.assertIsNotNone(self.run.finished_at)
        await self.assertEvaluationStatus(self.evaluation, EvaluationStatus.Failed)
        await self.assertEvaluationStatus(self.other_evaluation, EvaluationStatus.Pending)

    # measures

    def _measures(self, evaluation_pid, body):
        return self._as_the_worker("post", f"/evaluations/{evaluation_pid}/measures", json=body)

    async def assertNothingRecorded(self):
        self.assertEqual(0, await Observation.objects.filter(evaluation=self.evaluation).acount())
        self.assertEqual(0, await Observation.objects.filter(evaluation=self.other_evaluation).acount())
        self.assertEqual(0, await Measurement.objects.acount())
        self.assertFalse(await Metric.objects.filter(name="Accuracy").aexists())

    async def test_measures_of_another_evaluations_run_are_not_found(self):
        response = await self._measures(self.evaluation.pid, {str(self.other_run.pid): [ACCURACY]})
        self.assertEqual(404, response.status_code)
        self.assertEqual(NO_EVALUATION_PLUGIN, response.json()["detail"])
        await self.assertNothingRecorded()

    async def test_measures_with_one_foreign_run_record_nothing(self):
        body = {str(self.run.pid): [ACCURACY], str(self.other_run.pid): [ACCURACY]}
        response = await self._measures(self.evaluation.pid, body)
        self.assertEqual(404, response.status_code)
        self.assertEqual(NO_EVALUATION_PLUGIN, response.json()["detail"])
        await self.assertNothingRecorded()

    async def test_measures_of_an_unknown_run_are_not_found(self):
        response = await self._measures(self.evaluation.pid, {str(uuid.uuid4()): [ACCURACY]})
        self.assertEqual(404, response.status_code)
        self.assertEqual(NO_EVALUATION_PLUGIN, response.json()["detail"])
        await self.assertNothingRecorded()

    async def test_measures_on_an_unknown_evaluation_are_not_found(self):
        response = await self._measures(uuid.uuid4(), {str(self.run.pid): [ACCURACY]})
        self.assertEqual(404, response.status_code)
        self.assertEqual(NO_EVALUATION, response.json()["detail"])

    async def test_measures_of_its_own_run_are_recorded(self):
        response = await self._measures(self.evaluation.pid, {str(self.run.pid): [ACCURACY, F1]})
        self.assertEqual(201, response.status_code, response.text)
        observations = [o async for o in Observation.objects.filter(evaluation=self.evaluation)]
        self.assertEqual(1, len(observations))
        self.assertEqual(str(self.plugin), observations[0].tool)
        self.assertEqual(2, await Measurement.objects.filter(observation=observations[0]).acount())

    async def test_an_empty_measures_body_records_nothing(self):
        response = await self._measures(self.evaluation.pid, {})
        self.assertEqual(201, response.status_code, response.text)
        self.assertEqual(0, await Observation.objects.filter(evaluation=self.evaluation).acount())

    # artifacts

    async def test_artifact_for_another_evaluations_run_is_not_found(self):
        cases = [
            ("a run of another evaluation", self.evaluation.pid, self.other_run.pid, NO_EVALUATION_PLUGIN),
            ("an unknown run", self.evaluation.pid, uuid.uuid4(), NO_EVALUATION_PLUGIN),
            ("an unknown evaluation", uuid.uuid4(), self.run.pid, NO_EVALUATION),
        ]
        for name, evaluation_pid, run_pid, detail in cases:
            with self.subTest(name):
                upload_file = Mock(return_value=True)
                upload = SimpleUploadedFile("plugin_execution.log", b"log body", content_type="text/plain")
                with patch.object(internal.file_repository, "upload_file", upload_file):
                    response = await self._as_the_worker(
                        "post", f"/evaluations/{evaluation_pid}/artifacts",
                        data={"evaluation_plugin_uuid": str(run_pid)}, FILES={"file": upload})
                self.assertEqual(404, response.status_code)
                self.assertEqual(detail, response.json()["detail"])
                upload_file.assert_not_called()
                self.assertEqual(0, await Artifact.objects.acount())


class GetOfEvaluationTestCase(TwoEvaluationsWithOneRunEach):

    async def test_a_run_is_found_through_its_evaluation(self):
        repository = EvaluationPluginRepository()
        found = await repository.get_of_evaluation(self.run.pid, self.evaluation.pid)
        self.assertEqual(self.run.pid, found.pid)

        related = await repository.get_of_evaluation(self.run.pid, self.evaluation.pid, with_related=True)
        self.assertEqual("DemoPlugin", related.plugin_config.plugin.name)

    async def test_a_run_of_another_evaluation_is_not_found(self):
        repository = EvaluationPluginRepository()
        with self.assertRaises(EvaluationPlugin.DoesNotExist):
            await repository.get_of_evaluation(self.other_run.pid, self.evaluation.pid)
        with self.assertRaises(EvaluationPlugin.DoesNotExist):
            await repository.get_of_evaluation(uuid.uuid4(), self.evaluation.pid)
