"""A saved plugin configuration carries the name its author gives it at save time (POST /plugins/{pid}/config),
so a run can say which configuration it used in words, not only by number and date. The name is optional; the
history lists it, and the form's state names the current configuration so the next save can keep it.
Run as test_plugin_config_secrets_at_save.py runs the routes."""
from aisc_backend.models import PluginConfig
from aisc_backend.tests import test_plugin_config_secrets_at_save as at_save


class ConfigNames(at_save.SecretsAtSave):
    """The parent's helpers (install, save); its own tests are about keys and are left out below."""

    async def _pid(self):
        return (await self._install())["DemoA"]

    async def test_the_name_given_at_save_is_stored_and_answered(self):
        pid = await self._pid()
        res = await self._saved(pid, {"threshold": 0.5}, name="Debts pilot, conversations, 3 reps")
        row = await PluginConfig.objects.aget(pk=res.json()["plugin_config_id"])
        self.assertEqual(row.name, "Debts pilot, conversations, 3 reps")
        self.assertEqual(res.json()["name"], "Debts pilot, conversations, 3 reps")

    async def test_the_name_is_trimmed_and_may_be_left_out(self):
        pid = await self._pid()
        named = await self._saved(pid, {"threshold": 0.5}, name="  format check  ")
        unnamed = await self._saved(pid, {"threshold": 0.6})
        self.assertEqual((await PluginConfig.objects.aget(pk=named.json()["plugin_config_id"])).name, "format check")
        self.assertEqual((await PluginConfig.objects.aget(pk=unnamed.json()["plugin_config_id"])).name, "")

    async def test_a_name_longer_than_the_column_is_refused(self):
        pid = await self._pid()
        res = await self._save(pid, {"threshold": 0.5}, name="x" * 256)
        self.assertEqual(res.status_code, 422, res.text)

    async def test_the_history_lists_each_configurations_name(self):
        pid = await self._pid()
        await self._saved(pid, {"threshold": 0.5}, name="first")
        await self._saved(pid, {"threshold": 0.6}, name="second")
        res = await self.plugins.get(f"/{pid}/configs")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(sorted(row["name"] for row in res.json()), ["first", "second"])

    async def test_the_forms_state_names_the_current_configuration(self):
        from unittest import mock

        pid = await self._pid()
        await self._saved(pid, {"threshold": 0.5}, name="kept on the next save")
        with mock.patch(at_save.LOAD_PLUGIN, return_value=at_save._OldPlugin()):
            res = await self.plugins.get(f"/{pid}/config/state")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["name"], "kept on the next save")


# the inherited key tests run in their own module, not twice
for _name in [n for n in dir(at_save.SecretsAtSave) if n.startswith("test_")]:
    setattr(ConfigNames, _name, None)
