from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_plugins.models import Plugin, PluginConfig
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.plugin import router


client = TestAsyncClient(router)


class PluginRouterTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)
        self.plugin = Plugin.objects.create(
            name="DemoPlugin", display_name="Demo",
            package_name="demo-pkg", version="0.1.0", project=self.project,
        )
        self.plugin_config = PluginConfig.objects.create(plugin=self.plugin, config={"a": 1})

    async def test_config_history_lists_configs(self):
        response = await client.get(f"/{self.plugin.pid}/configs")
        self.assertEqual(200, response.status_code, response.text)
        body = response.data
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["config"], {"a": 1})

    async def test_restore_plugin_config(self):
        config = await PluginConfig.objects.acreate(plugin=self.plugin, config={"b": 2})
        response = await client.post(f"/{self.plugin.pid}/configs/{config.id}/restore")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(response.data["config"], {"b": 2})

    async def test_restore_missing_config_404(self):
        response = await client.post(f"/{self.plugin.pid}/configs/99999/restore")
        self.assertEqual(404, response.status_code)

    async def test_toggle_enabled(self):
        # Link the config so the response must serialize `config` (regression:
        # a plain get() left current_config unloaded and the async serializer
        # raised SynchronousOnlyOperation -> 500).
        self.plugin.current_config = self.plugin_config
        await self.plugin.asave()
        response = await client.patch(f"/{self.plugin.pid}/enabled", json={"enabled": False})
        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse(response.data["enabled"])
        self.assertEqual(response.data["config"], {"a": 1})

    async def test_refresh_only_updates_installed_plugins(self):
        """Refresh re-imports the package but must NOT create/enable rows for
        plugins the project never installed (regression: it used to install all)."""
        import unittest.mock as mock
        installed = await Plugin.objects.acreate(
            name="DemoPlugin", display_name="Old",
            package_name="demo-pkg", version="0.2.0", project=self.project,
        )
        loader = {
            "refresh_package": lambda pkg, version: {
                "DemoPlugin": type("P", (), {"display_name": "Demo New"})(),
                "OtherPlugin": type("P", (), {"display_name": "Other"})(),
            },
        }
        with mock.patch("aisc_backend.routers.plugin.plugin_loader", **loader):
            resp = await client.post(
                "/refresh",
                json={
                    "package_name": "demo-pkg",
                    "version": "0.2.0",
                    "project_uuid": str(self.project.pid),
                },
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual([p["name"] for p in resp.json()], ["DemoPlugin"])
        refreshed = await Plugin.objects.aget(pk=installed.pk)
        self.assertEqual(refreshed.display_name, "Demo New")
        self.assertFalse(
            await Plugin.objects.filter(
                package_name="demo-pkg", project=self.project, name="OtherPlugin"
            ).aexists()
        )


# --- version switch + update from catalogue -----------------------------------

class PluginVersionSwitchTestCase(TestCase):
    """PATCH /{plugin_pid}/version + POST /{plugin_pid}/update with a stubbed loader."""

    def setUp(self):
        self.project = Project.objects.create(name="vp", status=ProjectStatus.Ready)
        self.plugin = Plugin.objects.create(
            name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
            version="0.1.0", project=self.project,
        )

    def _loader(self, versions: dict[str, dict], plugins_per_version=None):
        plugins_per_version = plugins_per_version or {
            v: {"DemoPlugin": type(
                "P", (), {"display_name": f"Demo {v}"}
            )()}
            for v in versions
        }
        return {
            "list_packages": lambda *a, **k: {"demo-pkg": versions},
            "load_package": lambda pkg, version: plugins_per_version[version],
        }

    async def test_switch_version_creates_and_disables_old(self):
        import unittest.mock as mock
        with mock.patch("aisc_backend.routers.plugin.plugin_loader", **self._loader({
            "0.1.0": {}, "0.2.0": {},
        })):
            resp = await client.patch(
                f"/{self.plugin.pid}/version", json={"version": "0.2.0"}
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()[0]["version"], "0.2.0")
        # old row soft-disabled, new row enabled
        old = await Plugin.objects.aget(pk=self.plugin.pk)
        self.assertFalse(old.enabled)
        new = await Plugin.objects.filter(package_name="demo-pkg", version="0.2.0").afirst()
        self.assertTrue(new.enabled)

    async def test_switch_unknown_version_422(self):
        import unittest.mock as mock
        with mock.patch("aisc_backend.routers.plugin.plugin_loader", **self._loader({
            "0.1.0": {}, "0.2.0": {},
        })):
            resp = await client.patch(
                f"/{self.plugin.pid}/version", json={"version": "9.9.9"}
            )
        self.assertEqual(resp.status_code, 422)

    async def test_update_latest_version_when_none_given(self):
        import unittest.mock as mock
        with mock.patch("aisc_backend.routers.plugin.plugin_loader", **self._loader({
            "0.1.0": {}, "0.2.0": {}, "0.3.0": {},
        })):
            resp = await client.post(f"/{self.plugin.pid}/update", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()[0]["version"], "0.3.0")

    async def test_update_picks_true_latest_over_multidigit(self):
        import unittest.mock as mock
        with mock.patch("aisc_backend.routers.plugin.plugin_loader", **self._loader({
            "0.1.0": {}, "0.9.0": {}, "0.10.0": {},
        })):
            resp = await client.post(f"/{self.plugin.pid}/update", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()[0]["version"], "0.10.0")

    async def test_update_pins_requested_version(self):
        import unittest.mock as mock
        with mock.patch("aisc_backend.routers.plugin.plugin_loader", **self._loader({
            "0.1.0": {}, "0.2.0": {}, "0.3.0": {},
        })):
            resp = await client.post(
                f"/{self.plugin.pid}/update", json={"version": "0.2.0"}
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()[0]["version"], "0.2.0")

    async def test_update_missing_package_422(self):
        import unittest.mock as mock
        with mock.patch("aisc_backend.routers.plugin.plugin_loader", **self._loader({})):
            resp = await client.post(f"/{self.plugin.pid}/update", json={})
        self.assertEqual(resp.status_code, 422)
