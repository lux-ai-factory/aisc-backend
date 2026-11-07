"""A project may hold several versions of a test's package, but only one is on: turning a version on (installing
it, or switching it on) turns the package's other versions off in that project. Nothing is deleted, so their
settings and evaluations stay. Another package is left alone.
Run as test_plugin_config_secrets_at_save.py runs the routes."""
from unittest import mock

from asgiref.sync import async_to_sync
from ninja.testing import TestAsyncClient

from aisc_backend.models import Plugin
from aisc_backend.tests.test_ledger_events import Recording
from aisc_backend.tests.test_plugin_config_secrets_at_save import _OldPlugin

LOAD = "aisc_backend.routers.plugin.plugin_loader.load_package"


class OneVersionOn(Recording):
    def setUp(self):
        super().setUp()
        from aisc_backend.routers.plugin import router

        self.plugins = TestAsyncClient(router)

    async def _install(self, version, package="demo-pkg", project=None):
        with mock.patch(LOAD, return_value={"DemoA": _OldPlugin()}):
            res = await self.plugins.post("", json={"package_name": package, "version": version,
                                                    "project_uuid": str((project or self.project).pid)})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()[0]["pid"]

    async def _install_two(self, version):
        """Installs a version of a package of two plugins, DemoA and DemoB; their pids by name."""
        with mock.patch(LOAD, return_value={"DemoA": _OldPlugin(), "DemoB": _OldPlugin()}):
            res = await self.plugins.post("", json={"package_name": "demo-pkg", "version": version,
                                                    "project_uuid": str(self.project.pid)})
        self.assertEqual(res.status_code, 200, res.text)
        return {p["name"]: p["pid"] for p in res.json()}

    def _on(self, project=None):
        return sorted((p.package_name, p.version) for p in
                      Plugin.objects.filter(project=project or self.project, enabled=True))

    def test_installing_a_version_turns_the_other_versions_of_its_package_off(self):
        async_to_sync(self._install)("0.1.0")
        async_to_sync(self._install)("0.2.0")
        self.assertEqual(self._on(), [("demo-pkg", "0.2.0")])
        self.assertEqual(Plugin.objects.filter(project=self.project, package_name="demo-pkg").count(), 2)

    def test_installing_an_older_version_again_turns_it_on_and_the_newer_off(self):
        async_to_sync(self._install)("0.1.0")
        async_to_sync(self._install)("0.2.0")
        async_to_sync(self._install)("0.1.0")
        self.assertEqual(self._on(), [("demo-pkg", "0.1.0")])

    def test_switching_a_version_on_turns_the_others_off(self):
        old = async_to_sync(self._install)("0.1.0")
        async_to_sync(self._install)("0.2.0")
        res = async_to_sync(self.plugins.patch)(f"/{old}/enabled", json={"enabled": True})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._on(), [("demo-pkg", "0.1.0")])

    def test_switching_one_plugin_of_an_older_version_on_brings_its_siblings_that_were_on_along(self):
        old = async_to_sync(self._install_two)("0.1.0")
        async_to_sync(self._install_two)("0.2.0")
        res = async_to_sync(self.plugins.patch)(f"/{old['DemoA']}/enabled", json={"enabled": True})
        self.assertEqual(res.status_code, 200, res.text)
        on = sorted((p.name, p.version) for p in Plugin.objects.filter(project=self.project, enabled=True))
        self.assertEqual(on, [("DemoA", "0.1.0"), ("DemoB", "0.1.0")])

    def test_a_sibling_that_was_off_in_the_version_turned_off_stays_off(self):
        old = async_to_sync(self._install_two)("0.1.0")
        new = async_to_sync(self._install_two)("0.2.0")
        async_to_sync(self.plugins.patch)(f"/{new['DemoB']}/enabled", json={"enabled": False})
        async_to_sync(self.plugins.patch)(f"/{old['DemoA']}/enabled", json={"enabled": True})
        on = sorted((p.name, p.version) for p in Plugin.objects.filter(project=self.project, enabled=True))
        self.assertEqual(on, [("DemoA", "0.1.0")])

    def test_another_package_is_left_alone(self):
        async_to_sync(self._install)("1.0", package="other-pkg")
        async_to_sync(self._install)("0.1.0")
        async_to_sync(self._install)("0.2.0")
        self.assertEqual(self._on(), [("demo-pkg", "0.2.0"), ("other-pkg", "1.0")])

    def test_the_projects_plugins_list_the_version_on_first_within_its_package(self):
        """The settings page is opened by name and takes the first plugin of that name: the one on."""
        async_to_sync(self._install)("0.3.0")
        async_to_sync(self._install)("0.2.8")            # older, now on; 0.3.0 off
        async_to_sync(self._install)("1.0", package="a-pkg")
        listed = [(p.package_name, p.version, p.enabled) for p in self.project.get_plugins()]
        self.assertEqual(listed, [("a-pkg", "1.0", True), ("demo-pkg", "0.2.8", True), ("demo-pkg", "0.3.0", False)])
        async_to_sync(self._install)("0.3.0")
        listed = [(p.package_name, p.version, p.enabled) for p in self.project.get_plugins()]
        self.assertEqual(listed, [("a-pkg", "1.0", True), ("demo-pkg", "0.3.0", True), ("demo-pkg", "0.2.8", False)])


class TheLedgerSaysWhichVersionsWentOff(OneVersionOn):
    """The versions turned off ride in the event the request already writes (plugin.installed, or the
    switch's engine.plugin.toggled), never as events of their own: the ledger ties each event to its
    request's route and item, and a toggle of another plugin under an install or under another plugin's
    switch is refused."""

    def _events(self):
        from aisc_backend.tests.test_ledger_events import events

        return async_to_sync(events)()

    def test_an_install_names_the_versions_it_turned_off(self):
        async_to_sync(self._install)("0.1.0")
        async_to_sync(self._install)("0.2.0")
        last = self._events()[-1]
        self.assertEqual(last["action"], "plugin.installed")
        self.assertEqual(last["details"]["turned_off"], ["0.1.0"])
        self.assertEqual([e["action"] for e in self._events()], ["plugin.installed", "plugin.installed"])

    def test_a_first_install_says_nothing_of_other_versions(self):
        async_to_sync(self._install)("0.1.0")
        self.assertNotIn("turned_off", self._events()[-1]["details"])

    def test_a_switch_on_names_the_versions_it_turned_off_in_its_own_event(self):
        old = async_to_sync(self._install)("0.1.0")
        async_to_sync(self._install)("0.2.0")
        async_to_sync(self.plugins.patch)(f"/{old}/enabled", json={"enabled": True})
        last = self._events()[-1]
        self.assertEqual((last["action"], last["item_id"]), ("engine.plugin.toggled", old))
        self.assertEqual(last["details"], {"enabled": True, "turned_off": ["0.2.0"]})
        self.assertEqual([e["action"] for e in self._events()].count("engine.plugin.toggled"), 1)
