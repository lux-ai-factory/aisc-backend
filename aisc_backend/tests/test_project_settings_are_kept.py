"""A project's settings (engine ProjectConfig rows) are kept: deleting one marks it deleted instead of removing
it, so the plugin configurations of past runs still say which setting they referenced, and changing one records
the value it had and the value it takes, so a run can be told which value it got.

The routes are called as the door lets them run (a project admitted, LEDGER_MODE on), as in
test_ledger_events.py.
"""
import os
from unittest import mock

from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend import ledger, projectdb
from aisc_backend.models import (LedgerOutbox, Plugin, PluginConfig, PluginConfigProjectConfig, Project,
                                 ProjectConfig, ProjectConfigCategory, ProjectStatus)
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_backend.routers.project_config import router

PID = "4d3c2b1a-0f9e-4d8c-9b7a-665544332211"
REQUEST = "99999999-8888-4777-8666-555555555555"

settings_client = TestAsyncClient(router)


def unrecorded():
    return mock.patch.dict(os.environ, {"LEDGER_MODE": "off"})


class ProjectSettingsAreKept(TestCase):
    def setUp(self):
        super().setUp()
        inside = override_settings(AISC_DEPLOYMENT="configurator")    # an engine on its own records nothing
        inside.enable()
        self.addCleanup(inside.disable)
        patcher = mock.patch.dict(os.environ, {"LEDGER_MODE": "record"})
        patcher.start()
        self.addCleanup(patcher.stop)
        pid, request = projectdb.admitted_pid.set(PID), ledger.current_request.set(REQUEST)
        self.addCleanup(projectdb.admitted_pid.reset, pid)
        self.addCleanup(ledger.current_request.reset, request)
        with unrecorded():
            self.project = Project.objects.create(name="p", status=ProjectStatus.Ready, platform_project_id=PID)
            self.setting = ProjectConfig.objects.create(project=self.project, key="THRESHOLD", name="Threshold",
                                                        category=ProjectConfigCategory.VARIABLES,
                                                        json_value={"type": "number", "value": 0.5})
            plugin = Plugin.objects.create(project=self.project, name="A", display_name="A",
                                           package_name="aisc-plugin-a", version="0.1.0")
            self.plugin_config = PluginConfig.objects.create(plugin=plugin, config={"threshold": 0.5})
            self.link = PluginConfigProjectConfig.objects.create(plugin_config=self.plugin_config,
                                                                 project_config=self.setting,
                                                                 plugin_config_key="threshold")

    async def _delete(self):
        res = await settings_client.delete(f"/{self.project.pid}/{self.setting.pid}")
        self.assertEqual(res.status_code, 204, res.text)

    async def test_deleting_a_setting_keeps_its_row_marked_deleted(self):
        await self._delete()
        row = await ProjectConfig._base_manager.filter(pk=self.setting.pk).values().afirst()
        self.assertIsNotNone(row, "the deleted setting's row was removed, not marked deleted")
        self.assertIsNotNone(row.get("deleted_at"), "the kept setting is not marked deleted")

    async def test_a_deleted_setting_is_no_longer_listed(self):
        await self._delete()
        listing = await settings_client.get(f"/{self.project.pid}")
        self.assertEqual(listing.json(), [])
        row = await ProjectConfig._base_manager.filter(pk=self.setting.pk).values().afirst()
        self.assertIsNotNone(row, "the deleted setting's row was removed, not kept")

    async def test_deleting_a_setting_keeps_the_links_of_the_plugin_configurations_that_used_it(self):
        await self._delete()
        kept = await PluginConfigProjectConfig._base_manager.filter(pk=self.link.pk).aexists()
        self.assertTrue(kept, "deleting the setting removed the link of a plugin configuration that used it")

    async def test_changing_a_setting_records_the_value_it_had_and_the_new_one(self):
        # the PATCH route fails on SQLite before it writes (the async lazy load of project_config.project),
        # so its write is made here as the route makes it
        self.setting.json_value = {"type": "number", "value": 0.9}
        await ProjectConfigRepository().save(self.setting)
        events = [row.event async for row in LedgerOutbox.objects.order_by("id")]
        [event] = [e for e in events if e["action"] == "engine.setting.updated"]
        self.assertEqual(event.get("before"), {"json_value": {"type": "number", "value": 0.5}},
                         "the update event does not record the value the setting had")
        self.assertEqual(event.get("after"), {"json_value": {"type": "number", "value": 0.9}},
                         "the update event does not record the value the setting takes")

    async def test_a_deleted_settings_key_can_be_given_to_a_new_setting(self):
        await self._delete()
        res = await settings_client.post(f"/{self.project.pid}", json={
            "category": "variables", "key": "THRESHOLD", "name": "Threshold",
            "json_value": {"type": "number", "value": 0.7}})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([s["key"] for s in (await settings_client.get(f"/{self.project.pid}")).json()],
                         [res.json()["key"]])

    async def test_a_new_run_is_not_offered_a_deleted_setting(self):
        await self._delete()
        offered = [m async for m in self.plugin_config.setting_mappings.all()]
        self.assertEqual(offered, [])
