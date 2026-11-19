"""GET /api/v1/plugins carries the dimensions a local package declares, and none for a registry one."""
from unittest import mock

from django.test import SimpleTestCase
from ninja.testing import TestAsyncClient

from aisc_backend.routers.plugin import router

client = TestAsyncClient(router)


class AvailablePluginsDimensionsTestCase(SimpleTestCase):
    async def test_a_local_package_lists_its_declared_dimensions_and_a_registry_one_none(self):
        packages = {
            "my-probe": {"0.2.0+local": {"source": "local", "dimensions": ["REQ5"]}},
            "aisc-plugin-langbite": {"0.1.0": {"source": "registry"}},
        }
        with mock.patch("aisc_backend.routers.plugin.plugin_loader") as loader:
            loader.list_packages.return_value = packages
            response = await client.get("")
        self.assertEqual(200, response.status_code, response.text)
        by_name = {row["package_name"]: row for row in response.json()}
        self.assertEqual(["REQ5"], by_name["my-probe"]["dimensions"])
        self.assertEqual([], by_name["aisc-plugin-langbite"]["dimensions"])


class InstalledLoaderDimensionsTestCase(SimpleTestCase):
    """The loader the engine actually installs (not a stand-in) reads the declaration from a package folder."""

    def test_the_installed_loader_lists_the_dimensions_a_local_package_declares(self):
        import tempfile
        from pathlib import Path

        from aisc_plugin_manager import devpi_client
        from aisc_plugin_manager.loader import Loader

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(devpi_client.DevpiClient, "list_versions", lambda self: {}):
            package = Path(tmp) / "demo"
            (package / "aisc_plugin_demo").mkdir(parents=True)
            (package / "aisc_plugin_demo" / "__init__.py").write_text("")
            (package / "pyproject.toml").write_text(
                '[project]\nname = "aisc-plugin-demo"\nversion = "0.1.0"\n'
                'dependencies = ["aisc-plugin-interface>=0.2.0"]\n\n[tool.aisc]\ndimensions = ["REQ5"]\n')
            listed = Loader(tmp, "http://devpi.invalid", "root/public").list_packages(refresh=True)
        self.assertEqual(["REQ5"], listed["aisc-plugin-demo"]["0.1.0+local"]["dimensions"])
