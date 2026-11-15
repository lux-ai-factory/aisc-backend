"""The start of a run says, in the ledger, what the run is made of: the card version it assesses, each
plugin's package, version, saved configuration and the hash of that configuration as the ledger may hold it,
and the endpoint each connection or target it reaches answered at that moment. A run whose endpoint cannot
be named is not started. The worker's events carry the run's card version too."""
from unittest import mock

from ninja.errors import HttpError

from aisc_backend import platform_helpers
from aisc_backend.config_secrets import masked_for_ledger
from aisc_backend.models import AIComponent, EvaluationInput
from aisc_backend.routers import target_input
from aisc_backend.tests.run_ledger_support import (CARD_VERSION, CONFIG, PID, RunRecording, canonical_sha256,
                                                   events, events_of, unrecorded)

FINGERPRINT = {"ref": f"target:{PID}/system", "connection": "system-chat", "kind": "rest",
               "endpoint_sha256": "ab" * 32}


class RunRequestedContent(RunRecording):
    async def _reaches_the_system(self):
        with unrecorded():
            target = await AIComponent.objects.acreate(
                system=self.system, name="Target", component_type="resource",
                json_value={"value": target_input.system_reference(PID)})
            await EvaluationInput.objects.acreate(evaluation_plugin=self.evaluation_plugin, name="target",
                                                  component=target, value={})

    async def test_run_requested_names_its_card_version(self):
        await self.start()
        [event] = await events_of("engine.evaluation.run_requested")
        self.assertEqual(event.get("card_version"), CARD_VERSION)

    async def test_run_requested_names_each_plugins_package_version_and_configuration(self):
        await self.start()
        [event] = await events_of("engine.evaluation.run_requested")
        self.assertEqual(event["details"]["plugins"], [{
            "name": "DemoPlugin", "package": "demo-pkg", "version": "0.1.0",
            "plugin_config_id": self.config.id, "config_sha256": canonical_sha256(masked_for_ledger(CONFIG))}])

    async def test_run_requested_names_the_connection_and_its_endpoint(self):
        await self._reaches_the_system()
        with mock.patch.object(platform_helpers, "connection_fingerprints", create=True,
                               return_value=[FINGERPRINT]) as asked:
            await self.start()
        [event] = await events_of("engine.evaluation.run_requested")
        self.assertEqual(event["details"].get("connections"), [FINGERPRINT])
        self.assertEqual(asked.call_args.args[0], PID)

    async def test_a_run_whose_endpoint_cannot_be_named_is_not_started(self):
        from aisc_backend.services import celery_service

        await self._reaches_the_system()
        send = mock.Mock(return_value=mock.Mock(task_id="00000000-0000-4000-8000-000000000009"))
        refused = None
        with mock.patch.object(platform_helpers, "connection_fingerprints", create=True,
                               side_effect=platform_helpers.HelpersUnavailable("the platform did not answer")), \
             mock.patch.object(celery_service.celery, "send_task", new=send):
            try:
                await celery_service.run_evaluation(self.evaluation.pid)
            except HttpError as exc:
                refused = exc
        send.assert_not_called()
        self.assertEqual(getattr(refused, "status_code", None), 503)
        self.assertEqual(await events(), [])

    async def test_the_workers_events_carry_the_runs_card_version(self):
        await self.start()
        res = await self.as_the_worker("put", f"/evaluations/{self.evaluation.pid}?status=Processing")
        self.assertEqual(res.status_code, 200, res.text)
        [changed] = await events_of("engine.evaluation.status_changed")
        self.assertEqual(changed.get("card_version"), CARD_VERSION)

    async def test_run_requested_names_the_settings_the_run_is_given_never_a_secrets_value(self):
        from aisc_identity.canonical import sha256_of

        from aisc_backend.models import PluginConfigProjectConfig, ProjectConfig, ProjectConfigCategory
        from aisc_backend.utils.encryption import encrypt_value

        with unrecorded():
            variable = await ProjectConfig.objects.acreate(project=self.project, key="LANGUAGE",
                                                           category=ProjectConfigCategory.VARIABLES,
                                                           json_value={"value": "fr"})
            secret = await ProjectConfig.objects.acreate(project=self.project, key="JUDGE_KEY",
                                                         category=ProjectConfigCategory.SECRETS,
                                                         encrypted_value=encrypt_value("hunter2-not-a-shape"),
                                                         masked_value="hunt...hape")
            for setting, key in ((variable, "language"), (secret, "config.api_key")):
                await PluginConfigProjectConfig.objects.acreate(plugin_config=self.config, project_config=setting,
                                                                plugin_config_key=key)
        await self.start()
        [event] = await events_of("engine.evaluation.run_requested")
        self.assertEqual(event["details"]["plugins"][0].get("settings"), [
            {"key": "config.api_key", "setting": "JUDGE_KEY", "category": "secrets"},
            {"key": "language", "setting": "LANGUAGE", "category": "variables",
             "value_sha256": sha256_of({"value": "fr"})}])
        self.assertNotIn("hunter2", str(event))
        self.assertNotIn("hunt...hape", str(event))
