"""What each installed test declares about what it assesses, for the platform's target page.

The platform lists, under each target, the installed tests that can assess it: those whose kinds
include an AI card target, and of those that call the target only for a target that has a
connection. GET /plugins/declarations gives it, per enabled plugin of the project, what the plugin
interface reads from the plugin class: its target kinds, the protocols its tool speaks to a card
target's connection, and whether a card target must have one. For the plugin check
(scripts/verify-plugins.sh) it also says whether the plugin adopted @assesses, its helper roles, and the
names of the config fields its form asks a person for (names only, never a value).
"""
import json
import unittest.mock as mock
import uuid
from types import SimpleNamespace

from pydantic import BaseModel

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings

from aisc_backend.routers import target_input
from aisc_backend.tests.test_target_input import CardOnly, CardOrLocal, LocalOnly, OldDeclaration, ReadsItsInputs, \
    TwoHelpers, Unadopted
from aisc_plugin_interface import BaseEvaluationPlugin, InputType, assesses, evaluation_input, inputs_access, \
    system_under_test_access


class Endpoint(BaseModel):
    base_url: str = ""
    model: str = ""


class AskingConfig(BaseModel):
    rounds: int = 3
    note: str = "never-shown-default"
    target: Endpoint = Endpoint()
    target_api_key: str = ""


@assesses(card=system_under_test_access(protocols=("openai",), fields={"target.base_url": "base_url",
                                                                       "target_api_key": "api_key"}))
@evaluation_input(name="helper:critic", label="Critic", input_type=InputType.LLM)
class WithAConfigForm(BaseEvaluationPlugin[AskingConfig]):
    # what the platform fills is hidden from the person; target.model is still asked for
    form_ui_schema = {"target_api_key": {"ui:widget": "hidden"}, "target": {"base_url": {"ui:widget": "hidden"}}}


class SchemaThatDoesNotBuild(BaseModel):
    rounds: int = 3

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        raise TypeError("a field type with no JSON schema")


@assesses(card=inputs_access())
class FormThatDoesNotBuild(BaseEvaluationPlugin[SchemaThatDoesNotBuild]):
    pass


class NarrowForm(BaseModel):
    rounds: int = 3


class WiderForm(NarrowForm):
    threshold: float = 0.5
    target_model_id: str = ""


@assesses(card=inputs_access())
class OverridesItsForm(BaseEvaluationPlugin[NarrowForm]):
    """Its form is another model than its generic argument, as ClassificationPerformancePlugin's."""
    form_ui_schema = {"target_model_id": {"ui:widget": "hidden"}}

    @property
    def config_type(self):
        return WiderForm


@assesses(card=inputs_access())
class ChangesItsForm(BaseEvaluationPlugin[NarrowForm]):
    """Its form is shaped at run time (on_config_change), as the engine's form shows it."""

    def on_config_change(self, form_data):
        schema, ui = self.get_full_schema()
        schema = {**schema, "properties": {**schema["properties"], "added": {"type": "string"}}}
        return form_data, schema, {**ui, "rounds": {"ui:widget": "hidden"}}


class FallbackForm(BaseModel):
    rounds: int = 3
    target_model_id: str = "plugins-own-model"
    target: Endpoint = Endpoint(model="plugins-own-nested-model")


@assesses(card=inputs_access())
class HidesAFallback(BaseEvaluationPlugin[FallbackForm]):
    """Hidden fields with defaults of their own: a run with no card target would use them."""
    form_ui_schema = {"target_model_id": {"ui:widget": "hidden"}, "target": {"ui:widget": "hidden"}}


def instance_of(cls):
    """An instance of the plugin class (the fixtures leave evaluate abstract: a subclass gives it)."""
    return type(cls.__name__, (cls,), {"evaluate": lambda self, config_data: None})()


def row(name, display_name=None):
    return SimpleNamespace(pid=uuid.uuid4(), name=name, display_name=display_name or name.title(),
                           package_name=f"aisc-{name}", version="1.0.0")


@override_settings(AISC_DEPLOYMENT="configurator")
class TheDeclarations(SimpleTestCase):
    def declarations(self, classes):
        rows = [row(name) for name in classes]
        # the loader gives an instance of each plugin class, as the engine's plugin loader does
        load = mock.Mock(side_effect=lambda package, name, version: instance_of(classes[name]))
        with mock.patch.object(target_input, "_enabled_plugins", mock.AsyncMock(return_value=rows)), \
             mock.patch.object(target_input.plugin_routes.plugin_loader, "load_plugin", load):
            out = async_to_sync(target_input.plugin_declarations)(mock.Mock())
        return {d["name"]: d for d in out}, rows

    def test_each_enabled_plugin_says_what_it_assesses_and_how_it_reaches_a_card_target(self):
        out, rows = self.declarations({"chat": CardOnly, "inputs": ReadsItsInputs, "local": LocalOnly,
                                       "both": CardOrLocal, "old": OldDeclaration, "plain": Unadopted})
        # the keys the platform's target page reads (platform_service/targets.py), unchanged
        page_keys = ("pid", "name", "display_name", "package_name", "version", "target_kinds", "card_protocols",
                     "card_needs_connection")
        self.assertEqual({k: out["chat"][k] for k in page_keys},
                         {"pid": str(rows[0].pid), "name": "chat", "display_name": "Chat",
                          "package_name": "aisc-chat", "version": "1.0.0", "target_kinds": ["card"],
                          "card_protocols": ["openai"], "card_needs_connection": True})
        self.assertEqual((out["chat"]["adopted"], out["chat"]["helper_roles"]), (True, ["judge"]))
        self.assertEqual((out["inputs"]["target_kinds"], out["inputs"]["card_needs_connection"]), (["card"], False))
        self.assertEqual((out["local"]["target_kinds"], out["local"]["card_needs_connection"]), (["local_model"], False))
        self.assertEqual(out["both"]["target_kinds"], ["card", "local_model"])
        self.assertEqual((out["old"]["target_kinds"], out["old"]["card_protocols"]), (["card"], ["openai"]))
        self.assertEqual((out["plain"]["target_kinds"], out["plain"]["card_protocols"],
                          out["plain"]["card_needs_connection"]), ([], [], False))

    def test_each_plugin_says_whether_it_adopted_the_declaration_of_what_it_assesses(self):
        out, _ = self.declarations({"chat": CardOnly, "local": LocalOnly, "old": OldDeclaration, "plain": Unadopted})
        self.assertEqual({name: d["adopted"] for name, d in out.items()},
                         {"chat": True, "local": True, "old": False, "plain": False})

    def test_each_plugin_gives_its_helper_roles_with_a_helper_input_declared_by_hand(self):
        out, _ = self.declarations({"two": TwoHelpers, "form": WithAConfigForm, "plain": Unadopted})
        self.assertEqual(out["two"]["helper_roles"], ["judge", "attacker"])
        self.assertEqual(out["form"]["helper_roles"], ["critic"])
        self.assertEqual(out["plain"]["helper_roles"], [])

    def test_each_plugin_gives_the_names_of_the_config_fields_a_person_is_asked_for_never_a_value(self):
        out, _ = self.declarations({"form": WithAConfigForm, "plain": Unadopted})
        self.assertEqual(out["form"]["config_fields"], ["note", "rounds", "target.model"])
        self.assertEqual(out["plain"]["config_fields"], [])
        self.assertNotIn("never-shown-default", json.dumps(list(out.values())))

    def test_a_config_form_that_cannot_be_built_is_flagged_and_the_test_still_listed(self):
        out, _ = self.declarations({"broken": FormThatDoesNotBuild, "form": WithAConfigForm})
        self.assertEqual((out["broken"]["config_fields"], out["broken"]["config_fields_unreadable"]), ([], True))
        self.assertEqual(out["broken"]["target_kinds"], ["card"])
        self.assertFalse(out["form"]["config_fields_unreadable"])

    def test_the_config_fields_are_those_of_the_form_the_engine_shows(self):
        out, _ = self.declarations({"wider": OverridesItsForm, "changed": ChangesItsForm})
        self.assertEqual(out["wider"]["config_fields"], ["rounds", "threshold"])
        self.assertEqual(out["changed"]["config_fields"], ["added"])

    def test_each_hidden_config_field_says_only_whether_its_default_is_empty(self):
        out, _ = self.declarations({"form": WithAConfigForm, "fallback": HidesAFallback})
        self.assertEqual(out["form"]["hidden_config_fields"], {"target.base_url": True, "target_api_key": True})
        self.assertEqual(out["fallback"]["hidden_config_fields"],
                         {"target.base_url": True, "target.model": False, "target_model_id": False})
        self.assertEqual(out["fallback"]["config_fields"], ["rounds"])
        self.assertNotIn("plugins-own", json.dumps(list(out.values())))

    def test_a_plugin_that_does_not_load_is_left_out_not_the_whole_list(self):
        rows = [row("chat"), row("broken")]

        def load(package, name, version):
            if name == "broken":
                raise RuntimeError("no such package")
            return instance_of(CardOnly)
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
