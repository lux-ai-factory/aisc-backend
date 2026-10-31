"""What POST /evaluations/task writes, and when.

Every input component is looked up before anything is stored, so a launch naming a component that does not
exist is refused with nothing left behind. Once the worker is sent, the route saves only the task id: the
worker may already have moved the evaluation on, and the route's copy of the row is the one it created.
"""
import uuid
from unittest import mock

from aisc_plugin_interface import BaseEvaluationPlugin
from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend.auth import keycloak
from aisc_backend.models import (
    AIComponent,
    AIComponentType,
    AISystem,
    Evaluation,
    EvaluationInput,
    EvaluationPlugin,
    EvaluationStatus,
    Plugin,
    PluginConfig,
    Project,
    ProjectStatus,
)
from aisc_backend.routers import evaluation as evaluation_routes

SIGNED_IN = {"Authorization": "Bearer development"}
TASK = "00000000-0000-4000-8000-000000000007"
evaluations = TestAsyncClient(evaluation_routes.router)

_auth_off = None


def setUpModule():
    global _auth_off
    _auth_off = mock.patch.object(keycloak, "AUTH_ENABLED", False)
    _auth_off.start()


def tearDownModule():
    _auth_off.stop()


class DeclaresNothing(BaseEvaluationPlugin):
    pass


@override_settings(AISC_DEPLOYMENT="standalone")
class LaunchWrites(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        system = AISystem.objects.create(project=self.project, name="sys", description="")
        self.dataset = AIComponent.objects.create(component_type=AIComponentType.DATASET, system=system,
                                                  name="data.csv", description="", data="")
        plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", description="",
                                       package_name="demo-pkg", version="0.1", project=self.project, enabled=True)
        plugin.current_config = PluginConfig.objects.create(plugin=plugin, config={})
        plugin.save()

    async def launch(self, component_pid, worker=None):
        no_errors = {"missing": [], "invalid": [], "ambiguous": []}

        async def send(evaluation_pid):
            if worker:
                await worker(evaluation_pid)
            return mock.Mock(task_id=TASK)

        with mock.patch.object(evaluation_routes.plugin_loader, "load_plugin", return_value=DeclaresNothing), \
             mock.patch.object(evaluation_routes, "validate_plugin_settings",
                               new=mock.AsyncMock(return_value=no_errors)), \
             mock.patch.object(evaluation_routes.celery_service, "run_evaluation", new=send):
            return await evaluations.post("/task", headers=SIGNED_IN, json={
                "project_pid": str(self.project.pid),
                "plugins_to_run": [{"name": "DemoPlugin",
                                    "inputs": [{"name": "dataset", "pid": str(component_pid)}]}]})

    async def test_a_component_that_does_not_exist_is_refused_and_nothing_is_stored(self):
        response = await self.launch(uuid.uuid4())
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("not found", response.json()["detail"])
        self.assertEqual(await Evaluation.objects.acount(), 0)
        self.assertEqual(await EvaluationPlugin.objects.acount(), 0)
        self.assertEqual(await EvaluationInput.objects.acount(), 0)

    async def test_a_known_component_is_linked_to_the_run(self):
        response = await self.launch(self.dataset.pid)
        self.assertEqual(response.status_code, 200, response.content)
        evaluation_input = await EvaluationInput.objects.aget()
        self.assertEqual(evaluation_input.component_id, self.dataset.id)

    async def test_saving_the_task_id_keeps_the_status_the_worker_set(self):
        async def starts_at_once(evaluation_pid):
            await Evaluation.objects.filter(pid=evaluation_pid).aupdate(status=EvaluationStatus.Processing)

        response = await self.launch(self.dataset.pid, worker=starts_at_once)
        self.assertEqual(response.status_code, 200, response.content)
        evaluation = await Evaluation.objects.aget()
        self.assertEqual(str(evaluation.task), TASK)
        self.assertEqual(evaluation.status, EvaluationStatus.Processing)
