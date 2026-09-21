"""Where an installed plugin came from.

Discovery lives in the catalogue, so every install that arrives from it names
the catalogue entry it came from. The engine records that slug on the Plugin row
it creates: without it an installed distribution cannot be traced back to the
entry, its tags or its controls. Installs that did not come from the catalogue
(a deep link, a hand-made request) store no origin at all rather than a guess.
"""

from unittest.mock import patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import Plugin
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.routers.plugin import router as plugin_router

project_repository = ProjectRepository()
client = TestAsyncClient(plugin_router)


class FakePlugin:
    display_name = "LangBiTe"


def one_plugin_package(*_args, **_kwargs):
    """Stand in for the plugin manager: the index and the installed
    distribution are not what is under test here."""
    return {"langbite": FakePlugin()}


class CreatePluginsCatalogueOriginTestCase(TestCase):

    async def _install(self, body):
        with patch(
            "aisc_backend.routers.plugin.plugin_loader.load_package",
            side_effect=one_plugin_package,
        ):
            return await client.post("", json=body)

    async def test_records_the_catalogue_entry_it_was_installed_from(self):
        project = await project_repository.create("test")

        response = await self._install({
            "package_name": "aisc-plugin-langbite",
            "version": "0.1.2",
            "project_uuid": str(project.pid),
            "catalogue_slug": "langbite",
        })

        self.assertEqual(200, response.status_code)
        plugin = await Plugin.objects.aget(package_name="aisc-plugin-langbite")
        self.assertEqual("langbite", plugin.catalogue_slug)
        # And it is visible on the way out, so a client can show the origin.
        self.assertEqual("langbite", response.data[0]["catalogue_slug"])

    async def test_an_install_without_an_origin_stores_none(self):
        project = await project_repository.create("test")

        response = await self._install({
            "package_name": "aisc-plugin-langbite",
            "version": "0.1.2",
            "project_uuid": str(project.pid),
        })

        self.assertEqual(200, response.status_code)
        plugin = await Plugin.objects.aget(package_name="aisc-plugin-langbite")
        self.assertIsNone(plugin.catalogue_slug)

    async def test_re_enabling_a_plugin_learns_the_origin_it_lacked(self):
        # A row can predate the catalogue mapping, or have been created by a
        # deep link. When the same distribution is later installed from the
        # catalogue, the known origin is filled in rather than left empty.
        project = await project_repository.create("test")
        await self._install({
            "package_name": "aisc-plugin-langbite",
            "version": "0.1.2",
            "project_uuid": str(project.pid),
        })

        await self._install({
            "package_name": "aisc-plugin-langbite",
            "version": "0.1.2",
            "project_uuid": str(project.pid),
            "catalogue_slug": "langbite",
        })

        self.assertEqual(1, await Plugin.objects.acount())
        plugin = await Plugin.objects.aget(package_name="aisc-plugin-langbite")
        self.assertEqual("langbite", plugin.catalogue_slug)

    async def test_a_known_origin_is_never_overwritten_with_nothing(self):
        project = await project_repository.create("test")
        await self._install({
            "package_name": "aisc-plugin-langbite",
            "version": "0.1.2",
            "project_uuid": str(project.pid),
            "catalogue_slug": "langbite",
        })

        await self._install({
            "package_name": "aisc-plugin-langbite",
            "version": "0.1.2",
            "project_uuid": str(project.pid),
        })

        plugin = await Plugin.objects.aget(package_name="aisc-plugin-langbite")
        self.assertEqual("langbite", plugin.catalogue_slug)
