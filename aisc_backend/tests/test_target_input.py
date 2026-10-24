"""Every evaluation names its target.

In configurator mode the plugin input definitions the evaluation form reads end with one more,
required `resource` input, `target`: the assessed system or one of its components, whose engine
components the platform keeps (`target:<platform pid>/<key>`). The upstream plugin route is not
touched: a route of this module is mounted in front of it, only in configurator mode, and reuses
its handler. The form's own required-input check then asks every run for a target, and upstream's
create route stores the pick as an ordinary EvaluationInput. Standalone is unchanged.
"""
import unittest
import unittest.mock as mock
import uuid
from pathlib import Path

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings

from aisc_backend.routers import target_input
from aisc_plugin_interface import (
    BaseEvaluationPlugin, CsvInputProvider, InputDefinition, InputType, OnnxInputProvider, assesses,
    evaluation_input, helper_model, inputs_access, model_session, onnx, system_under_test_access,
)
from aisc_plugin_interface.system_under_test import system_under_test

URLS = Path(__file__).resolve().parents[2] / "config" / "urls.py"


def plugin_with(*defs):
    return mock.AsyncMock(return_value=list(defs))


DATASET = InputDefinition(name="dataset", label="Dataset", input_type=InputType.DATASET, required=True)


ENV = {"AISC_TARGET_BASE_URL": "base_url"}


class Unadopted(BaseEvaluationPlugin):
    """A plugin that declares nothing about what it assesses."""


def with_prompts(cls):
    return evaluation_input(name="prompts", label="Prompts", input_provider_class=CsvInputProvider,
                            input_type=InputType.DATASET)(cls)


def with_model_file(cls):
    return evaluation_input(name="model", label="Model", input_provider_class=OnnxInputProvider,
                            input_type=InputType.MODEL)(cls)


@helper_model("judge", "Judge model", required=True)
@assesses(card=system_under_test_access(protocols=("openai",), env=ENV))
@with_prompts
class CardOnly(BaseEvaluationPlugin):
    pass


@assesses(card=inputs_access())
@with_prompts
class ReadsItsInputs(BaseEvaluationPlugin):
    pass


@assesses(local_model=onnx(input="model"))
@with_model_file
@with_prompts
class LocalOnly(BaseEvaluationPlugin):
    pass


@assesses(card=model_session(outputs={"label": "label", "probability": "probability"}),
          local_model=onnx(input="model"))
@with_prompts
@with_model_file
class CardOrLocal(BaseEvaluationPlugin):
    pass


@helper_model("attacker", "Attacker", required=False)
@assesses(card=system_under_test_access(protocols=("openai",), env=ENV))
@with_prompts
@helper_model("judge", "Judge model", required=True)
class TwoHelpers(BaseEvaluationPlugin):
    pass


@system_under_test(protocols=("openai",), env=ENV)
@with_prompts
class OldDeclaration(BaseEvaluationPlugin):
    pass


@with_prompts
@helper_model("judge", "Judge model", required=True)
class HelperButNothingAssessed(BaseEvaluationPlugin):
    pass


@override_settings(AISC_DEPLOYMENT="configurator")
class TheTargetInput(SimpleTestCase):
    def definitions(self, *defs):
        with mock.patch.object(target_input, "upstream_input_definitions", plugin_with(*defs)), \
             mock.patch.object(target_input, "loaded_plugin", mock.AsyncMock(return_value=Unadopted)), \
             mock.patch.object(target_input, "ensure_system_target", mock.AsyncMock()) as ensured:
            out = async_to_sync(target_input.input_definitions_with_target)(mock.Mock(), uuid.uuid4())
        return out, ensured

    def test_every_plugin_ends_with_the_required_target_input(self):
        out, _ = self.definitions(DATASET)
        self.assertEqual([d.name for d in out], ["dataset", "target"])
        target = out[-1]
        self.assertEqual((target.input_type, target.required), (InputType.RESOURCE, True))
        self.assertIn("system", target.label.lower())

    def test_a_plugin_that_declares_target_is_not_given_a_second(self):
        own = InputDefinition(name="target", label="Mine", input_type=InputType.RESOURCE, required=True)
        out, _ = self.definitions(DATASET, own)
        self.assertEqual([d.name for d in out], ["dataset", "target"])
        self.assertEqual(out[-1].label, "Mine")

    def test_a_plugins_own_optional_target_is_required_here(self):
        # Every evaluation names a target; a plugin's own declaration (optional, so standalone
        # keeps working) is made required in configurator mode
        own = InputDefinition(name="target", label="Mine", input_type=InputType.RESOURCE, required=False)
        out, _ = self.definitions(DATASET, own)
        self.assertEqual([(d.name, d.required, d.label) for d in out], [("dataset", True, "Dataset"), ("target", True, "Mine")])

    def test_the_system_target_is_made_sure_of_so_there_is_always_one_to_pick(self):
        _, ensured = self.definitions(DATASET)
        ensured.assert_awaited_once()

    def test_the_system_target_failing_never_hides_the_definitions(self):
        with mock.patch.object(target_input, "upstream_input_definitions", plugin_with(DATASET)), \
             mock.patch.object(target_input, "loaded_plugin", mock.AsyncMock(return_value=Unadopted)), \
             mock.patch.object(target_input, "ensure_system_target", mock.AsyncMock(side_effect=RuntimeError("db"))):
            out = async_to_sync(target_input.input_definitions_with_target)(mock.Mock(), uuid.uuid4())
        self.assertEqual([d.name for d in out], ["dataset", "target"])


@override_settings(AISC_DEPLOYMENT="configurator")
class WhatIsAssessedComesFirst(SimpleTestCase):
    """The form asks what is assessed first, offering only the kinds the plugin declares; the webapp lists
    `resource` components (the AI card's targets) for a RESOURCE input and `model` components (local
    model files) for a MODEL input, so an undeclared kind cannot be picked."""

    def form(self, plugin):
        with mock.patch.object(target_input, "upstream_input_definitions", plugin_with(*plugin._input_definitions)), \
             mock.patch.object(target_input, "loaded_plugin", mock.AsyncMock(return_value=plugin)), \
             mock.patch.object(target_input, "ensure_system_target", mock.AsyncMock()) as ensured:
            out = async_to_sync(target_input.input_definitions_with_target)(mock.Mock(), uuid.uuid4())
        return out, ensured

    @staticmethod
    def shown(out):
        return [(d.name, d.label, d.input_type, d.required) for d in out]

    def test_a_card_only_plugin_asks_for_an_ai_card_target_first(self):
        out, ensured = self.form(CardOnly)
        self.assertEqual(self.shown(out)[:2], [
            ("target", "What is assessed (AI card)", InputType.RESOURCE, True),
            ("prompts", "Prompts", InputType.DATASET, True)])
        ensured.assert_awaited_once()

    def test_a_card_only_plugin_without_its_own_target_input_is_given_one(self):
        out, _ = self.form(ReadsItsInputs)
        self.assertEqual(self.shown(out), [
            ("target", "What is assessed (AI card)", InputType.RESOURCE, True),
            ("prompts", "Prompts", InputType.DATASET, True)])

    def test_an_old_declaration_alone_reads_as_card_only(self):
        out, _ = self.form(OldDeclaration)
        self.assertEqual(self.shown(out), [
            ("target", "What is assessed (AI card)", InputType.RESOURCE, True),
            ("prompts", "Prompts", InputType.DATASET, True)])

    def test_a_local_only_plugin_asks_for_its_model_file_and_no_card_target(self):
        out, ensured = self.form(LocalOnly)
        self.assertEqual(self.shown(out), [
            ("model", "What is assessed (local model file)", InputType.MODEL, True),
            ("prompts", "Prompts", InputType.DATASET, True)])
        ensured.assert_not_awaited()

    def test_a_plugin_of_both_kinds_offers_both_optional(self):
        out, ensured = self.form(CardOrLocal)
        self.assertEqual(self.shown(out), [
            ("target", "What is assessed: from the AI card", InputType.RESOURCE, False),
            ("model", "What is assessed: or a local model file", InputType.MODEL, False),
            ("prompts", "Prompts", InputType.DATASET, True)])
        ensured.assert_awaited_once()

    def test_helper_models_come_last_labelled_not_assessed(self):
        out, _ = self.form(TwoHelpers)
        self.assertEqual(self.shown(out), [
            ("target", "What is assessed (AI card)", InputType.RESOURCE, True),
            ("prompts", "Prompts", InputType.DATASET, True),
            ("helper:judge", "Helper model · Judge (not assessed)", InputType.LLM, True),
            ("helper:attacker", "Helper model · Attacker (not assessed)", InputType.LLM, False)])

    def test_a_card_only_plugins_helper_follows_its_other_inputs(self):
        out, _ = self.form(CardOnly)
        self.assertEqual([d.name for d in out], ["target", "prompts", "helper:judge"])
        self.assertEqual(out[-1].label, "Helper model · Judge (not assessed)")

    def test_a_plugin_that_declares_nothing_keeps_todays_form(self):
        out, ensured = self.form(with_prompts(type("Plain", (Unadopted,), {})))
        self.assertEqual(self.shown(out), [
            ("prompts", "Prompts", InputType.DATASET, True),
            ("target", target_input.TARGET_LABEL, InputType.RESOURCE, True)])
        ensured.assert_awaited_once()

    def test_a_plugin_with_a_helper_but_nothing_assessed_keeps_todays_form(self):
        out, _ = self.form(HelperButNothingAssessed)
        self.assertEqual(self.shown(out), [
            ("helper:judge", "Judge model", InputType.LLM, True),
            ("prompts", "Prompts", InputType.DATASET, True),
            ("target", target_input.TARGET_LABEL, InputType.RESOURCE, True)])


@override_settings(AISC_DEPLOYMENT="standalone")
class StandaloneIsUnchanged(SimpleTestCase):
    def test_an_adopted_plugins_inputs_are_as_it_declares_them(self):
        with mock.patch.object(target_input, "upstream_input_definitions", plugin_with(*TwoHelpers._input_definitions)), \
             mock.patch.object(target_input, "loaded_plugin", mock.AsyncMock(return_value=TwoHelpers)) as loaded:
            out = async_to_sync(target_input.input_definitions_with_target)(mock.Mock(), uuid.uuid4())
        self.assertEqual(out, list(TwoHelpers._input_definitions))
        loaded.assert_not_awaited()

    def test_no_target_input_in_standalone(self):
        with mock.patch.object(target_input, "upstream_input_definitions", plugin_with(DATASET)):
            out = async_to_sync(target_input.input_definitions_with_target)(mock.Mock(), uuid.uuid4())
        self.assertEqual([d.name for d in out], ["dataset"])


class Mounting(SimpleTestCase):
    def test_the_route_is_mounted_in_front_of_the_plugin_router_in_configurator_mode_only(self):
        text = URLS.read_text()
        branch = text.index("if deployment.is_configurator():")
        mounted = text.index('v1_router.add_router("/plugins", target_input_router)')
        upstream = text.index('v1_router.add_router("/plugins", plugin_router)')
        self.assertLess(branch, mounted)
        self.assertLess(mounted, upstream)
        self.assertIn('v1_router.add_router("/projects/for-platform", platform_project_router)',
                      text[branch:mounted + 200])

    def test_the_upstream_handler_is_reused_not_copied(self):
        from aisc_backend.routers import plugin
        self.assertIs(target_input.upstream_input_definitions, plugin.get_plugin_input_definitions)


class SystemTargetValue(SimpleTestCase):
    def test_the_mirror_is_found_by_the_platforms_value(self):
        pid = uuid.uuid4()
        self.assertEqual(target_input.system_reference(pid), f"target:{pid}/system")


if __name__ == "__main__":
    unittest.main()


from django.test import TestCase  # noqa: E402


class TheSystemTargetIsMadeOnce(TestCase):
    def test_made_once_as_a_resource_the_platform_finds_by_its_value(self):
        from aisc_backend.models import AIComponent, AISystem, Project
        platform_pid = uuid.uuid4()
        project = Project.objects.create(name="MCAS", platform_project_id=platform_pid)
        system = AISystem.objects.filter(project=project).first() or AISystem.objects.create(project=project, name="MCAS")
        with mock.patch.object(target_input, "latest_system_pid_sync", return_value=uuid.uuid4()):
            async_to_sync(target_input.ensure_system_target)()
            async_to_sync(target_input.ensure_system_target)()
        made = AIComponent.objects.filter(system=system, json_value__value=f"target:{platform_pid}/system")
        self.assertEqual(made.count(), 1)
        # named as the platform names it; the platform renames it once it knows the connection
        self.assertEqual((made[0].name, made[0].component_type),
                         ("AI card · System: MCAS · not connected", "resource"))


@override_settings(AISC_DEPLOYMENT="configurator")
class NoSystemTargetBeforeTheAICard(TestCase):
    """A project used only for the engine (and Superset) never saved an AI card version: it has no AI
    card targets, so the system is not offered until card version 1 exists."""

    def setUp(self):
        from aisc_backend.models import AISystem, Project
        self.project = Project.objects.create(name="Research", platform_project_id=uuid.uuid4())
        self.system = (AISystem.objects.filter(project=self.project).first()
                       or AISystem.objects.create(project=self.project, name="Research"))

    def test_the_system_is_offered_once_the_project_has_a_card_version(self):
        with mock.patch.object(target_input, "latest_system_pid_sync", return_value=None) as latest:
            self.assertFalse(target_input.system_target_offered(self.project))
        latest.assert_called_once_with(self.project.platform_project_id, using=self.project._state.db)
        with mock.patch.object(target_input, "latest_system_pid_sync", return_value=uuid.uuid4()):
            self.assertTrue(target_input.system_target_offered(self.project))

    def test_an_already_mirrored_system_costs_no_card_version_query(self):
        from aisc_backend.models import AIComponent
        AIComponent.objects.create(system=self.system, name="AI card · System: Research", component_type="resource",
                                   json_value={"value": target_input.system_reference(self.project.platform_project_id)})
        with mock.patch.object(target_input, "latest_system_pid_sync") as latest:
            async_to_sync(target_input.ensure_system_target)()
        latest.assert_not_called()

    def test_a_card_only_plugins_target_options_have_no_system_without_a_card_version(self):
        from aisc_backend.models import AIComponent
        with mock.patch.object(target_input, "upstream_input_definitions", plugin_with(*CardOnly._input_definitions)), \
             mock.patch.object(target_input, "loaded_plugin", mock.AsyncMock(return_value=CardOnly)), \
             mock.patch.object(target_input, "latest_system_pid_sync", return_value=None):
            out = async_to_sync(target_input.input_definitions_with_target)(mock.Mock(), uuid.uuid4())
        self.assertEqual(out[0].name, "target")
        # the form lists the system's `resource` components for the target input
        offered = AIComponent.objects.filter(system=self.system, component_type="resource")
        self.assertFalse(offered.filter(json_value__value=target_input.system_reference(
            self.project.platform_project_id)).exists())


class WhoAnswersTheForm(SimpleTestCase):
    """The mode is read once, at start: a child process per mode resolves the form's URL."""

    PROBE = (
        "import os, uuid, django\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n"
        "django.setup()\n"
        "from django.urls import resolve\n"
        "m = resolve(f'/api/v1/plugins/{uuid.uuid4()}/input_definitions')\n"
        "views = [c.cell_contents for c in (m.func.__closure__ or []) if hasattr(c.cell_contents, 'operations')]\n"
        "print([op.view_func.__module__ + '.' + op.view_func.__name__ for v in views for op in v.operations])\n"
    )

    def handler(self, **env):
        import os
        import subprocess
        import sys
        run = subprocess.run([sys.executable, "-c", self.PROBE], cwd=URLS.parents[1], capture_output=True,
                             text=True, env={**os.environ, **env}, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        return run.stdout.strip().splitlines()[-1]

    def test_configurator_answers_from_this_module(self):
        self.assertIn("target_input.input_definitions_with_target",
                      self.handler(AISC_DEPLOYMENT="configurator", DB_ENGINE="django.db.backends.postgresql",
                                   DJANGO_SECRET_KEY="s", RUN_TICKET_KEY="t", AUTH_ENABLED="true"))

    def test_standalone_answers_from_the_upstream_route(self):
        self.assertIn("routers.plugin.get_plugin_input_definitions", self.handler(AISC_DEPLOYMENT="standalone"))
