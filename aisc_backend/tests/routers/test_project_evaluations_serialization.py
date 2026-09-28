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


class ProjectEvaluationsSerializationTestCase(TestCase):
    """Regression: serializing evaluation inputs must not hit `source_dataset`
    lazily in an async context (SynchronousOnlyOperation). The FK must be
    prefetched so `AIComponentOutSchema.source_dataset_pid` can resolve.
    """

    async def test_evaluations_response_with_derived_datashape_input(self):
        project = await Project.objects.acreate(name="p", status=ProjectStatus.Ready)
        system = await AISystem.objects.acreate(project=project)

        source = await AIComponent.objects.acreate(
            name="Source Dataset",
            description="",
            data="s3://bucket/source.parquet",
            component_type=AIComponentType.DATASET,
            system=system,
        )
        datashape = await AIComponent.objects.acreate(
            name="Derived Shape",
            description="",
            data="",
            component_type=AIComponentType.DATASHAPE,
            system=system,
            source_dataset=source,
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
            name="datashape",
            component=datashape,
            value={},
        )

        response = await client.get(f"/{project.pid}/evaluations", params={"status": "Pending"})

        self.assertEqual(200, response.status_code)
        self.assertEqual(len(response.data), 1)

        evaluation_plugin_out = response.data[0]["evaluation_plugins"][0]
        input_file = evaluation_plugin_out["evaluation_inputs"][0]["input_file"]
        self.assertEqual(input_file["source_dataset_pid"], str(source.pid))
        self.assertEqual(input_file["pid"], str(datashape.pid))
