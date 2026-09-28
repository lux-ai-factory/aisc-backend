from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import (
    AISystem,
    AIComponent,
    AIComponentType,
    Evaluation,
    EvaluationInput,
    EvaluationPlugin,
    EvaluationStatus,
    Plugin,
    PluginConfig,
    Project,
    ProjectStatus,
)
from aisc_backend.routers.project import router as project_router

client = TestAsyncClient(project_router)


class EvaluationInputsTemplateTestCase(TestCase):
    async def test_returns_latest_evaluation_inputs_per_plugin(self):
        project = await Project.objects.acreate(name="p", status=ProjectStatus.Ready)
        system = await AISystem.objects.acreate(project=project)
        dataset = await AIComponent.objects.acreate(
            name="Dataset A",
            description="",
            component_type=AIComponentType.DATASET,
            system=system,
        )
        plugin = await Plugin.objects.acreate(
            name="DemoPlugin",
            display_name="Demo",
            description="",
            package_name="demo-pkg",
            version="0.1.0",
            project=project,
        )
        config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        evaluation = await Evaluation.objects.acreate(
            status=EvaluationStatus.Pending, project=project
        )
        evaluation_plugin = await EvaluationPlugin.objects.acreate(
            plugin_config=config, evaluation=evaluation
        )
        await EvaluationInput.objects.acreate(
            evaluation_plugin=evaluation_plugin,
            name="dataset",
            component=dataset,
            value={"rows": 100},
        )

        response = await client.get(f"/{project.pid}/evaluation-inputs-template")

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            response.data,
            {
                "DemoPlugin": {
                    "dataset": {
                        "component_pid": str(dataset.pid),
                        "value": {"rows": 100},
                    }
                }
            },
        )

    async def test_returns_empty_when_no_evaluations(self):
        project = await Project.objects.acreate(name="p", status=ProjectStatus.Ready)
        response = await client.get(f"/{project.pid}/evaluation-inputs-template")
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.data, {})
