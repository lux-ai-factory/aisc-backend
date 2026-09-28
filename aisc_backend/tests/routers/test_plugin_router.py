from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import Plugin, PluginConfig
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
        response = await client.patch(f"/{self.plugin.pid}/enabled", json={"enabled": False})
        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse(response.data["enabled"])
