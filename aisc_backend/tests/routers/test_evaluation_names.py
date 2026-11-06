"""A run can be given a name: when it is launched, and afterwards (renamed). The results dashboard shows it in
place of the automatic name (Run 16 · OpenAI back-end · 05 Nov 2026, 08:38), so runs compared side by side are
told apart by what they are. The name is kept on each plugin run of the evaluation (its existing name field,
empty until now). An empty name gives the automatic one back. Every rename is recorded in the audit log.
"""
import unittest.mock as mock
import uuid

from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend.auth import keycloak
from aisc_backend.models import Evaluation, EvaluationPlugin, Plugin, PluginConfig, Project, ProjectStatus
from aisc_backend.routers import evaluation as evaluation_routes
from aisc_plugin_interface import BaseEvaluationPlugin

SIGNED_IN = {"Authorization": "Bearer development"}
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
class RunNames(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Mijke", status=ProjectStatus.Ready)

    async def plugin(self):
        plugin = await Plugin.objects.acreate(name="StrongRejectPlugin", display_name="StrongREJECT (Mijke)",
                                              description="", package_name="aisc-plugin-strongreject-mijke",
                                              version="0.2.0", project=self.project, enabled=True)
        plugin.current_config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        await plugin.asave()
        return plugin

    async def launch(self, **extra):
        await self.plugin()
        no_errors = {"missing": [], "invalid": [], "ambiguous": []}
        with mock.patch.object(evaluation_routes.plugin_loader, "load_plugin", return_value=DeclaresNothing), \
             mock.patch.object(evaluation_routes, "validate_plugin_settings",
                               new=mock.AsyncMock(return_value=no_errors)), \
             mock.patch.object(evaluation_routes.celery_service, "run_evaluation",
                               new=mock.AsyncMock(return_value=mock.Mock(task_id=str(uuid.uuid4())))):
            return await evaluations.post("/task", headers=SIGNED_IN, json={
                "project_pid": str(self.project.pid),
                "plugins_to_run": [{"name": "StrongRejectPlugin",
                                    "package_name": "aisc-plugin-strongreject-mijke"}], **extra})

    async def names(self) -> list[str]:
        return [n async for n in EvaluationPlugin.objects.values_list("name", flat=True)]

    # -- at launch --------------------------------------------------------------------------------------

    async def test_a_run_launched_with_a_name_keeps_it(self):
        response = await self.launch(name="  Mijke on OpenAI, full set  ")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(await self.names(), ["Mijke on OpenAI, full set"])

    async def test_a_run_launched_without_a_name_has_none_as_before(self):
        response = await self.launch()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(await self.names(), [""])

    async def test_a_name_longer_than_the_field_is_refused_before_anything_is_stored(self):
        response = await self.launch(name="x" * 256)
        self.assertIn(response.status_code, (400, 422), response.content)
        self.assertFalse(await Evaluation.objects.aexists())

    # -- afterwards -------------------------------------------------------------------------------------

    async def launched(self):
        response = await self.launch()
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["pid"]

    async def rename(self, pid, name):
        with mock.patch.object(evaluation_routes, "log_action") as audit:
            response = await evaluations.patch(f"/{pid}/name", headers=SIGNED_IN, json={"name": name})
        return response, audit

    async def test_a_run_is_renamed_afterwards(self):
        pid = await self.launched()
        response, _ = await self.rename(pid, " Local back-end, 4 Nov ")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"pid": pid, "name": "Local back-end, 4 Nov"})
        self.assertEqual(await self.names(), ["Local back-end, 4 Nov"])

    async def test_an_empty_name_gives_the_automatic_one_back(self):
        pid = await self.launched()
        await self.rename(pid, "first")
        response, _ = await self.rename(pid, "   ")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(await self.names(), [""])

    async def test_every_rename_is_in_the_audit_log(self):
        pid = await self.launched()
        _, audit = await self.rename(pid, "Local")
        audit.assert_called_once()
        kwargs = audit.call_args.kwargs
        self.assertEqual((kwargs["action"], kwargs["resource_type"], kwargs["resource_id"]),
                         ("rename", "evaluation", pid))
        self.assertEqual(kwargs["metadata"]["name"], "Local")

    async def test_renaming_a_run_that_does_not_exist_is_404(self):
        response, audit = await self.rename(uuid.uuid4(), "x")
        self.assertEqual(response.status_code, 404, response.content)
        audit.assert_not_called()

    async def test_a_rename_longer_than_the_field_is_refused(self):
        pid = await self.launched()
        response, _ = await self.rename(pid, "x" * 256)
        self.assertIn(response.status_code, (400, 422), response.content)
        self.assertEqual(await self.names(), [""])


class RunNameInTheList(TestCase):
    """The runs list (GET /projects/{pid}/evaluations) carries each plugin run's name as run_name; its `name`
    stays the plugin's, as the webapp reads it."""

    async def test_the_list_carries_the_run_name_next_to_the_plugin_name(self):
        from aisc_backend.models import EvaluationStatus
        from aisc_backend.routers.project import router as project_router

        project = await Project.objects.acreate(name="p", status=ProjectStatus.Ready)
        plugin = await Plugin.objects.acreate(name="StrongRejectPlugin", display_name="StrongREJECT (Mijke)",
                                              description="", package_name="pkg", version="0.2.0", project=project)
        config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        evaluation = await Evaluation.objects.acreate(status=EvaluationStatus.Pending, project=project)
        await EvaluationPlugin.objects.acreate(plugin_config=config, evaluation=evaluation, name="Local, 4 Nov")
        response = await TestAsyncClient(project_router).get(f"/{project.pid}/evaluations",
                                                              params={"status": "Pending"})
        self.assertEqual(response.status_code, 200)
        out = response.data[0]["evaluation_plugins"][0]
        self.assertEqual((out["name"], out["run_name"]), ("StrongRejectPlugin", "Local, 4 Nov"))
