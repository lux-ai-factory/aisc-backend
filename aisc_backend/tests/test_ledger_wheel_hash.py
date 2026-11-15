"""Installing a plugin records in the ledger which wheel was installed, by its sha256, so a result can be
traced to the exact plugin code that produced it."""
import contextlib
import importlib
from unittest import mock

from ninja.testing import TestAsyncClient

from aisc_backend.tests.test_ledger_events import Recording, _FakePlugin, events

HASH = "a" * 64


def installing(classes, wheel_sha256=HASH, code=None):
    """The package's classes as the engine gets them: today from the loader, later from the worker's manifest."""
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch("aisc_backend.routers.plugin.plugin_loader.load_package", return_value=classes))
    try:
        declarations = importlib.import_module("aisc_backend.plugin_declarations")
    except ImportError:
        return stack
    stack.enter_context(mock.patch.object(declarations, "fetch_manifest", return_value={
        "wheel_sha256": wheel_sha256, "dependency_lock": "", "code": code,
        "manifest": {"plugins": {name: {"display_name": name, "hooks": []} for name in classes}}}))
    return stack


class WheelHashInTheLedger(Recording):
    def setUp(self):
        super().setUp()
        from aisc_backend.routers.plugin import router

        self.plugins = TestAsyncClient(router)

    async def test_installing_a_package_names_its_wheels_hash(self):
        body = {"package_name": "demo-pkg", "version": "0.1.0", "project_uuid": str(self.project.pid),
                "wheel_sha256": HASH}
        with installing({"DemoA": _FakePlugin()}):
            res = await self.plugins.post("", json=body)
        self.assertEqual(res.status_code, 200, res.text)
        [event] = await events()
        self.assertEqual(event["action"], "plugin.installed")
        self.assertEqual(event["details"].get("wheel_sha256"), HASH, event["details"])

    async def test_installing_a_local_package_names_its_code_digest_and_no_wheel(self):
        body = {"package_name": "demo-pkg", "version": "0.1.0", "project_uuid": str(self.project.pid)}
        with installing({"DemoA": _FakePlugin()}, wheel_sha256=None, code={"digest": "d" * 64}):
            res = await self.plugins.post("", json=body)
        self.assertEqual(res.status_code, 200, res.text)
        [event] = await events()
        self.assertIsNone(event["details"]["wheel_sha256"], event["details"])
        self.assertEqual(event["details"].get("code_digest"), "d" * 64, event["details"])
