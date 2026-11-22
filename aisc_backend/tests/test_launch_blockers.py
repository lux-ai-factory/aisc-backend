"""Which tests cannot be launched yet, and why, for the launch page.

A test that calls its target (an AI card target reached through a connection, `card_needs_connection`)
can only fail while no target of the project's AI card has a connection. GET /plugins/launch-blockers
names those tests (by plugin pid) with the reason, so the launch page greys them out and they cannot be
ticked. A test that reads its inputs, or that may assess a local model file instead, is never blocked.
When the platform cannot say whether a target is connected, nothing is blocked: the launch check
(routers/evaluation.py, check_assessed) decides then, as before.
"""
import unittest.mock as mock

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase, override_settings

from aisc_backend import plugin_declarations
from aisc_backend.routers import target_input
from aisc_backend.tests.stored_manifest import declared_of
from aisc_backend.tests.test_plugin_declarations import instance_of, row
from aisc_backend.tests.test_target_input import CardOnly, CardOrLocal, LocalOnly, OldDeclaration, ReadsItsInputs, \
    Unadopted

PLATFORM_PID = "11111111-2222-3333-4444-555555555555"


@override_settings(AISC_DEPLOYMENT="configurator")
class TheLaunchBlockers(SimpleTestCase):
    def blockers(self, classes, targets, connected=None, unreadable=()):
        """The route's answer by plugin name, for these plugin classes, card target keys and connections
        (key -> True, False or None when the platform cannot tell)."""
        rows = [row(name) for name in classes]
        by_pid = {str(r.pid): r.name for r in rows}

        async def declared(plugin_row):
            if plugin_row.name in unreadable:
                raise ValueError("this manifest does not load")
            return declared_of(instance_of(classes[plugin_row.name]), plugin_row)

        asked = []

        def is_connected(platform_pid, key):
            asked.append((platform_pid, key))
            return (connected or {}).get(key, False)

        with mock.patch.object(target_input, "_enabled_plugins", mock.AsyncMock(return_value=rows)), \
             mock.patch.object(plugin_declarations, "declared", side_effect=declared), \
             mock.patch.object(target_input, "_card_targets", mock.AsyncMock(return_value=(PLATFORM_PID, targets))), \
             mock.patch.object(target_input, "card_target_connected", side_effect=is_connected):
            out = async_to_sync(target_input.launch_blockers)(mock.Mock())
        self.asked = asked
        return {by_pid[pid]: reason for pid, reason in out.items()}

    def test_a_test_that_calls_its_target_is_blocked_while_no_target_has_a_connection(self):
        out = self.blockers({"chat": CardOnly, "old": OldDeclaration}, ["system", "component:x"])
        self.assertEqual(set(out), {"chat", "old"})
        self.assertIn("No target of the AI card has a connection yet", out["chat"])
        self.assertIn("Manage", out["chat"])

    def test_it_is_blocked_when_the_card_has_no_target_at_all(self):
        self.assertEqual(set(self.blockers({"chat": CardOnly}, [])), {"chat"})

    def test_nothing_is_blocked_once_one_target_has_a_connection(self):
        out = self.blockers({"chat": CardOnly}, ["system", "component:x"], connected={"component:x": True})
        self.assertEqual(out, {})

    def test_nothing_is_blocked_when_the_platform_cannot_tell(self):
        self.assertEqual(self.blockers({"chat": CardOnly}, ["system"], connected={"system": None}), {})

    def test_a_target_the_platform_cannot_tell_about_does_not_hide_one_that_is_not_connected(self):
        # one target unknown and the other not connected: the platform could not tell for every target
        out = self.blockers({"chat": CardOnly}, ["system", "component:x"], connected={"system": None})
        self.assertEqual(out, {})

    def test_tests_that_do_not_call_a_target_are_never_blocked_nor_is_the_platform_asked(self):
        out = self.blockers({"inputs": ReadsItsInputs, "local": LocalOnly, "both": CardOrLocal, "plain": Unadopted},
                            ["system"])
        self.assertEqual(out, {})
        self.assertEqual(self.asked, [])

    def test_the_platform_is_asked_about_this_projects_targets(self):
        self.blockers({"chat": CardOnly}, ["system"])
        self.assertEqual(self.asked, [(PLATFORM_PID, "system")])

    def test_a_test_whose_declaration_does_not_load_does_not_hide_the_others(self):
        out = self.blockers({"broken": CardOnly, "chat": CardOnly}, ["system"], unreadable=("broken",))
        self.assertEqual(set(out), {"chat"})

    def test_a_project_not_linked_to_the_platform_blocks_nothing(self):
        rows = [row("chat")]

        async def declared(plugin_row):
            return declared_of(instance_of(CardOnly), plugin_row)
        with mock.patch.object(target_input, "_enabled_plugins", mock.AsyncMock(return_value=rows)), \
             mock.patch.object(plugin_declarations, "declared", side_effect=declared), \
             mock.patch.object(target_input, "_card_targets", mock.AsyncMock(return_value=(None, []))):
            self.assertEqual(async_to_sync(target_input.launch_blockers)(mock.Mock()), {})


class TheCardTargetKeys(SimpleTestCase):
    def test_keys_are_read_from_this_projects_mirrors_in_use_only(self):
        values = [f"target:{PLATFORM_PID}/system", f"target:{PLATFORM_PID}/component:abc",
                  "target:99999999-0000-0000-0000-000000000000/system", "connection:x/y", None]
        self.assertEqual(target_input.card_target_keys(PLATFORM_PID, values), ["system", "component:abc"])


class TheOpenProjectsTargets(TestCase):
    """_card_targets reads the open project's mirrors (component type `resource`), leaving retired ones out."""

    def test_the_targets_in_use_of_the_platform_project(self):
        import uuid
        from django.utils import timezone

        from aisc_backend.models import AIComponent, AISystem, Project

        pid = uuid.UUID(PLATFORM_PID)
        project = Project.objects.create(name="p", description="", platform_project_id=pid)
        system = AISystem.objects.create(project=project, name="sys", description="")
        AIComponent.objects.create(system=system, name="system", component_type="resource",
                                   json_value={"value": f"target:{pid}/system"})
        AIComponent.objects.create(system=system, name="gone", component_type="resource",
                                   json_value={"value": f"target:{pid}/component:old"}, retired_at=timezone.now())
        AIComponent.objects.create(system=system, name="file", component_type="dataset",
                                   json_value={"value": f"target:{pid}/component:data"})
        self.assertEqual(async_to_sync(target_input._card_targets)(), (PLATFORM_PID, ["system"]))

    def test_no_platform_project_means_no_targets(self):
        self.assertEqual(async_to_sync(target_input._card_targets)(), (None, []))
