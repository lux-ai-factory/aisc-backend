"""
Integration test over the current project/component/evaluation API.

The legacy "DataShape" feature (models, schemas, routers) was removed; a
datashape is now an AIComponent of type "datashape" whose document lives in
json_value, linked to its source dataset via the source_dataset FK.

Covers the full lifecycle end-to-end through the real routers: project ->
dataset/model components -> file upload -> derived datashape component ->
evaluation -> measures -> done. External services are mocked in the test client
helpers (S3) or inline (plugin loader + celery dispatch); immudb is mocked
suite-wide in aisc_backend/tests/__init__.py.
"""
import unittest.mock as mock
import uuid
from types import SimpleNamespace

from django.test import TestCase

from aisc_backend.models import EvaluationStatus, Plugin, PluginConfig
from aisc_backend.models.project import Project
from aisc_backend.tests.integration import (
    project_test_client,
    dataset_test_client,
    model_test_client,
    evaluation_test_client,
)
from aisc_backend.tests.utils import create_measure_in_schema_list_for_model_eval

project_name = 'test'

training_dataset_name = 'training-dataset'
testing_dataset_name = 'testing-dataset'
model_name = 'model'

batch_count = 10
day_int = 90


class IntegrationTestCase(TestCase):

    async def test_integration(self):
        # Create Project
        project = await project_test_client.create_project(project_name)
        self.assertIsNotNone(project)
        self.assertEqual(project.name, project_name)

        # Get projects list
        projects = await project_test_client.get_projects()
        self.assertIsNotNone(projects)
        self.assertEqual(len(projects), 1)

        # Rename project
        project = await project_test_client.patch_project(project.pid, f"{project_name}-renamed")
        self.assertIsNotNone(project)
        self.assertEqual(project.name, f"{project_name}-renamed")

        # Create training + testing dataset components
        training_dataset = await project_test_client.create_project_dataset(project.pid, training_dataset_name)
        self.assertIsNotNone(training_dataset)
        self.assertEqual(training_dataset.name, training_dataset_name)
        self.assertEqual(training_dataset.component_type, "dataset")

        testing_dataset = await project_test_client.create_project_dataset(project.pid, testing_dataset_name)
        self.assertIsNotNone(testing_dataset)
        self.assertEqual(testing_dataset.name, testing_dataset_name)

        # Create model component
        model = await project_test_client.create_project_model(project.pid, model_name, training_dataset.pid)
        self.assertIsNotNone(model)
        self.assertEqual(model.name, model_name)
        self.assertEqual(model.component_type, "model")

        # Upload a file to the training dataset component (S3 mocked in the client)
        upload_dataset_file_result = await dataset_test_client.upload_dataset_file(training_dataset.pid)
        self.assertIsNotNone(upload_dataset_file_result.file_name)

        # Upload a model file (S3 mocked in the client)
        upload_model_file_result = await model_test_client.upload_model_file(model.pid)
        self.assertIsNotNone(upload_model_file_result.file_name)

        # Create a datashape component derived from the training dataset
        datashape = await project_test_client.create_project_datashape(project.pid, training_dataset.pid)
        self.assertIsNotNone(datashape)
        self.assertEqual(datashape.component_type, "datashape")
        self.assertEqual(datashape.source_dataset_pid, training_dataset.pid)

        # Project details expose all created components
        project_details = await project_test_client.get_project_details(project.pid)
        component_names = [c.name for c in project_details.components]
        self.assertIn(training_dataset_name, component_names)
        self.assertIn(testing_dataset_name, component_names)
        self.assertIn(model_name, component_names)
        self.assertIn("datashape", component_names)

        # Get project by name
        fetched = await project_test_client.get_project_by_name(f"{project_name}-renamed")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.pid, project.pid)

        # Enable a plugin so an evaluation can be created (no real plugin infra needed:
        # the loader + dispatch are mocked; the ORM rows are what the router validates).
        project_model = await Project.objects.aget(pid=project.pid)
        plugin = await Plugin.objects.acreate(
            package_name="demo-pkg", version="0.1.0",
            display_name="Demo Plugin", project=project_model,
        )
        plugin.name = "DemoPlugin"
        plugin_config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        plugin.current_config = plugin_config
        await plugin.asave()

        with (
            mock.patch(
                "aisc_backend.routers.plugin.plugin_loader.load_plugin",
                return_value=SimpleNamespace(project_config_definitions=[]),
            ),
            mock.patch(
                "aisc_backend.services.celery_service.run_evaluation",
                new=mock.AsyncMock(return_value=SimpleNamespace(task_id=str(uuid.uuid4()))),
            ),
        ):
            evaluation = await evaluation_test_client.create_evaluation(
                project.pid,
                [{"name": "DemoPlugin", "inputs": [{"pid": training_dataset.pid, "name": "training_dataset"}]}],
            )
        self.assertIsNotNone(evaluation)
        self.assertEqual(evaluation.status, EvaluationStatus.Pending)

        # a4s-eval gets Pending evaluations
        evaluations = await evaluation_test_client.get_evaluations_by_status(EvaluationStatus.Pending)
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(evaluations[0].evaluation_pid, evaluation.pid)

        # a4s-eval claims the evaluation by moving it to Processing
        evaluation_status = await evaluation_test_client.update_evaluation_status(
            evaluation.pid, EvaluationStatus.Processing
        )
        self.assertEqual(EvaluationStatus.Processing, evaluation_status)

        # a4s-eval gets evaluation details (project + plugin + inputs)
        evaluation_detail = await evaluation_test_client.get_evaluation_including(
            evaluation.pid, "project,plugin"
        )
        self.assertIsNotNone(evaluation_detail)
        self.assertIsNotNone(evaluation_detail.evaluation_plugins)
        evaluation_plugin = evaluation_detail.evaluation_plugins[0]

        # a4s-eval posts dummy measures for the evaluation run
        measures = create_measure_in_schema_list_for_model_eval(batch_count, day_int)
        create_evaluation_measures_response = await evaluation_test_client.create_evaluation_measures(
            evaluation.pid,
            {evaluation_plugin["pid"]: measures},
        )
        self.assertEqual(201, create_evaluation_measures_response.status_code)

        # a4s-eval marks the evaluation done
        evaluation_status = await evaluation_test_client.update_evaluation_status(
            evaluation.pid, EvaluationStatus.Done
        )
        self.assertEqual(EvaluationStatus.Done, evaluation_status)

        # Get done evaluations
        evaluations = await evaluation_test_client.get_evaluations_by_status(EvaluationStatus.Done)
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(evaluations[0].evaluation_pid, evaluation.pid)

        # The recorded metric names are queryable via the measurements API
        metric_names = await evaluation_test_client.get_evaluation_metric_names(evaluation.pid)
        self.assertEqual(set(metric_names), {"Accuracy", "F1", "Precision", "Recall", "MCC", "ROCAUC"})
