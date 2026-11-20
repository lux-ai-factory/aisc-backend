"""CORE-01 slice 1: one feature-flag mechanism for optional behaviour.

With every optional flag OFF (the default), the core paths still work: a project
can be created and an evaluation run. The optional routers are gated at request
time (aisc_backend.flags.require_flag), so the same build serves core-only.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend import flags
from aisc_evaluation.models import Evaluation, EvaluationStatus
from aisc_plugins.models import Plugin, PluginConfig
from aisc_backend.models import Project
from aisc_backend.routers.control import router as control_router
from aisc_backend.routers.evaluation import router as evaluation_router
from aisc_backend.routers.project import router as project_router

OPTIONAL_FLAGS = ("qualification", "objectives", "controls", "reporting")

project_client = TestAsyncClient(project_router)
evaluation_client = TestAsyncClient(evaluation_router)
control_client = TestAsyncClient(control_router)


@override_settings(
    FEATURE_QUALIFICATION=False,
    FEATURE_OBJECTIVES=False,
    FEATURE_CONTROLS=False,
    FEATURE_REPORTING=False,
)
class CoreWorksWithOptionalFeaturesOffTestCase(TestCase):

    async def test_core_project_and_evaluation_run_with_all_optional_flags_off(self):
        for name in OPTIONAL_FLAGS:
            self.assertFalse(flags.is_enabled(name))

        # Core: a project can be created; the caller becomes its owner.
        created = await project_client.post("", json={"name": "core-only"})
        self.assertEqual(200, created.status_code)
        pid = uuid.UUID(created.data["pid"])
        project = await Project.objects.aget(pid=pid)

        # An optional router is gated at request time: with the flag off its
        # routes do not exist, and the very same route answers once it is on
        # (one installed app graph, no URLconf change).
        gated = await control_client.get(f"/{pid}/controls")
        self.assertEqual(404, gated.status_code)
        with override_settings(FEATURE_CONTROLS=True):
            enabled = await control_client.get(f"/{pid}/controls")
        self.assertEqual(200, enabled.status_code)

        # Core: an evaluation can be run against the project. The plugin loader
        # and the celery dispatch are mocked, so this exercises the router + ORM
        # path only (no broker, no real plugin).
        plugin = await Plugin.objects.acreate(
            package_name="demo-pkg", version="0.1.0",
            display_name="Demo Plugin", project=project,
        )
        plugin.name = "DemoPlugin"
        plugin_config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        plugin.current_config = plugin_config
        await plugin.asave()

        with (
            patch(
                "aisc_backend.routers.plugin.plugin_loader.load_plugin",
                return_value=SimpleNamespace(project_config_definitions=[]),
            ),
            patch(
                "aisc_backend.services.celery_service.run_evaluation",
                new=AsyncMock(return_value=SimpleNamespace(task_id=str(uuid.uuid4()))),
            ),
        ):
            response = await evaluation_client.post(
                "/task",
                json={
                    "project_pid": str(pid),
                    "plugins_to_run": [{"name": "DemoPlugin", "inputs": []}],
                },
            )

        self.assertEqual(200, response.status_code)
        evaluation = await Evaluation.objects.aget(pid=uuid.UUID(response.data["pid"]))
        self.assertEqual(EvaluationStatus.Pending, evaluation.status)
