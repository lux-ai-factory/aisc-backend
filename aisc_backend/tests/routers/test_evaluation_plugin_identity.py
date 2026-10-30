"""A run names each plugin by its package and its name: two packages may ship a plugin of the same name (a
copy of a test, such as LangBiTe and LangBiTe (Mijke), both LangBiteEvaluationPlugin).

POST /evaluations/task runs the plugin of the named package. A request naming only the plugin (as before) is
accepted when one enabled plugin has that name, and refused when several have it, so the engine never guesses
which one to run. Every refused launch is logged with its reason, since the page is the only other place
that shows it.
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
class PluginIdentity(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Mijke", status=ProjectStatus.Ready)

    async def plugin(self, package, display, name="LangBiteEvaluationPlugin", configured=True):
        plugin = await Plugin.objects.acreate(name=name, display_name=display, description="",
                                              package_name=package, version="0.1", project=self.project,
                                              enabled=True)
        if configured:
            plugin.current_config = await PluginConfig.objects.acreate(plugin=plugin, config={})
            await plugin.asave()
        return plugin

    async def launch(self, *plugins_to_run):
        no_errors = {"missing": [], "invalid": [], "ambiguous": []}
        with mock.patch.object(evaluation_routes.plugin_loader, "load_plugin", return_value=DeclaresNothing), \
             mock.patch.object(evaluation_routes, "validate_plugin_settings",
                               new=mock.AsyncMock(return_value=no_errors)), \
             mock.patch.object(evaluation_routes.celery_service, "run_evaluation",
                               new=mock.AsyncMock(return_value=mock.Mock(task_id=str(uuid.uuid4())))):
            return await evaluations.post("/task", headers=SIGNED_IN,
                                          json={"project_pid": str(self.project.pid),
                                                "plugins_to_run": list(plugins_to_run)})

    async def ran(self) -> list[str]:
        return [p async for p in EvaluationPlugin.objects.values_list(
            "plugin_config__plugin__package_name", flat=True)]

    async def test_the_named_package_runs_when_two_plugins_share_a_name(self):
        await self.plugin("aisc-plugin-langbite", "LangBiTe", configured=False)
        await self.plugin("aisc-plugin-langbite-mijke", "LangBiTe (Mijke)")
        response = await self.launch({"name": "LangBiteEvaluationPlugin", "package_name": "aisc-plugin-langbite-mijke"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(await self.ran(), ["aisc-plugin-langbite-mijke"])

    async def test_a_shared_name_alone_is_refused_and_names_the_packages(self):
        await self.plugin("aisc-plugin-langbite", "LangBiTe")
        await self.plugin("aisc-plugin-langbite-mijke", "LangBiTe (Mijke)")
        response = await self.launch({"name": "LangBiteEvaluationPlugin"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["detail"],
                         "Several plugins are named LangBiteEvaluationPlugin (aisc-plugin-langbite,"
                         " aisc-plugin-langbite-mijke): the run must name the package")
        self.assertEqual(await Evaluation.objects.acount(), 0)

    async def test_a_unique_name_alone_still_runs(self):
        await self.plugin("aisc-plugin-strongreject-mijke", "StrongREJECT (Mijke)", name="StrongRejectPlugin")
        response = await self.launch({"name": "StrongRejectPlugin"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(await self.ran(), ["aisc-plugin-strongreject-mijke"])

    async def test_a_package_that_has_no_plugin_of_that_name_is_refused(self):
        await self.plugin("aisc-plugin-langbite", "LangBiTe")
        response = await self.launch({"name": "LangBiteEvaluationPlugin", "package_name": "aisc-plugin-other"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["detail"],
                         "Plugin LangBiteEvaluationPlugin of aisc-plugin-other is not enabled for this project")

    async def test_a_refused_launch_is_logged_with_its_reason(self):
        await self.plugin("aisc-plugin-langbite", "LangBiTe", configured=False)
        with self.assertLogs(evaluation_routes.logger, "WARNING") as logged:
            response = await self.launch({"name": "LangBiteEvaluationPlugin", "package_name": "aisc-plugin-langbite"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("launch refused (400): Plugin LangBiteEvaluationPlugin has no current config",
                      "\n".join(logged.output))
