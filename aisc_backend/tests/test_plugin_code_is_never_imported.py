"""The engine never imports plugin code: every plugin page answers from the manifest stored on the plugin's
row, read once in the worker's sandbox at install. A form hook the plugin overrides is asked of the worker; a
row installed before manifests existed gets its manifest once and keeps it; a manifest that cannot be read is
a 503 with its reason. An install stores the manifest, the wheel's hash and the dependency lock, and a refresh
whose wheel changed on the index is refused."""
import importlib
import re
import unittest.mock as mock
from pathlib import Path

from django.test import TestCase
from ninja.testing import TestAsyncClient

import aisc_backend
from aisc_backend.models import Plugin, PluginConfig
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers import plugin as plugin_routes

client = TestAsyncClient(plugin_routes.router)
HASH = "a" * 64
LOCK = f"demo-pkg==0.1.0 \\\n    --hash=sha256:{HASH}\n"

#: what the sandbox reads from a plugin that declares nothing and overrides no hook
MANIFEST = {
    "name": "DemoPlugin", "display_name": "Demo", "help_text": "A demo plugin.", "display_icon": "",
    "feature_flags": {}, "input_definitions": [], "project_config_definitions": [],
    "form_schema": {"type": "object", "properties": {}}, "form_ui_schema": {},
    "metrics": [], "metric_visualizations": [],
    "declaration": {"target_kinds": [], "card_access": None, "card_protocols": [], "card_needs_connection": False,
                    "local_model_input": None, "helper_roles": [], "inputs": [], "project_configs": [],
                    "metrics": []},
    "adopted": False, "hooks": [],
}
IMPORTED = AssertionError("the engine imported plugin code")


def no_import():
    """The loader's import paths, each failing the test if called."""
    return mock.patch.multiple(plugin_routes.plugin_loader, load_plugin=mock.Mock(side_effect=IMPORTED),
                               load_package=mock.Mock(side_effect=IMPORTED),
                               refresh_package=mock.Mock(side_effect=IMPORTED))


class PluginCodeIsNeverImportedTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)
        self.plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
                                            version="0.1.0", project=self.project)
        self.config = PluginConfig.objects.create(plugin=self.plugin, config={})
        self.plugin.current_config = self.config
        self.plugin.save()

    def _stored(self, manifest=MANIFEST, **more):
        fields = {f.name for f in Plugin._meta.get_fields()}
        missing = {"manifest", "wheel_sha256", "dependency_lock", "code_identity"} - fields
        self.assertEqual(missing, set(), "a plugin row keeps no manifest, hash or lock")
        Plugin.objects.filter(pk=self.plugin.pk).update(**{"manifest": manifest, "wheel_sha256": HASH,
                                                            "dependency_lock": LOCK, **more})

    def _declarations(self):
        try:
            return importlib.import_module("aisc_backend.plugin_declarations")
        except ImportError:
            self.fail("the engine has no stored declarations to answer from (aisc_backend.plugin_declarations)")

    async def _get(self, path):
        with no_import():
            return await client.get(path)

    async def test_every_declaration_route_answers_from_the_manifest(self):
        from asgiref.sync import sync_to_async

        await sync_to_async(self._stored)()
        pid = self.plugin.pid
        for path in (f"/{pid}/feature_flags", f"/{pid}/display_icon", f"/{pid}/input_definitions",
                     f"/{pid}/project_config_definitions"):
            with self.subTest(path=path):
                response = await self._get(path)
                self.assertEqual(response.status_code, 200, path)

    async def test_the_form_state_answers_from_the_manifest(self):
        from asgiref.sync import sync_to_async

        await sync_to_async(self._stored)()
        with no_import():
            response = await client.post(f"/{self.plugin.pid}/config/state",
                                         json={"config": {}, "project_config_selections": []})
        self.assertEqual(response.status_code, 200, response.content[:300])

    async def test_a_hook_the_plugin_overrides_is_asked_of_the_worker(self):
        from asgiref.sync import sync_to_async

        await sync_to_async(self._stored)({**MANIFEST, "hooks": ["on_config_change"]})
        declarations = self._declarations()
        with no_import(), mock.patch.object(declarations, "plugin_hook", create=True,
                                            return_value=[{}, {"type": "object"}, {}]) as asked:
            response = await client.post(f"/{self.plugin.pid}/config/state",
                                         json={"config": {}, "project_config_selections": []})
        self.assertEqual(response.status_code, 200, response.content[:300])
        asked.assert_called_once()

    async def test_a_row_without_a_manifest_gets_it_once_and_keeps_it(self):
        declarations = self._declarations()
        read = {"wheel_sha256": HASH, "dependency_lock": LOCK, "manifest": {"plugins": {"DemoPlugin": MANIFEST}},
                "code": None}
        with no_import(), mock.patch.object(declarations, "fetch_manifest", return_value=read) as fetch:
            for _ in range(2):
                response = await client.get(f"/{self.plugin.pid}/feature_flags")
                self.assertEqual(response.status_code, 200)
        fetch.assert_called_once()

    async def test_a_local_row_whose_folder_changed_has_its_manifest_read_again(self):
        from asgiref.sync import sync_to_async

        await sync_to_async(self._stored)(wheel_sha256=None, code_identity={"digest": "old"})
        declarations = self._declarations()
        edited = {**MANIFEST, "display_icon": "edited"}
        read = {"wheel_sha256": None, "dependency_lock": LOCK, "manifest": {"plugins": {"DemoPlugin": edited}},
                "code": {"digest": "new", "git_commit": None, "git_dirty": None}}
        with no_import(), mock.patch.object(declarations, "fetch_manifest", return_value=read) as fetch, \
                mock.patch.object(plugin_routes.plugin_loader, "local_provenance", return_value={"digest": "new"}):
            first = await client.get(f"/{self.plugin.pid}/display_icon")
            again = await client.get(f"/{self.plugin.pid}/display_icon")
        self.assertEqual((first.json(), again.json()), ("edited", "edited"))
        fetch.assert_called_once()

    async def test_an_unchanged_local_row_keeps_its_manifest(self):
        from asgiref.sync import sync_to_async

        await sync_to_async(self._stored)(wheel_sha256=None, code_identity={"digest": "same"})
        declarations = self._declarations()
        with no_import(), mock.patch.object(declarations, "fetch_manifest") as fetch, \
                mock.patch.object(plugin_routes.plugin_loader, "local_provenance", return_value={"digest": "same"}):
            response = await client.get(f"/{self.plugin.pid}/feature_flags")
        self.assertEqual(response.status_code, 200)
        fetch.assert_not_called()

    async def test_a_manifest_that_cannot_be_read_is_503_with_its_reason(self):
        declarations = self._declarations()
        with no_import(), mock.patch.object(declarations, "fetch_manifest",
                                            side_effect=RuntimeError("the sandbox did not answer")):
            response = await client.get(f"/{self.plugin.pid}/feature_flags")
        self.assertEqual(response.status_code, 503)
        self.assertIn("the plugin's declarations could not be read", response.json()["detail"])

    async def test_an_install_stores_the_manifest_the_hash_and_the_lock(self):
        declarations = self._declarations()
        read = {"wheel_sha256": HASH, "dependency_lock": LOCK, "manifest": {"plugins": {"OtherPlugin": MANIFEST}},
                "code": None}
        with no_import(), mock.patch.object(declarations, "fetch_manifest", return_value=read):
            response = await client.post("", json={"package_name": "other-pkg", "version": "1.0",
                                                   "project_uuid": str(self.project.pid), "wheel_sha256": HASH})
        self.assertEqual(response.status_code, 200, response.content[:300])
        row = await Plugin.objects.aget(package_name="other-pkg")
        self.assertEqual((row.wheel_sha256, row.dependency_lock), (HASH, LOCK))
        self.assertEqual(row.manifest["display_name"], "Demo")

    async def test_a_refresh_whose_wheel_changed_on_the_index_is_refused(self):
        from asgiref.sync import sync_to_async

        await sync_to_async(self._stored)()
        declarations = self._declarations()
        read = {"wheel_sha256": "b" * 64, "dependency_lock": LOCK,
                "manifest": {"plugins": {"DemoPlugin": MANIFEST}}, "code": None}
        with no_import(), mock.patch.object(declarations, "fetch_manifest", return_value=read):
            response = await client.post("/refresh", json={"package_name": "demo-pkg", "version": "0.1.0",
                                                           "project_uuid": str(self.project.pid)})
        self.assertEqual(response.status_code, 409, response.content[:300])
        self.assertIn("changed on the index", response.json()["detail"])

    def test_no_backend_module_calls_the_loaders_import_path(self):
        root = Path(aisc_backend.__file__).parent
        offenders = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                           if "tests" not in p.parts
                           and re.search(r"\b(load_plugin|load_package|refresh_package)\(", p.read_text()))
        self.assertEqual(offenders, [])
