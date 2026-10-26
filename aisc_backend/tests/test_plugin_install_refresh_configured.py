"""Installing or refreshing a package whose plugin is already configured answers 200 with each plugin as the
project page shows it: a configured plugin's `config` is its current configuration with every key "set".

POST /api/v1/plugins and POST /api/v1/plugins/refresh answer the rows they installed or refreshed, reloaded
with their current configuration and its mappings after the transaction (PluginRepository.list_with_related).
Run as test_plugin_config_secrets_at_save.py runs the routes.
"""
from unittest import mock

from asgiref.sync import async_to_sync
from django.db import connection
from django.test.utils import CaptureQueriesContext

from aisc_backend.models import Plugin, PluginConfig
from aisc_backend.tests.test_ledger_events import Recording, unrecorded
from aisc_backend.tests import test_plugin_config_secrets_at_save as at_save
from aisc_backend.tests.test_plugin_config_secrets_at_save import CRED, LOAD_PLUGIN, _OldPlugin

PACKAGE = {"package_name": "demo-pkg", "version": "0.1.0"}
SHOWN = {"threshold": 0.5, "model_credential": "set"}


def _package():
    return {"DemoA": _OldPlugin(), "DemoB": _OldPlugin()}


class InstallRefreshConfigured(Recording):
    # the set-up and helpers of test_plugin_config_secrets_at_save.py, without its tests
    PACKAGE = at_save.SecretsAtSave.PACKAGE

    def setUp(self):
        super().setUp()
        from ninja.testing import TestAsyncClient

        from aisc_backend.routers.plugin import router

        self.plugins = TestAsyncClient(router)

    _install = at_save.SecretsAtSave._install
    _save = at_save.SecretsAtSave._save
    _saved = at_save.SecretsAtSave._saved
    _first_save = at_save.SecretsAtSave._first_save

    def _body(self):
        return {**PACKAGE, "project_uuid": str(self.project.pid)}

    async def _install_again(self):
        with mock.patch("aisc_backend.routers.plugin.plugin_loader.load_package", return_value=_package()), \
                mock.patch(LOAD_PLUGIN, return_value=_OldPlugin()):
            return await self.plugins.post("", json=self._body())

    async def _refresh(self):
        with mock.patch("aisc_backend.routers.plugin.plugin_loader.refresh_package", return_value=_package()), \
                mock.patch(LOAD_PLUGIN, return_value=_OldPlugin()):
            return await self.plugins.post("/refresh", json=self._body())

    async def _remove(self):
        res = await self.plugins.delete("", json=self._body())
        self.assertEqual(res.status_code, 204, res.text)

    @staticmethod
    def _by_name(res):
        return {row["name"]: row for row in res.json()}

    async def test_installing_again_answers_the_configured_plugin_with_its_key_set(self):
        await self._first_save()
        res = await self._install_again()
        self.assertEqual(res.status_code, 200, res.text)
        rows = self._by_name(res)
        self.assertEqual(rows["DemoA"]["config"], SHOWN)
        self.assertIsNone(rows["DemoB"]["config"])
        self.assertNotIn(CRED, res.text)

    async def test_installing_after_removing_answers_the_same(self):
        await self._first_save()
        await self._remove()
        res = await self._install_again()
        self.assertEqual(res.status_code, 200, res.text)
        rows = self._by_name(res)
        self.assertEqual(rows["DemoA"]["config"], SHOWN)
        self.assertIsNone(rows["DemoB"]["config"])
        self.assertTrue(rows["DemoA"]["enabled"])
        self.assertTrue(rows["DemoB"]["enabled"])
        self.assertNotIn(CRED, res.text)

    async def test_refreshing_answers_the_configured_plugin_with_its_key_set(self):
        await self._first_save()
        res = await self._refresh()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._by_name(res)["DemoA"]["config"], SHOWN)
        self.assertNotIn(CRED, res.text)

    async def test_a_configuration_without_a_key_is_answered_as_it_is(self):
        self.pid = (await self._install())["DemoA"]
        await self._saved(self.pid, {"threshold": 0.5})
        res = await self._install_again()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._by_name(res)["DemoA"]["config"], {"threshold": 0.5})

    async def test_the_answer_keeps_the_packages_plugin_order(self):
        await self._first_save()
        res = await self._install_again()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([row["name"] for row in res.json()], ["DemoA", "DemoB"])

    def test_list_with_related_loads_the_configuration_and_its_mappings(self):
        from aisc_backend.repositories.plugin_repository import PluginRepository

        async_to_sync(self._first_save)()
        a = Plugin.objects.get(name="DemoA")
        b = Plugin.objects.get(name="DemoB")
        with unrecorded():
            b.current_config = PluginConfig.objects.create(plugin=b, config={"threshold": 0.1})
            b.save()

        loaded = async_to_sync(PluginRepository().list_with_related)([b.pid, a.pid])
        self.assertEqual([p.pid for p in loaded], [b.pid, a.pid])
        with CaptureQueriesContext(connection) as queries:
            seen = []
            for plugin in loaded:
                seen.append(plugin.current_config.config)
                for mapping in plugin.current_config._prefetched_objects_cache["setting_mappings"]:
                    seen.append((mapping.plugin_config_key, mapping.project_config.key))
        self.assertEqual(len(queries.captured_queries), 0)
        self.assertEqual(seen, [{"threshold": 0.1}, {"threshold": 0.5},
                                ("config.model_credential", "demoa_model_credential")])
