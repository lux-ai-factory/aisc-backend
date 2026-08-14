"""L4 — Level 2 behaviour tests: flag / auth / trust / pin gates.

Drives the real endpoint through the ninja test client with the loader mocked (no
real pip install). Covers the visible-only-behind-flag rule (404), the token wall
(401), and the trust gates that must fire *before* any install (403 untrusted, 400
unpinned).
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


def _payload(index_url=TRUSTED, version="0.1.35", project_uuid=None):
    return {
        "package_name": "agentdojo",
        "version": version,
        "index_url": index_url,
        "project_uuid": str(project_uuid),
    }


@override_settings(
    CATALOGUE_INSTALL_TOKEN=TOKEN,
    CATALOGUE_TRUSTED_INDEXES=[TRUSTED],
)
class InstallBehaviourTests(TestCase):
    async def _make_project(self):
        return await ProjectRepository().create("l4-behaviour")

    @override_settings(CATALOGUE_INSTALL_ENABLED=False)
    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_disabled_feature_returns_404(self, mock_load):
        # Feature off (default) => endpoint invisible even with a valid token.
        project = await self._make_project()
        resp = await client.post(
            "/install", json=_payload(project_uuid=project.pid), headers=BEARER
        )
        self.assertEqual(resp.status_code, 404)
        mock_load.assert_not_called()

    @override_settings(CATALOGUE_INSTALL_ENABLED=False)
    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_disabled_feature_returns_404_without_header(self, mock_load):
        # Feature off + NO Authorization header => still 404 (invisible), not 401.
        # Guards E7/E8: a Platform not using the feature is identical to before.
        project = await self._make_project()
        resp = await client.post("/install", json=_payload(project_uuid=project.pid))
        self.assertEqual(resp.status_code, 404)
        mock_load.assert_not_called()

    @override_settings(CATALOGUE_INSTALL_ENABLED=True)
    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_enabled_without_token_401(self, mock_load):
        project = await self._make_project()
        resp = await client.post("/install", json=_payload(project_uuid=project.pid))
        self.assertEqual(resp.status_code, 401)
        mock_load.assert_not_called()

    @override_settings(CATALOGUE_INSTALL_ENABLED=True)
    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_enabled_wrong_token_401(self, mock_load):
        project = await self._make_project()
        resp = await client.post(
            "/install",
            json=_payload(project_uuid=project.pid),
            headers={"Authorization": "Bearer wrong"},
        )
        self.assertEqual(resp.status_code, 401)
        mock_load.assert_not_called()

    @override_settings(CATALOGUE_INSTALL_ENABLED=True)
    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_untrusted_index_403(self, mock_load):
        project = await self._make_project()
        resp = await client.post(
            "/install",
            json=_payload(
                index_url="https://pypi.org/simple/", project_uuid=project.pid
            ),
            headers=BEARER,
        )
        self.assertEqual(resp.status_code, 403)
        mock_load.assert_not_called()
        self.assertEqual(await Plugin.objects.acount(), 0)

    @override_settings(CATALOGUE_INSTALL_ENABLED=True)
    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_unpinned_version_400(self, mock_load):
        project = await self._make_project()
        resp = await client.post(
            "/install",
            json=_payload(version=">=1.0", project_uuid=project.pid),
            headers=BEARER,
        )
        self.assertEqual(resp.status_code, 400)
        mock_load.assert_not_called()
        self.assertEqual(await Plugin.objects.acount(), 0)

    @override_settings(CATALOGUE_INSTALL_ENABLED=True)
    @patch(
        "aisc_backend.routers.catalogue_install.plugin_loader.load_package",
        return_value=FAKE_PACKAGE,
    )
    async def test_valid_request_installs_200(self, mock_load):
        project = await self._make_project()
        resp = await client.post(
            "/install", json=_payload(project_uuid=project.pid), headers=BEARER
        )
        self.assertEqual(resp.status_code, 200)
        mock_load.assert_called_once_with("agentdojo", "0.1.35")
        row = await Plugin.objects.aget(project=project, name="FakePlugin")
        self.assertEqual(row.package_name, "agentdojo")
        self.assertEqual(row.version, "0.1.35")
        self.assertTrue(row.enabled)
