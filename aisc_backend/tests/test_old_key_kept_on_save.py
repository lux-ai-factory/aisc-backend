"""Saving a plugin whose current configuration still holds a key in clear, without typing the key again, keeps
that key as a project secret (POST /api/v1/plugins/{pid}/config).

The form leaves a key field empty when the key was withheld from it, so a save of an old row's plugin would
otherwise drop the key. The save carries each old key, as text, that it leaves empty or absent into a new
project secret mapped from the new row; a key typed in the field wins. A plugin that adopted @assesses takes no
key, so nothing is carried for it. The old row is left as it is. Run as test_plugin_config_secrets_at_save.py
runs the routes.
"""
from unittest import mock

from aisc_backend.models import Plugin, PluginConfig
from aisc_backend.tests import test_plugin_config_secrets_at_save as at_save
from aisc_backend.tests.test_ledger_events import Recording, events, unrecorded
from aisc_backend.tests.test_plugin_config_secrets_at_save import (CRED, LOAD_PLUGIN, _AdoptedPlugin, _OldPlugin,
                                                                   hint, mappings, secrets)
from aisc_backend.utils.encryption import decrypt_value

URL = "https://user:pa55word@example.invalid/v1"


class OldKeyKeptOnSave(Recording):
    PACKAGE = at_save.SecretsAtSave.PACKAGE

    def setUp(self):
        super().setUp()
        from ninja.testing import TestAsyncClient

        from aisc_backend.routers.plugin import router

        self.plugins = TestAsyncClient(router)

    _install = at_save.SecretsAtSave._install
    _save = at_save.SecretsAtSave._save
    _saved = at_save.SecretsAtSave._saved

    async def _old_row(self, config, plugin_class=_OldPlugin):
        """DemoA installed, its current configuration a row saved before keys were kept as project secrets."""
        self.pid = (await self._install(plugin_class))["DemoA"]
        plugin = await Plugin.objects.aget(pid=self.pid)
        with unrecorded():
            old = await PluginConfig.objects.acreate(plugin=plugin, config=config)
            plugin.current_config = old
            await plugin.asave()
        return old

    async def _new_row(self, res):
        return await PluginConfig.objects.aget(pk=res.json()["plugin_config_id"])

    async def _assert_kept(self, res, key="demoa_model_credential", path="model_credential", value=CRED):
        [secret] = await secrets()
        self.assertEqual(secret.key, key)
        self.assertEqual(decrypt_value(secret.encrypted_value), value)
        row = await self._new_row(res)
        self.assertEqual(await mappings(row.id), {f"config.{path}": secret.id})
        self.assertEqual(res.json()["uiSchema"][path], hint(key))
        self.assertNotIn(value, res.text)
        return secret, row

    async def test_an_absent_key_field_carries_the_old_key_into_a_project_secret(self):
        await self._old_row({"threshold": 0.5, "model_credential": CRED})
        res = await self._saved(self.pid, {"threshold": 0.7})
        _, row = await self._assert_kept(res)
        self.assertEqual(row.config, {"threshold": 0.7})

    async def test_an_empty_key_field_carries_the_old_key_too(self):
        await self._old_row({"threshold": 0.5, "model_credential": CRED})
        res = await self._saved(self.pid, {"threshold": 0.7, "model_credential": ""})
        _, row = await self._assert_kept(res)
        self.assertEqual(row.config.get("threshold"), 0.7)
        self.assertEqual(row.config.get("model_credential", ""), "")

    async def test_a_key_typed_in_the_field_wins(self):
        await self._old_row({"threshold": 0.5, "model_credential": CRED})
        new = "cred-new-0123456789ab"
        res = await self._saved(self.pid, {"threshold": 0.7, "model_credential": new})
        [secret] = await secrets()
        self.assertEqual(decrypt_value(secret.encrypted_value), new)
        self.assertNotIn(CRED, res.text)

    async def test_an_unmarked_key_shaped_field_is_carried(self):
        await self._old_row({"threshold": 0.5, "endpoint": URL})
        res = await self._saved(self.pid, {"threshold": 0.5})
        await self._assert_kept(res, key="demoa_endpoint", path="endpoint", value=URL)

    async def test_the_old_row_is_left_as_it_is_and_still_cannot_be_restored(self):
        old = await self._old_row({"threshold": 0.5, "model_credential": CRED})
        await self._saved(self.pid, {"threshold": 0.7})
        old = await PluginConfig.objects.aget(pk=old.pk)
        self.assertEqual(old.config, {"threshold": 0.5, "model_credential": CRED})
        with mock.patch(LOAD_PLUGIN, return_value=_OldPlugin()):
            res = await self.plugins.post(f"/{self.pid}/configs/{old.id}/restore")
        self.assertEqual(res.status_code, 409, res.text)

    async def test_a_second_save_reuses_the_kept_secret(self):
        await self._old_row({"threshold": 0.5, "model_credential": CRED})
        first = await self._saved(self.pid, {"threshold": 0.7})
        secret, _ = await self._assert_kept(first)
        res = await self._saved(self.pid, {"threshold": 0.8})
        self.assertEqual(await secrets(), [secret])
        row = await self._new_row(res)
        self.assertEqual(await mappings(row.id), {"config.model_credential": secret.id})

    async def test_nothing_is_carried_for_a_plugin_that_adopted_assesses(self):
        await self._old_row({"threshold": 0.5, "model_credential": CRED}, _AdoptedPlugin)
        res = await self._saved(self.pid, {"threshold": 0.7}, _AdoptedPlugin)
        self.assertEqual(await secrets(), [])
        self.assertNotIn("ui:help", res.json()["uiSchema"].get("model_credential", {}))

    async def test_an_object_under_a_key_name_is_not_carried(self):
        await self._old_row({"model_credential": {"k": CRED}})
        res = await self._saved(self.pid, {"threshold": 0.7})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(await secrets(), [])

    async def test_the_ledger_records_the_new_secret_then_the_configuration(self):
        await self._old_row({"threshold": 0.5, "model_credential": CRED})
        before = await events()
        await self._saved(self.pid, {"threshold": 0.7})
        [secret] = await secrets()
        created, configured = (await events())[len(before):]
        self.assertEvent(created, "engine.secret.created", "secret", secret.pid, {"key": "demoa_model_credential"})
        self.assertEqual(configured["action"], "engine.plugin.configured")
        self.assertEqual(configured["content"]["config"], {"threshold": 0.7, "model_credential": "set"})
