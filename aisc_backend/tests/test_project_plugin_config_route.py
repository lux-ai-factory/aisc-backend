"""Reading a project's plugin configuration never hands back a key.

GET /api/v1/projects/{pid}/plugins/{name}/config answers the plugin's current configuration masked as the ledger
copy is: a field the form or its name marks as a key is "set" (None when empty), a field kept as a project secret
is "set" at its path, and a key-shaped value anywhere is "set". The other routes that hand back a stored
configuration (the form's GET /plugins/{pid}/config/state, the history GET /plugins/{pid}/configs, and a plugin's
`config` wherever it is serialised) never return a key in clear either, including a row saved before keys were
kept as project secrets.
"""
import uuid
from unittest import mock

from ninja.testing import TestAsyncClient

from aisc_backend.models import (Plugin, PluginConfig, PluginConfigProjectConfig, Project, ProjectConfig,
                                 ProjectConfigCategory)
from aisc_backend.tests.test_ledger_events import Recording, events, unrecorded
from aisc_backend.utils.encryption import encrypt_value

CRED = "cred-0123456789abcdef"
LOAD_PLUGIN = "aisc_backend.routers.plugin.plugin_loader.load_plugin"


class _Form:
    """A plugin whose form marks `model_credential` as a password and holds a nested `auth.token`."""
    display_name = "Demo"
    project_config_definitions = []
    help_text = ""

    def get_full_schema(self):
        return {"properties": {
            "threshold": {"type": "number"},
            "model_credential": {"type": "string", "format": "password"},
            "endpoint": {"type": "string"},
            "auth": {"type": "object", "properties": {"token": {"type": "string"}}},
        }}, {}

    def on_config_change(self, config):
        return config, {}, {}


class _Base(Recording):
    def setUp(self):
        super().setUp()
        from aisc_backend.routers.plugin import router as plugin_router
        from aisc_backend.routers.project import router as project_router

        self.projects = TestAsyncClient(project_router)
        self.plugins = TestAsyncClient(plugin_router)

    async def _plugin(self, enabled=True) -> Plugin:
        with unrecorded():
            return await Plugin.objects.acreate(name="DemoA", display_name="Demo", package_name="demo-pkg",
                                                version="0.1.0", project=self.project, enabled=enabled)

    async def _secret(self, key="demoa_model_credential", name="Demo: model_credential", value=CRED,
                      category=ProjectConfigCategory.SECRETS) -> ProjectConfig:
        with unrecorded():
            return await ProjectConfig.objects.acreate(
                project=self.project, category=category, key=key, name=name,
                encrypted_value=encrypt_value(value), masked_value="cred...cdef")

    async def _config(self, plugin: Plugin, config: dict, mappings=(), current=True) -> PluginConfig:
        """A stored configuration of `plugin`, linked to each (plugin_config_key, project config) of `mappings`."""
        with unrecorded():
            row = await PluginConfig.objects.acreate(plugin=plugin, config=config)
            for key, project_config in mappings:
                await PluginConfigProjectConfig.objects.acreate(plugin_config=row, project_config=project_config,
                                                                plugin_config_key=key)
            if current:
                plugin.current_config = row
                await plugin.asave()
        return row

    async def _kept_split(self, enabled=True):
        """A configuration saved after the split: the key kept as a project secret through its mapping."""
        plugin = await self._plugin(enabled)
        secret = await self._secret()
        await self._config(plugin, {"threshold": 0.5}, [("config.model_credential", secret)])
        return plugin, secret

    async def _old_clear_row(self):
        """A configuration saved before keys were kept as project secrets: the key is in the stored row."""
        plugin = await self._plugin()
        await self._config(plugin, {"threshold": 0.5, "model_credential": CRED})
        return plugin

    async def _get(self, name="DemoA", form=None, pid=None):
        with mock.patch(LOAD_PLUGIN, return_value=form or _Form()):
            return await self.projects.get(f"/{pid or self.project.pid}/plugins/{name}/config")


class ProjectPluginConfigRoute(_Base):
    """GET /api/v1/projects/{pid}/plugins/{name}/config."""

    async def test_a_kept_key_is_set_and_nothing_of_the_secret_is_answered(self):
        _, secret = await self._kept_split()
        res = await self._get()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"threshold": 0.5, "model_credential": "set"})
        for leaked in (CRED, "cred...cdef", "demoa_model_credential", str(secret.pid)):
            self.assertNotIn(leaked, res.text)

    async def test_an_old_row_holding_the_key_in_clear_answers_it_set(self):
        await self._old_clear_row()
        res = await self._get()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"threshold": 0.5, "model_credential": "set"})
        self.assertNotIn(CRED, res.text)

    async def test_an_empty_key_field_with_nothing_kept_is_null(self):
        plugin = await self._plugin()
        await self._config(plugin, {"threshold": 0.5, "model_credential": ""})
        res = await self._get()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"threshold": 0.5, "model_credential": None})

    async def test_a_plugin_that_cannot_be_loaded_is_masked_by_name_and_value(self):
        plugin = await self._plugin()
        await self._config(plugin, {"n": 1, "judge_api_key": CRED})
        with mock.patch(LOAD_PLUGIN, side_effect=RuntimeError("cannot load")):
            res = await self.projects.get(f"/{self.project.pid}/plugins/DemoA/config")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"n": 1, "judge_api_key": "set"})

    async def test_a_key_shape_in_an_unmarked_field_is_set(self):
        plugin = await self._plugin()
        await self._config(plugin, {"threshold": 0.5, "endpoint": "https://user:pa55word@example.invalid/v1"})
        res = await self._get()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"threshold": 0.5, "endpoint": "set"})
        self.assertNotIn("pa55word", res.text)

    async def test_a_kept_nested_path_is_set(self):
        plugin = await self._plugin()
        secret = await self._secret(key="demoa_auth_token", name="Demo: auth.token")
        await self._config(plugin, {"auth": {}}, [("config.auth.token", secret)])
        res = await self._get()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"auth": {"token": "set"}})

    async def test_only_the_current_configuration_is_read(self):
        plugin = await self._plugin()
        older = await self._config(plugin, {"threshold": 0.1})
        await self._config(plugin, {"threshold": 0.9}, current=False)
        await plugin.arefresh_from_db()
        self.assertEqual(plugin.current_config_id, older.id)
        res = await self._get()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"threshold": 0.1})

    async def test_a_mapping_to_a_setting_is_not_a_kept_path(self):
        plugin = await self._plugin()
        setting = await self._secret(key="model", name="model", value="gpt-x",
                                     category=ProjectConfigCategory.VARIABLES)
        await self._config(plugin, {"threshold": 0.5}, [("model", setting)])
        res = await self._get()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"threshold": 0.5})

    async def test_a_plugin_with_no_configuration_is_404(self):
        await self._plugin()
        res = await self._get()
        self.assertEqual(res.status_code, 404, res.text)
        self.assertEqual(res.json(), {"detail": f"Plugin DemoA of project {self.project.pid} has no configuration"})

    async def test_no_plugin_of_that_name_is_404(self):
        await self._kept_split()
        res = await self._get(name="Nope")
        self.assertEqual(res.status_code, 404, res.text)
        self.assertEqual(res.json(), {"detail": f"Project {self.project.pid} has no plugin Nope"})

    async def test_a_disabled_plugin_is_not_found(self):
        await self._kept_split(enabled=False)
        res = await self._get()
        self.assertEqual(res.status_code, 404, res.text)
        self.assertEqual(res.json(), {"detail": f"Project {self.project.pid} has no plugin DemoA"})

    async def test_an_unknown_project_is_not_found(self):
        with self.assertRaises(Project.DoesNotExist):
            await self._get(pid=uuid.uuid4())

    async def test_reading_writes_no_row_and_records_no_event(self):
        await self._kept_split()

        async def counts():
            return (await PluginConfig.objects.acount(), await PluginConfigProjectConfig.objects.acount(),
                    await ProjectConfig.objects.acount())

        rows, recorded = await counts(), await events()
        res = await self._get()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(await counts(), rows)
        self.assertEqual(await events(), recorded)


class FormState(_Base):
    """GET /plugins/{pid}/config/state: the form's configuration. A kept key stays out of it, so the form shows
    an empty password input that keeps the key; an old row's key in clear is not handed to the form."""

    async def _state(self, plugin):
        with mock.patch(LOAD_PLUGIN, return_value=_Form()):
            return await self.plugins.get(f"/{plugin.pid}/config/state")

    async def test_a_kept_key_stays_out_of_the_forms_configuration(self):
        plugin, secret = await self._kept_split()
        res = await self._state(plugin)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["config"], {"threshold": 0.5})
        self.assertEqual(res.json()["secret_fields"], ["model_credential"])
        self.assertNotIn(CRED, res.text)

    async def test_an_old_row_holding_the_key_in_clear_does_not_hand_it_to_the_form(self):
        plugin = await self._old_clear_row()
        res = await self._state(plugin)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn(CRED, res.text)
        config = res.json()["config"]
        self.assertEqual(config["threshold"], 0.5)
        # empty, so a save from the form does not store a placeholder as the key
        self.assertIn(config.get("model_credential"), (None, ""))

    async def test_a_key_shape_in_an_unmarked_field_is_not_handed_to_the_form(self):
        plugin = await self._plugin()
        await self._config(plugin, {"threshold": 0.5, "endpoint": "https://user:pa55word@example.invalid/v1"})
        res = await self._state(plugin)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn("pa55word", res.text)


class ConfigHistory(_Base):
    """GET /plugins/{pid}/configs: every stored row, each masked as the ledger copy is."""

    async def test_every_row_answers_its_keys_set(self):
        plugin = await self._plugin()
        secret = await self._secret()
        old = await self._config(plugin, {"threshold": 0.1, "model_credential": CRED}, current=False)
        new = await self._config(plugin, {"threshold": 0.5}, [("config.model_credential", secret)])
        with mock.patch(LOAD_PLUGIN, return_value=_Form()):
            res = await self.plugins.get(f"/{plugin.pid}/configs")
        self.assertEqual(res.status_code, 200, res.text)
        by_id = {row["id"]: row["config"] for row in res.json()}
        self.assertEqual(by_id, {old.id: {"threshold": 0.1, "model_credential": "set"},
                                 new.id: {"threshold": 0.5, "model_credential": "set"}})
        self.assertNotIn(CRED, res.text)
        self.assertNotIn("cred...cdef", res.text)

    async def test_a_plugin_that_cannot_be_loaded_has_its_rows_masked_by_name_and_value(self):
        plugin = await self._plugin()
        await self._config(plugin, {"n": 1, "judge_api_key": CRED,
                                    "endpoint": "https://user:pa55word@example.invalid/v1"})
        with mock.patch(LOAD_PLUGIN, side_effect=RuntimeError("cannot load")):
            res = await self.plugins.get(f"/{plugin.pid}/configs")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([row["config"] for row in res.json()],
                         [{"n": 1, "judge_api_key": "set", "endpoint": "set"}])


class SerialisedPlugin(_Base):
    """A plugin's `config` wherever it is serialised (the project's plugin list the webapp's menu reads, and the
    plugin a toggle answers): masked as the ledger copy is, and still null for a plugin with no configuration."""

    async def _listed(self):
        with mock.patch(LOAD_PLUGIN, return_value=_Form()):
            res = await self.projects.get(f"/{self.project.pid}")
        self.assertEqual(res.status_code, 200, res.text)
        [plugin] = res.json()["plugins"]
        return res, plugin

    async def test_an_old_row_holding_the_key_in_clear_is_listed_set(self):
        await self._old_clear_row()
        res, plugin = await self._listed()
        self.assertEqual(plugin["config"], {"threshold": 0.5, "model_credential": "set"})
        self.assertNotIn(CRED, res.text)

    async def test_a_kept_key_is_listed_set(self):
        await self._kept_split()
        res, plugin = await self._listed()
        self.assertEqual(plugin["config"], {"threshold": 0.5, "model_credential": "set"})
        self.assertNotIn(CRED, res.text)

    async def test_a_plugin_with_no_configuration_is_listed_null(self):
        await self._plugin()
        _, plugin = await self._listed()
        self.assertIsNone(plugin["config"])

    async def test_the_toggle_answer_holds_no_key_in_clear(self):
        plugin = await self._old_clear_row()
        with mock.patch(LOAD_PLUGIN, return_value=_Form()):
            res = await self.plugins.patch(f"/{plugin.pid}/enabled", json={"enabled": True})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["config"], {"threshold": 0.5, "model_credential": "set"})
        self.assertNotIn(CRED, res.text)
