"""Saving a plugin configuration never stores a key in clear (POST /api/v1/plugins/{pid}/config).

A key field is taken out of the stored configuration and kept as a project secret owned through a `config.<path>`
mapping; the form shows it as a password input that keeps the key while empty. A plugin that adopted @assesses
takes no key at all. Run as test_ledger_events.py runs the routes: the ledger on, the project admitted, the
request witnessed.
"""
import importlib
import json
import logging
import os
from unittest import mock

from ninja.testing import TestAsyncClient

from aisc_backend import ledger
from aisc_backend.models import (Evaluation, EvaluationPlugin, EvaluationStatus, Plugin, PluginConfig,
                                 PluginConfigProjectConfig, ProjectConfig, ProjectConfigCategory)
from aisc_backend.tests.test_ledger_events import INTERNAL_SECRET, Recording, events, settings_client, unrecorded
from aisc_backend.utils.encryption import decrypt_value

CRED = "cred-0123456789abcdef"
LOAD_PLUGIN = "aisc_backend.routers.plugin.plugin_loader.load_plugin"


def rule():
    return importlib.import_module("aisc_backend.config_secret_fields")


class _OldPlugin:
    """A plugin that has not adopted @assesses and asks for a key in its form."""
    display_name = "Demo"
    project_config_definitions = []
    help_text = ""

    def get_full_schema(self):
        return {"properties": {"threshold": {"type": "number"}, "model_credential": {"type": "string"}}}, {}

    def on_config_change(self, config):
        return config, {}, {}


class _AdoptedPlugin(_OldPlugin):
    target_kinds = ("card",)


class _UnreadableSchemaPlugin(_OldPlugin):
    def get_full_schema(self):
        raise RuntimeError("the schema cannot be read")


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def hint(key):
    return {"ui:widget": "password",
            "ui:help": f"Kept as the project secret {key}. Leave this empty to keep it; type a new key to replace it."}


async def secrets() -> list[ProjectConfig]:
    return [row async for row in ProjectConfig.objects.filter(category=ProjectConfigCategory.SECRETS).order_by("id")]


async def mappings(config_id) -> dict:
    return {m.plugin_config_key: m.project_config_id
            async for m in PluginConfigProjectConfig.objects.filter(plugin_config_id=config_id)}


class SecretsAtSave(Recording):
    PACKAGE = {"package_name": "demo-pkg", "version": "0.1.0"}

    def setUp(self):
        super().setUp()
        from aisc_backend.routers.plugin import router

        self.plugins = TestAsyncClient(router)

    async def _install(self, plugin_class=_OldPlugin):
        with mock.patch("aisc_backend.routers.plugin.plugin_loader.load_package",
                        return_value={"DemoA": plugin_class(), "DemoB": plugin_class()}):
            res = await self.plugins.post("", json={**self.PACKAGE, "project_uuid": str(self.project.pid)})
        self.assertEqual(res.status_code, 200, res.text)
        return {row["name"]: row["pid"] for row in res.json()}

    async def _save(self, pid, config, plugin_class=_OldPlugin, **body):
        with mock.patch(LOAD_PLUGIN, return_value=plugin_class()):
            return await self.plugins.post(f"/{pid}/config", json={"config": config, **body})

    async def _saved(self, pid, config, plugin_class=_OldPlugin, **body):
        res = await self._save(pid, config, plugin_class, **body)
        self.assertEqual(res.status_code, 200, res.text)
        return res

    async def _first_save(self):
        """Case 8's save: the old plugin's form sent with its key."""
        self.pid = (await self._install())["DemoA"]
        return await self._saved(self.pid, {"threshold": 0.5, "model_credential": CRED})

    async def _only_secret(self):
        [secret] = await secrets()
        return secret

    async def _new_events(self, before):
        return (await events())[len(before):]

    # Saving

    async def test_a_key_is_kept_as_a_project_secret_and_never_stored_in_clear(self):
        records = _Records()
        root = logging.getLogger()
        level = root.level
        root.addHandler(records)
        root.setLevel(logging.DEBUG)
        try:
            res = await self._first_save()
        finally:
            root.removeHandler(records)
            root.setLevel(level)

        row = await PluginConfig.objects.aget(pk=res.json()["plugin_config_id"])
        self.assertEqual(row.config, {"threshold": 0.5})
        secret = await self._only_secret()
        self.assertEqual((secret.key, secret.name, secret.description),
                         ("demoa_model_credential", "Demo: model_credential", rule().SECRET_DESCRIPTION))
        self.assertEqual(decrypt_value(secret.encrypted_value), CRED)
        self.assertEqual(secret.masked_value, "cred...cdef")
        self.assertEqual(await mappings(row.id), {"config.model_credential": secret.id})

        body = res.json()
        self.assertNotIn("secret_fields", body)
        self.assertNotIn("model_credential", body["config"])
        self.assertEqual(body["uiSchema"]["model_credential"], {
            "ui:widget": "password",
            "ui:help": "Kept as the project secret demoa_model_credential. Leave this empty to keep it; "
                       "type a new key to replace it."})

        self.assertNotIn(CRED, json.dumps(await events()))
        self.assertNotIn(CRED, json.dumps([c.config async for c in PluginConfig.objects.all()]))
        self.assertNotIn(CRED, res.text)
        for record in records.records:
            self.assertNotIn(CRED, record.getMessage())

    async def test_the_save_records_the_new_secret_then_the_configuration(self):
        res = await self._first_save()
        secret = await self._only_secret()
        _install, created, configured = await events()
        self.assertEvent(created, "engine.secret.created", "secret", secret.pid, {"key": "demoa_model_credential"})
        self.assertEvent(configured, "engine.plugin.configured", "plugin", self.pid, {})
        self.assertEqual(configured["content"], {
            "config_id": res.json()["plugin_config_id"],
            "config": {"threshold": 0.5, "model_credential": "set"},
            "settings": {"config.model_credential": str(secret.pid)}})

    async def test_an_empty_or_absent_key_field_keeps_the_stored_key(self):
        await self._first_save()
        secret = await self._only_secret()
        before = await events()
        res = await self._saved(self.pid, {"threshold": 0.7, "model_credential": ""})
        [configured] = await self._new_events(before)
        self.assertEqual(configured["action"], "engine.plugin.configured")
        self.assertEqual(configured["content"]["config"], {"threshold": 0.7, "model_credential": "set"})
        self.assertEqual(await secrets(), [secret])
        row = await PluginConfig.objects.aget(pk=res.json()["plugin_config_id"])
        self.assertEqual(row.config, {"threshold": 0.7, "model_credential": ""})
        self.assertEqual(await mappings(row.id), {"config.model_credential": secret.id})
        self.assertEqual(res.json()["uiSchema"]["model_credential"], hint("demoa_model_credential"))

        res = await self._saved(self.pid, {"threshold": 0.8})
        row = await PluginConfig.objects.aget(pk=res.json()["plugin_config_id"])
        self.assertEqual(row.config, {"threshold": 0.8})
        self.assertEqual(await mappings(row.id), {"config.model_credential": secret.id})
        self.assertEqual(res.json()["uiSchema"]["model_credential"], hint("demoa_model_credential"))

    async def test_a_new_key_replaces_the_secrets_value(self):
        await self._first_save()
        secret = await self._only_secret()
        new = "cred-new-0123456789abc"
        before = await events()
        await self._saved(self.pid, {"threshold": 0.5, "model_credential": new})
        updated, configured = await self._new_events(before)
        self.assertEvent(updated, "engine.secret.updated", "secret", secret.pid,
                         {"key": "demoa_model_credential", "changed": ["value"]})
        self.assertEqual(configured["action"], "engine.plugin.configured")
        replaced = await self._only_secret()
        self.assertEqual((replaced.pid, replaced.key), (secret.pid, "demoa_model_credential"))
        self.assertEqual(decrypt_value(replaced.encrypted_value), new)

        before = await events()
        await self._saved(self.pid, {"threshold": 0.5, "model_credential": new})
        self.assertEqual([e["action"] for e in await self._new_events(before)], ["engine.plugin.configured"])

    async def _persons_secret(self):
        res = await settings_client.post(f"/{self.project.pid}", json={
            "category": "secrets", "key": "demoa_model_credential", "name": "Mine", "value": "persons-own-value-123"})
        self.assertEqual(res.status_code, 200, res.text)
        return await ProjectConfig.objects.aget(pid=res.json()["pid"])

    async def test_a_secret_the_person_made_under_the_same_key_is_never_touched(self):
        persons = await self._persons_secret()
        after_persons = await events()
        await self._first_save()
        moved = await ProjectConfig.objects.exclude(pk=persons.pk).aget(category=ProjectConfigCategory.SECRETS)
        self.assertEqual(moved.key, "demoa_model_credential_2")
        persons = await ProjectConfig.objects.aget(pk=persons.pk)
        self.assertEqual(decrypt_value(persons.encrypted_value), "persons-own-value-123")
        self.assertEqual([e for e in await self._new_events(after_persons) if e["item_id"] == str(persons.pid)], [])

    async def test_a_config_mapping_sent_by_the_client_is_ignored(self):
        persons = await self._persons_secret()
        await self._first_save()
        moved = await ProjectConfig.objects.exclude(pk=persons.pk).aget(category=ProjectConfigCategory.SECRETS)
        selection = [{"plugin_config_key": "config.model_credential", "project_config_pid": str(persons.pid)}]
        res = await self._saved(self.pid, {"threshold": 0.6}, project_config_selections=selection)
        self.assertEqual(await mappings(res.json()["plugin_config_id"]), {"config.model_credential": moved.id})

        other = await Plugin.objects.aget(name="DemoB")
        res = await self._saved(other.pid, {"threshold": 0.6}, project_config_selections=selection)
        self.assertEqual(await mappings(res.json()["plugin_config_id"]), {})

    async def test_reading_the_form_and_previewing_name_the_kept_fields(self):
        await self._first_save()
        with mock.patch(LOAD_PLUGIN, return_value=_OldPlugin()):
            res = await self.plugins.get(f"/{self.pid}/config/state")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["config"], {"threshold": 0.5})
        self.assertEqual(res.json()["uiSchema"]["model_credential"], hint("demoa_model_credential"))

        rows, before = await PluginConfig.objects.acount(), await events()
        with mock.patch(LOAD_PLUGIN, return_value=_OldPlugin()):
            res = await self.plugins.post(f"/{self.pid}/config/state", json={"config": {"threshold": 0.6}})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["uiSchema"]["model_credential"], hint("demoa_model_credential"))
        self.assertEqual(await PluginConfig.objects.acount(), rows)
        self.assertEqual(await events(), before)

    # Refusals

    async def test_a_plugin_that_adopted_assesses_takes_no_key(self):
        pid = (await self._install(_AdoptedPlugin))["DemoA"]
        before = await events()
        res = await self._save(pid, {"threshold": 0.5, "target_api_key": "sk-target-0123456789abcd"}, _AdoptedPlugin)
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(res.json()["detail"], rule().ADOPTED_REFUSAL.format(fields="target_api_key"))
        self.assertEqual(await PluginConfig.objects.acount(), 0)
        self.assertEqual(await secrets(), [])
        self.assertEqual(await events(), before)

        res = await self._saved(pid, {"threshold": 0.5, "target_api_key": ""}, _AdoptedPlugin)
        row = await PluginConfig.objects.aget(pk=res.json()["plugin_config_id"])
        self.assertEqual(row.config, {"threshold": 0.5, "target_api_key": ""})
        self.assertEqual(await secrets(), [])

    async def test_an_object_under_a_key_name_is_refused(self):
        pid = (await self._install())["DemoA"]
        before = await events()
        res = await self._save(pid, {"api_key": {"openai": "sk-abcdefghijklmnopqrstuv"}})
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(res.json()["detail"], "the key field api_key must hold one key as text, not a list or an object")
        self.assertEqual(await PluginConfig.objects.acount(), 0)
        self.assertEqual(await secrets(), [])
        self.assertEqual(await events(), before)

    async def test_at_most_sixteen_key_fields(self):
        pid = (await self._install())["DemoA"]
        before = await events()
        res = await self._save(pid, {f"k{n:02d}_api_key": f"value-{n:02d}-0123456789" for n in range(1, 18)})
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(res.json()["detail"], "a configuration may hold at most 16 key fields; this one holds 17")
        self.assertEqual(await PluginConfig.objects.acount(), 0)
        self.assertEqual(await events(), before)

        await self._saved(pid, {f"k{n:02d}_api_key": f"value-{n:02d}-0123456789" for n in range(1, 17)})
        self.assertEqual(len(await secrets()), 16)
        self.assertEqual([e["action"] for e in await self._new_events(before)],
                         ["engine.secret.created"] * 16 + ["engine.plugin.configured"])

    async def test_a_plugin_whose_schema_cannot_be_read_has_its_keys_found_by_name_and_value(self):
        pid = (await self._install())["DemoA"]
        res = await self._saved(pid, {"auth_header": "header-value-3", "judge_api_key": "sk-judge-0123456789abcdefgh"},
                                _UnreadableSchemaPlugin)
        row = await PluginConfig.objects.aget(pk=res.json()["plugin_config_id"])
        self.assertEqual(row.config, {"auth_header": "header-value-3"})
        secret = await self._only_secret()
        self.assertEqual(res.json()["uiSchema"]["judge_api_key"], hint(secret.key))
        self.assertEqual(decrypt_value(secret.encrypted_value), "sk-judge-0123456789abcdefgh")

    # Restoring and deleting

    async def test_a_row_holding_a_key_in_clear_cannot_be_restored(self):
        await self._first_save()
        secret = await self._only_secret()
        kept = await self._saved(self.pid, {"threshold": 0.7, "model_credential": ""})
        await self._saved(self.pid, {"threshold": 0.8})
        plugin = await Plugin.objects.aget(pid=self.pid)
        current = plugin.current_config_id
        with unrecorded():
            old = await PluginConfig.objects.acreate(plugin=plugin, config={"threshold": 0.5,
                                                                            "model_credential": "cred-old-0123456789"})
        before = await events()
        with mock.patch(LOAD_PLUGIN, return_value=_OldPlugin()):
            res = await self.plugins.post(f"/{self.pid}/configs/{old.id}/restore")
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["detail"], rule().RESTORE_REFUSED.format(config_id=old.id, fields="model_credential"))
        self.assertEqual((await Plugin.objects.aget(pid=self.pid)).current_config_id, current)
        self.assertEqual(await events(), before)

        config_id = kept.json()["plugin_config_id"]
        with mock.patch(LOAD_PLUGIN, return_value=_OldPlugin()):
            res = await self.plugins.post(f"/{self.pid}/configs/{config_id}/restore")
        self.assertEqual(res.status_code, 200, res.text)
        [restored] = await self._new_events(before)
        self.assertEvent(restored, "engine.plugin.configured", "plugin", self.pid, {})
        self.assertEqual(restored["content"], {"config_id": config_id,
                                               "config": {"threshold": 0.7, "model_credential": "set"},
                                               "settings": {"config.model_credential": str(secret.pid)}})

    async def test_deleting_the_secret_in_settings_removes_the_kept_key(self):
        await self._first_save()
        secret = await self._only_secret()
        res = await settings_client.delete(f"/{self.project.pid}/{secret.pid}")
        self.assertEqual(res.status_code, 204, res.text)
        self.assertEvent((await events())[-1], "engine.secret.deleted", "secret", secret.pid,
                         {"key": "demoa_model_credential"})
        with mock.patch(LOAD_PLUGIN, return_value=_OldPlugin()):
            res = await self.plugins.get(f"/{self.pid}/config/state")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn("ui:help", res.json()["uiSchema"].get("model_credential", {}))

    # The worker

    async def test_the_worker_gets_the_kept_key_through_its_mapping(self):
        res = await self._first_save()
        secret = await self._only_secret()
        with unrecorded():
            row = await PluginConfig.objects.aget(pk=res.json()["plugin_config_id"])
            evaluation = await Evaluation.objects.acreate(status=EvaluationStatus.Pending, project=self.project)
            await EvaluationPlugin.objects.acreate(evaluation=evaluation, plugin_config=row)
        internal = TestAsyncClient(importlib.import_module("aisc_backend.routers.internal").router)
        headers = {"X-Internal-Secret": INTERNAL_SECRET}
        token = ledger.current_request.set(None)
        try:
            with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}):
                detail = await internal.get(f"/evaluations/{evaluation.pid}", headers=headers)
                self.assertEqual(detail.status_code, 200, detail.text)
                [evaluation_plugin] = detail.json()["evaluation_plugins"]
                selections = evaluation_plugin["plugin_config"]["project_config_selections"]
                self.assertEqual(selections, [{"plugin_config_key": "config.model_credential",
                                               "project_config_pid": str(secret.pid)}])
                settings = await internal.post(f"/projects/settings/{self.project.pid}/by-pid", headers=headers,
                                               json={"project_config_selections": selections})
        finally:
            ledger.current_request.reset(token)
        self.assertEqual(settings.status_code, 200, settings.text)
        [setting] = settings.json()
        self.assertEqual((setting["category"], setting["plugin_config_key"]), ("secrets", "config.model_credential"))
        self.assertEqual(decrypt_value(setting["encrypted_value"]), CRED)

