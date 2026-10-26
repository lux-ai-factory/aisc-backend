"""A plugin's stored failure text keeps no secret: the rows already stored (aisc_backend.secret_cleanup.scrub_failures,
the failure step of `manage.py clean_old_secrets apply`) and every new one the worker reports."""
import importlib
import os
from unittest import mock

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import Evaluation, EvaluationPlugin, EvaluationStatus, Plugin, PluginConfig, Project
from aisc_backend.tests import test_ledger_events as ledger_events

LEAKED = "Plugin failed: openai.AuthenticationError: Incorrect API key provided: sk-proj-abcdefghijklmnopqrstuv"
SCRUBBED = "Plugin failed: openai.AuthenticationError: Incorrect API key provided: [redacted]"


def cleanup():
    return importlib.import_module("aisc_backend.secret_cleanup")


class StoredFailures(TestCase):
    def setUp(self):
        project = Project.objects.create(name="p", description="")
        plugin = Plugin.objects.create(project=project, name="LangBiTe", display_name="LangBiTe",
                                       package_name="aisc-plugin-langbite", version="0.2.8")
        config = PluginConfig.objects.create(plugin=plugin, config={})
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Failed, project=project)
        self.e1, self.e2, self.e3 = (
            EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=config, error_message=text)
            for text in (LEAKED, "", "timeout after 30 s"))

    def text(self, row):
        return EvaluationPlugin.objects.get(pk=row.pk).error_message

    def test_a_key_in_a_stored_failure_is_redacted_and_the_rest_stays(self):
        cleanup().scrub_failures(EvaluationPlugin, "default", apply=True)
        self.assertEqual(self.text(self.e1), SCRUBBED)
        self.assertEqual(self.text(self.e2), "")
        self.assertEqual(self.text(self.e3), "timeout after 30 s")
        self.assertEqual(cleanup().scrub_failures(EvaluationPlugin, "default", apply=True), (0, []))

    def test_count_names_the_plugin_and_changes_nothing(self):
        self.assertEqual(cleanup().scrub_failures(EvaluationPlugin, "default", apply=False),
                         (1, ["aisc-plugin-langbite"]))
        self.assertEqual(self.text(self.e1), LEAKED)


class StoredFailuresUseTheTextRule(TestCase):
    """Failure texts are records, not inputs: they keep using the text rule, prefixed names included, and leave
    ordinary prose alone."""

    TEXTS = {
        "judge rejected judge_api_key=lb-judge-0123456789abcdef": "judge rejected judge_api_key=[redacted]",
        "env OPENAI_API_KEY=abcd1234efgh5678ijkl DB_PASSWORD=hunter22xyz": "env OPENAI_API_KEY=[redacted] DB_PASSWORD=[redacted]",
        "proxy refused Authorization: Token abcdef0123456789": "proxy refused Authorization: [redacted]",
        "She was the bearer of bad news; password: it must contain a digit": None,
    }

    def setUp(self):
        project = Project.objects.create(name="p", description="")
        plugin = Plugin.objects.create(project=project, name="A", display_name="A", package_name="aisc-plugin-a",
                                       version="0.1.0")
        config = PluginConfig.objects.create(plugin=plugin, config={})
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Failed, project=project)
        self.rows = {text: EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=config,
                                                           error_message=text) for text in self.TEXTS}

    def test_apply_scrubs_prefixed_names_and_headers_and_leaves_prose(self):
        self.assertEqual(cleanup().scrub_failures(EvaluationPlugin, "default", apply=True),
                         (3, ["aisc-plugin-a"] * 3))
        for text, expected in self.TEXTS.items():
            with self.subTest(text=text[:20]):
                self.assertEqual(EvaluationPlugin.objects.get(pk=self.rows[text].pk).error_message,
                                 expected or text)


class ReportedFailures(ledger_events.Recording):
    """The worker's PATCH .../fail, set up as test_ledger_events.Runs sets it up."""

    def setUp(self):
        super().setUp()
        with ledger_events.unrecorded():
            plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
                                           version="0.1.0", project=self.project)
            config = PluginConfig.objects.create(plugin=plugin, config={})
            self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=self.project)
            self.evaluation_plugin = EvaluationPlugin.objects.create(evaluation=self.evaluation,
                                                                     plugin_config=config)
        from aisc_backend.routers.internal import router

        self.internal = TestAsyncClient(router)
        patcher = mock.patch.dict(os.environ, {"INTERNAL_API_KEY": ledger_events.INTERNAL_SECRET})
        patcher.start()
        self.addCleanup(patcher.stop)

    _start = ledger_events.Runs._start
    _as_the_worker = ledger_events.Runs._as_the_worker

    async def test_the_route_stores_the_failure_text_scrubbed(self):
        await self._start()
        res = await self._as_the_worker("patch", f"/evaluations/{self.evaluation.pid}/plugins/"
                                                 f"{self.evaluation_plugin.pid}/fail",
                                        json={"error_message": "boom Bearer abcDEF0123456789ghi"})
        self.assertEqual(res.status_code, 200, res.text)
        stored = await EvaluationPlugin.objects.aget(pk=self.evaluation_plugin.pk)
        self.assertEqual(stored.error_message, "boom [redacted]")
