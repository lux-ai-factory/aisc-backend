"""What each installed test declares about what it assesses, for the platform's target page.

The platform lists, under each target, the installed tests that can assess it: those whose kinds
include an AI card target, and of those that call the target only for a target that has a
connection. GET /plugins/declarations gives it, per enabled plugin of the project, what the plugin
interface reads from the plugin class: its target kinds, the protocols its tool speaks to a card
target's connection, and whether a card target must have one.
"""
import unittest.mock as mock
import uuid
from types import SimpleNamespace

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings

from aisc_backend.routers import target_input
from aisc_backend.tests.test_target_input import CardOnly, CardOrLocal, LocalOnly, OldDeclaration, ReadsItsInputs, \
    Unadopted


def row(name, display_name=None):
    return SimpleNamespace(pid=uuid.uuid4(), name=name, display_name=display_name or name.title(),
                           package_name=f"aisc-{name}", version="1.0.0")


@override_settings(AISC_DEPLOYMENT="configurator")
class TheDeclarations(SimpleTestCase):
    def declarations(self, classes):
        rows = [row(name) for name in classes]
        load = mock.Mock(side_effect=lambda package, name, version: classes[name])
        with mock.patch.object(target_input, "_enabled_plugins", mock.AsyncMock(return_value=rows)), \
             mock.patch.object(target_input.plugin_routes.plugin_loader, "load_plugin", load):
            out = async_to_sync(target_input.plugin_declarations)(mock.Mock())
        return {d["name"]: d for d in out}, rows

    def test_each_enabled_plugin_says_what_it_assesses_and_how_it_reaches_a_card_target(self):
        out, rows = self.declarations({"chat": CardOnly, "inputs": ReadsItsInputs, "local": LocalOnly,
                                       "both": CardOrLocal, "old": OldDeclaration, "plain": Unadopted})
        self.assertEqual(out["chat"], {"pid": str(rows[0].pid), "name": "chat", "display_name": "Chat",
                                       "package_name": "aisc-chat", "version": "1.0.0", "target_kinds": ["card"],
                                       "card_protocols": ["openai"], "card_needs_connection": True})
        self.assertEqual((out["inputs"]["target_kinds"], out["inputs"]["card_needs_connection"]), (["card"], False))
        self.assertEqual((out["local"]["target_kinds"], out["local"]["card_needs_connection"]), (["local_model"], False))
        self.assertEqual(out["both"]["target_kinds"], ["card", "local_model"])
        self.assertEqual((out["old"]["target_kinds"], out["old"]["card_protocols"]), (["card"], ["openai"]))
        self.assertEqual((out["plain"]["target_kinds"], out["plain"]["card_protocols"],
                          out["plain"]["card_needs_connection"]), ([], [], False))

    def test_a_plugin_that_does_not_load_is_left_out_not_the_whole_list(self):
        rows = [row("chat"), row("broken")]

        def load(package, name, version):
            if name == "broken":
                raise RuntimeError("no such package")
            return CardOnly
        with mock.patch.object(target_input, "_enabled_plugins", mock.AsyncMock(return_value=rows)), \
             mock.patch.object(target_input.plugin_routes.plugin_loader, "load_plugin", mock.Mock(side_effect=load)):
            out = async_to_sync(target_input.plugin_declarations)(mock.Mock())
        self.assertEqual([d["name"] for d in out], ["chat"])

    def test_a_plugin_whose_declaration_cannot_be_read_is_left_out_not_the_whole_list(self):
        class Odd(Unadopted):
            target_kinds = 5  # not a tuple of kinds: reading it raises

        out, _ = self.declarations({"chat": CardOnly, "odd": Odd})
        self.assertEqual(list(out), ["chat"])

    def test_the_route_is_on_the_configurator_plugins_router(self):
        paths = {path for path, _ in target_input.router.path_operations.items()}
        self.assertIn("/declarations", paths)
