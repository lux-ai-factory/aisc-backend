"""L4 — Level 3 integration tests: real endpoint, real DB, loader mocked.

Focus on the persisted outcome and idempotence (E3/E4): install writes enabled
``Plugin`` rows readable straight from the DB (the same rows the worker reads), and
a second identical call creates no duplicates.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend.models import Plugin
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.routers.catalogue_install import router

client = TestAsyncClient(router)

TRUSTED = "https://registry.example.com/aisc/+simple/"
TOKEN = "s3cr3t"
BEARER = {"Authorization": f"Bearer {TOKEN}"}

FAKE_PACKAGE = {"FakePlugin": SimpleNamespace(display_name="Fake")}


def _payload(project_uuid, index_url=TRUSTED, version="0.1.35"):
    return {
        "package_name": "agentdojo",
        "version": version,
        "index_url": index_url,
        "project_uuid": str(project_uuid),
    }


@override_settings(
    CATALOGUE_INSTALL_ENABLED=True,
    CATALOGUE_INSTALL_TOKEN=TOKEN,
    CATALOGUE_TRUSTED_INDEXES=[TRUSTED],
)
class InstallIntegrationTests(TestCase):
    async def _make_project(self):
        return await ProjectRepository().create("l4-integration")

    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_install_creates_enabled_plugin_rows(self, mock_load):
        project = await self._make_project()
        resp = await client.post(
            "/install", json=_payload(project.pid), headers=BEARER
        )
        self.assertEqual(resp.status_code, 200)
        rows = [p async for p in Plugin.objects.filter(project=project)]
        self.assertEqual(len(rows), 1)
        p = rows[0]
        self.assertEqual(p.name, "FakePlugin")
        self.assertEqual(p.package_name, "agentdojo")
        self.assertEqual(p.version, "0.1.35")
        self.assertTrue(p.enabled)

    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_install_is_idempotent(self, mock_load):
        project = await self._make_project()
        first = await client.post(
            "/install", json=_payload(project.pid), headers=BEARER
        )
        second = await client.post(
            "/install", json=_payload(project.pid), headers=BEARER
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        # E3: two identical calls -> exactly one row, no duplicate.
        self.assertEqual(await Plugin.objects.filter(project=project).acount(), 1)

    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_reinstall_reuses_row(self, mock_load):
        project = await self._make_project()
        await client.post("/install", json=_payload(project.pid), headers=BEARER)
        original_pid = (await Plugin.objects.aget(project=project)).pid

        await client.post("/install", json=_payload(project.pid), headers=BEARER)
        rows = [p async for p in Plugin.objects.filter(project=project)]
        self.assertEqual(len(rows), 1)
        # Same (package, version, name, project) -> the very same row is reused.
        self.assertEqual(rows[0].pid, original_pid)

    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_installed_plugin_visible_in_db(self, mock_load):
        project = await self._make_project()
        await client.post("/install", json=_payload(project.pid), headers=BEARER)
        # E4: a plain DB query (what the worker also runs) sees the enabled row.
        visible = await Plugin.objects.filter(
            project=project,
            package_name="agentdojo",
            version="0.1.35",
            enabled=True,
        ).aexists()
        self.assertTrue(visible)

    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_untrusted_index_installs_nothing(self, mock_load):
        project = await self._make_project()
        resp = await client.post(
            "/install",
            json=_payload(project.pid, index_url="https://evil.example.com/x/+simple/"),
            headers=BEARER,
        )
        self.assertEqual(resp.status_code, 403)
        mock_load.assert_not_called()
        self.assertEqual(await Plugin.objects.filter(project=project).acount(), 0)
