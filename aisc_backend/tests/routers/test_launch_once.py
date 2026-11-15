"""A launch makes one run, stored whole, and a run that cannot be sent is failed.

The page sends each click with its own Idempotency-Key: the same key again (a double click, a retried request)
answers with the run it already made and sends nothing more to the worker. A key the engine cannot use is refused
before anything is stored. When the worker cannot be reached, the run and each of its tests end Failed with the
reason, instead of staying Pending forever. A failure while the run's rows are written leaves none of them.
"""
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
class ALaunchMakesOneRun(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        system = AISystem.objects.create(project=self.project, name="sys", description="")
        self.dataset = AIComponent.objects.create(component_type=AIComponentType.DATASET, system=system,
                                                  name="data.csv", description="", data="")
        plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", description="",
                                       package_name="demo-pkg", version="0.1", project=self.project, enabled=True)
        plugin.current_config = PluginConfig.objects.create(plugin=plugin, config={})
        plugin.save()

    async def launch(self, component_pid, key=None, send=None):
        no_errors = {"missing": [], "invalid": [], "ambiguous": []}

        async def run_evaluation(evaluation_pid):
            if send:
                send(evaluation_pid)
            return mock.Mock(task_id=TASK)

        headers = dict(SIGNED_IN)
        if key is not None:
            headers["Idempotency-Key"] = key
        with mock.patch.object(evaluation_routes.plugin_loader, "load_plugin", return_value=DeclaresNothing), \
             mock.patch.object(evaluation_routes, "validate_plugin_settings",
                               new=mock.AsyncMock(return_value=no_errors)), \
             mock.patch.object(evaluation_routes.celery_service, "run_evaluation", new=run_evaluation):
            return await evaluations.post("/task", headers=headers, json={
                "project_pid": str(self.project.pid),
                "plugins_to_run": [{"name": "DemoPlugin",
                                    "inputs": [{"name": "dataset", "pid": str(component_pid)}]}]})

    async def test_a_launch_repeated_with_its_key_makes_one_run_and_sends_it_once(self):
        sent = []
        first = await self.launch(self.dataset.pid, key="k-1", send=sent.append)
        again = await self.launch(self.dataset.pid, key="k-1", send=sent.append)
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual(first.json()["pid"], again.json()["pid"])
        self.assertEqual(await Evaluation.objects.acount(), 1)
        self.assertEqual(len(sent), 1)

    async def test_two_launches_with_different_keys_make_two_runs(self):
        await self.launch(self.dataset.pid, key="k-1")
        await self.launch(self.dataset.pid, key="k-2")
        self.assertEqual(await Evaluation.objects.acount(), 2)

    async def test_a_malformed_key_is_refused_and_nothing_is_stored(self):
        for key in ("a b", "x" * 129):
            r = await self.launch(self.dataset.pid, key=key)
            self.assertEqual(r.status_code, 400, key)
        self.assertEqual(await Evaluation.objects.acount(), 0)

    async def test_a_run_that_cannot_be_sent_is_failed_with_the_reason(self):
        def broker_down(pid):
            raise ConnectionError("broker down")

        not_sent = getattr(evaluation_routes, "NOT_SENT", "<NOT_SENT undefined>")
        try:
            r = await self.launch(self.dataset.pid, send=broker_down)
            answer = (r.status_code, r.json().get("detail"))
        except ConnectionError:
            answer = ("raised ConnectionError", None)
        self.assertEqual(answer, (503, not_sent))
        e = await Evaluation.objects.aget()
        self.assertEqual(e.status, EvaluationStatus.Failed)
        tests = [(t.status, t.error_message, t.finished_at is not None) async for t in EvaluationPlugin.objects.all()]
        self.assertEqual(tests, [("Failed", not_sent, True)])

    async def test_a_failure_while_storing_the_run_leaves_nothing(self):
        with mock.patch.object(EvaluationInput, "asave", side_effect=RuntimeError("disk full")), \
             mock.patch.object(EvaluationInput, "save", side_effect=RuntimeError("disk full")), \
             self.assertRaises(RuntimeError):
            await self.launch(self.dataset.pid)
        self.assertEqual((await Evaluation.objects.acount(), await EvaluationPlugin.objects.acount()), (0, 0))

    def test_the_launch_key_header_is_allowed_from_the_pages_origin(self):
        from corsheaders.conf import conf
        self.assertIn("idempotency-key", [h.lower() for h in conf.CORS_ALLOW_HEADERS])
