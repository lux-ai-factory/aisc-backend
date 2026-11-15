"""A launch whose endpoints cannot be named is refused before the run is stored, in the platform's words.

When the ledger records the start of a run, the start names the endpoint each target or connection of the run
would reach, as the platform fingerprints it. The engine asks the platform before it writes the evaluation, so a
refused launch leaves no run behind (no Pending run the person must clear by hand). The refusal says why: the
platform's own reason when it refused a reference (a deleted connection, an unknown target), and a 503 that asks
to try again when the platform cannot be reached. No key or endpoint is ever part of the message.
"""
import io
import json
import os
from unittest import mock
from urllib import error

from aisc_plugin_interface import BaseEvaluationPlugin
from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend import ledger, platform_helpers, projectdb
from aisc_backend.auth import keycloak
from aisc_backend.models import (
    AIComponent,
    AISystem,
    Evaluation,
    EvaluationPlugin,
    LedgerOutbox,
    Plugin,
    PluginConfig,
    Project,
    ProjectStatus,
)
from aisc_backend.routers import evaluation as evaluation_routes
from aisc_backend.tests.stored_manifest import as_declared

PID = "0f0e0d0c-0b0a-4908-8706-050403020188"
REQUEST = "11111111-2222-4333-8444-555555555588"
ENGINE_TOKEN = "engine-token-never-shown"
PLATFORM = {"PLATFORM_URL": "http://platform:8000", "PLATFORM_ENGINE_TOKEN": ENGINE_TOKEN,
            "LEDGER_MODE": "record"}
SIGNED_IN = {"Authorization": "Bearer development"}
TASK = "00000000-0000-4000-8000-000000000008"
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


def platform_refuses(status: int, detail: str):
    """urlopen as the platform answering `status` with {"detail": detail}."""
    def answer(req, timeout=None):
        raise error.HTTPError(req.full_url, status, "refused", {}, io.BytesIO(json.dumps({"detail": detail}).encode()))
    return answer


def platform_unreachable(req, timeout=None):
    raise error.URLError(f"connection refused by {req.full_url} with token {ENGINE_TOKEN}")


def platform_names(connections):
    """urlopen as the platform fingerprinting the references."""
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.status = 200
    response.read.return_value = json.dumps({"connections": connections}).encode()
    return mock.Mock(return_value=response)


@override_settings(AISC_DEPLOYMENT="configurator")
class ARefusedLaunch(TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, PLATFORM)
        patcher.start()
        self.addCleanup(patcher.stop)
        pid, request = projectdb.admitted_pid.set(PID), ledger.current_request.set(REQUEST)
        self.addCleanup(projectdb.admitted_pid.reset, pid)
        self.addCleanup(ledger.current_request.reset, request)
        with mock.patch.dict(os.environ, {"LEDGER_MODE": "off"}):
            self.project = Project.objects.create(name="p", status=ProjectStatus.Ready, platform_project_id=PID)
            system = AISystem.objects.create(project=self.project, name="sys", description="")
            self.target = AIComponent.objects.create(system=system, name="Target", component_type="resource",
                                                     json_value={"value": "connection:system-chat"})
            plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", description="",
                                           package_name="demo-pkg", version="0.1", project=self.project,
                                           enabled=True)
            plugin.current_config = PluginConfig.objects.create(plugin=plugin, config={})
            plugin.save()

    async def launch(self, urlopen):
        no_errors = {"missing": [], "invalid": [], "ambiguous": []}
        send = mock.Mock(return_value=mock.Mock(task_id=TASK))
        with as_declared(DeclaresNothing), \
             mock.patch.object(evaluation_routes, "validate_plugin_settings",
                               new=mock.AsyncMock(return_value=no_errors)), \
             mock.patch.object(evaluation_routes, "_refuse_what_was_not_declared", new=mock.AsyncMock()), \
             mock.patch.object(platform_helpers.urlrequest, "urlopen", new=urlopen), \
             mock.patch.object(evaluation_routes.celery_service.celery, "send_task", new=send):
            response = await evaluations.post("/task", headers=SIGNED_IN, json={
                "project_pid": str(self.project.pid),
                "plugins_to_run": [{"name": "DemoPlugin",
                                    "inputs": [{"name": "target", "pid": str(self.target.pid)}]}]})
        return response, send

    async def assert_nothing_left(self, send):
        send.assert_not_called()
        self.assertEqual(await Evaluation.objects.acount(), 0, "a refused launch leaves no Pending run")
        self.assertEqual(await EvaluationPlugin.objects.acount(), 0)
        self.assertEqual(await LedgerOutbox.objects.acount(), 0)

    async def test_a_deleted_connection_is_named_and_no_run_is_left(self):
        response, send = await self.launch(platform_refuses(410, "connection 'system-chat' was deleted"))
        self.assertEqual(response.status_code, 422, response.content)
        self.assertIn("connection 'system-chat' was deleted", response.json()["detail"])
        await self.assert_nothing_left(send)

    async def test_an_unknown_target_is_named_and_no_run_is_left(self):
        response, send = await self.launch(platform_refuses(404, "no target 'chatbot'"))
        self.assertEqual(response.status_code, 422, response.content)
        self.assertIn("no target 'chatbot'", response.json()["detail"])
        await self.assert_nothing_left(send)

    async def test_an_unreachable_platform_asks_to_try_again_and_no_run_is_left(self):
        response, send = await self.launch(platform_unreachable)
        self.assertEqual(response.status_code, 503, response.content)
        detail = response.json()["detail"]
        self.assertIn("try again", detail)
        self.assertNotIn(ENGINE_TOKEN, detail)
        self.assertNotIn("platform:8000", detail)
        await self.assert_nothing_left(send)

    async def test_a_platform_error_is_not_passed_on_as_a_reason(self):
        response, send = await self.launch(platform_refuses(500, f"traceback with {ENGINE_TOKEN}"))
        self.assertEqual(response.status_code, 503, response.content)
        self.assertIn("try again", response.json()["detail"])
        self.assertNotIn(ENGINE_TOKEN, response.json()["detail"])
        await self.assert_nothing_left(send)

    async def test_named_endpoints_start_the_run_and_the_platform_is_asked_once(self):
        fingerprint = {"ref": "connection:system-chat", "connection": "system-chat", "kind": "rest",
                       "endpoint_sha256": "cd" * 32}
        urlopen = platform_names([fingerprint])
        response, send = await self.launch(urlopen)
        self.assertEqual(response.status_code, 200, response.content)
        send.assert_called_once()
        self.assertEqual(urlopen.call_count, 1)
        [start] = [row.event async for row in LedgerOutbox.objects.all()
                   if row.event["action"] == "engine.evaluation.run_requested"]
        self.assertEqual(start["details"]["connections"], [fingerprint])
