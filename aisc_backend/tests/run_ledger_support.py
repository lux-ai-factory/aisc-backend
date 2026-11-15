"""A run as the ledger tests of its contents see it: the ledger on, a platform project admitted, the
gateway's request witnessed, one plugin with a saved configuration, an evaluation of one card version and
its one test. Not a test module: the test modules subclass `RunRecording`."""
import hashlib
import json
import os
from unittest import mock

from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend import ledger, projectdb
from aisc_backend.models import LedgerOutbox, Project, ProjectStatus
from aisc_backend.models.ai_system import AISystem

PID = "0f0e0d0c-0b0a-4908-8706-050403020177"
REQUEST = "11111111-2222-4333-8444-555555555577"
CARD_VERSION = "7a7a7a7a-1b1b-4c4c-8d8d-9e9e9e9e9e9e"
INTERNAL_SECRET = "test-internal-secret"
CONFIG = {"temperature": 0.25, "rounds": 3}


def canonical_sha256(value) -> str:
    """The sha256 of the canonical JSON of `value` (RFC 8785 for the plain values these tests use)."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


async def events() -> list[dict]:
    return [row.event async for row in LedgerOutbox.objects.order_by("id")]


async def events_of(action: str) -> list[dict]:
    return [event for event in await events() if event["action"] == action]


def unrecorded():
    return mock.patch.dict(os.environ, {"LEDGER_MODE": "off"})


class RunRecording(TestCase):
    def setUp(self):
        super().setUp()
        from aisc_backend.models import Evaluation, EvaluationPlugin, EvaluationStatus, Plugin, PluginConfig

        inside = override_settings(AISC_DEPLOYMENT="configurator")
        inside.enable()
        self.addCleanup(inside.disable)
        patcher = mock.patch.dict(os.environ, {"LEDGER_MODE": "record", "INTERNAL_API_KEY": INTERNAL_SECRET})
        patcher.start()
        self.addCleanup(patcher.stop)
        pid, request = projectdb.admitted_pid.set(PID), ledger.current_request.set(REQUEST)
        self.addCleanup(projectdb.admitted_pid.reset, pid)
        self.addCleanup(ledger.current_request.reset, request)
        with unrecorded():
            self.project = Project.objects.create(name="p", status=ProjectStatus.Ready, platform_project_id=PID)
            self.system = AISystem.objects.create(project=self.project, name="sys", description="")
            self.plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
                                                version="0.1.0", project=self.project)
            self.config = PluginConfig.objects.create(plugin=self.plugin, config=dict(CONFIG))
            self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=self.project,
                                                        system_id=CARD_VERSION)
            self.evaluation_plugin = EvaluationPlugin.objects.create(evaluation=self.evaluation,
                                                                     plugin_config=self.config)
        self.internal = TestAsyncClient(__import__("aisc_backend.routers.internal", fromlist=["router"]).router)

    async def start(self):
        """Start the run as the launch route does; returns the mocked send_task."""
        from aisc_backend.services import celery_service

        send = mock.Mock(return_value=mock.Mock(task_id="00000000-0000-4000-8000-000000000009"))
        with mock.patch.object(celery_service.celery, "send_task", new=send):
            await celery_service.run_evaluation(self.evaluation.pid)
        return send

    async def as_the_worker(self, method, path, **kw):
        token = ledger.current_request.set(None)
        try:
            return await getattr(self.internal, method)(path, headers={"X-Internal-Secret": INTERNAL_SECRET}, **kw)
        finally:
            ledger.current_request.reset(token)
