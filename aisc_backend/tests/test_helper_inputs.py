"""A run's helper model is a platform reference, never a component of the AI system.

In configurator mode a project's helper models (a judge, an attacker...) live on the platform, under
Manage > Helper models. A plugin's `helper:<role>` input is then given the reference of one of them
(`helper:<platform pid>/<name>`) and the model picked for the run; the engine checks the reference
against the platform's list for that project and role, and stores it on the run's input with no
component. The form's definition of that input has the type `helper`, so no AI System component can
be listed for it, and the worker is handed the reference, never a component. The engine also lists the
project's helper models for the form, by reference, label, roles and endpoint host.

Standalone mode has no platform: a helper input is still filled from an `llm` component.
"""
import os
import unittest.mock as mock
import uuid

from asgiref.sync import async_to_sync
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend import platform_helpers
from aisc_backend.auth import keycloak
from aisc_backend.models import (
    AIComponent, AIComponentType, AISystem, Evaluation, EvaluationInput, EvaluationPlugin,
    EvaluationStatus, Plugin, PluginConfig, Project, ProjectStatus,
)
from aisc_backend.routers import evaluation as evaluation_routes
from aisc_backend.routers import helper_models, target_input
from aisc_backend.routers import plugin as plugin_routes
from aisc_backend.routers.internal import router as internal_router
from aisc_backend.routers.project import router as project_router
from aisc_backend.schemas.plugin import EvaluationInputOutSchema
from aisc_plugin_interface import (
    BaseEvaluationPlugin, InputType, assesses, helper_model, system_under_test_access,
)

SIGNED_IN = {"Authorization": "Bearer development"}
INTERNAL_SECRET = "test-internal-secret"
evaluations = TestAsyncClient(evaluation_routes.router)
helpers_client = TestAsyncClient(helper_models.router)
internal = TestAsyncClient(internal_router)
projects = TestAsyncClient(project_router)

FROM_MANAGE = "a helper model is picked from Manage > Helper models, not from the AI system's components"

_auth_off = None


def setUpModule():
    global _auth_off
    _auth_off = mock.patch.object(keycloak, "AUTH_ENABLED", False)
    _auth_off.start()


def tearDownModule():
    _auth_off.stop()


@helper_model("judge", "Judge model", required=True)
class UsesAJudge(BaseEvaluationPlugin):
    pass


@helper_model("judge", "Judge model", required=True)
@assesses(card=system_under_test_access(protocols=("openai",), env={"AISC_TARGET_BASE_URL": "base_url"}))
class JudgesTheCardTarget(BaseEvaluationPlugin):
    pass


def listed(platform_pid, name="judge-a", label="Judge A", roles=("judge",)):
    return {"name": name, "label": label, "roles": list(roles), "ref": f"helper:{platform_pid}/{name}",
            "endpoint_host": "llm.example.org"}


class Fixture:
    """A project linked to the platform, with an `llm` component of its AI system and one plugin."""

    def __init__(self):
        self.platform_pid = uuid.uuid4()
        self.project = Project.objects.create(name="MCAS", status=ProjectStatus.Ready,
                                              platform_project_id=self.platform_pid)
        system = AISystem.objects.create(project=self.project)
        self.llm = AIComponent.objects.create(
            system=system, name="Old judge", component_type=AIComponentType.LLM,
            json_value={"endpoint_url": "http://llm.example.org/v1", "secret_key": "judge_key"})
        plugin = Plugin.objects.create(name="Judged", display_name="Judged test", description="",
                                       package_name="judged", version="0.1", project=self.project,
                                       enabled=True)
        config = PluginConfig.objects.create(plugin=plugin, config={})
        plugin.current_config = config
        plugin.save()

    @property
    def ref(self):
        return f"helper:{self.platform_pid}/judge-a"


class RunsWithHelpers(TestCase):
    def setUp(self):
        self.f = Fixture()

    async def run_with(self, inputs, helpers=()):
        no_errors = {"missing": [], "invalid": [], "ambiguous": []}
        with mock.patch.object(evaluation_routes.plugin_loader, "load_plugin", return_value=UsesAJudge), \
             mock.patch.object(evaluation_routes, "validate_plugin_settings",
                               new=mock.AsyncMock(return_value=no_errors)), \
             mock.patch.object(platform_helpers, "list_helpers", return_value=list(helpers)) as listing, \
             mock.patch.object(evaluation_routes.celery_service, "run_evaluation",
                               new=mock.AsyncMock(return_value=mock.Mock(task_id=str(uuid.uuid4())))):
            response = await evaluations.post(
                "/task", headers=SIGNED_IN,
                json={"project_pid": str(self.f.project.pid),
                      "plugins_to_run": [{"name": "Judged", "inputs": inputs}]})
        return response, listing

    async def stored(self):
        return [i async for i in EvaluationInput.objects.all()]


@override_settings(AISC_DEPLOYMENT="configurator")
class InTheConfigurator(RunsWithHelpers):
    async def test_helper_input_is_stored_as_reference(self):
        response, listing = await self.run_with(
            [{"name": "helper:judge", "helper_ref": self.f.ref, "value": {"model": "gpt-4o-mini"}}],
            helpers=[listed(self.f.platform_pid)])
        self.assertEqual(response.status_code, 200, response.content)
        listing.assert_called_once_with(str(self.f.platform_pid), "judge")
        [stored] = await self.stored()
        self.assertIsNone(stored.component_id)
        self.assertEqual(stored.helper_ref, self.f.ref)
        self.assertEqual(stored.value, {"model": "gpt-4o-mini", "label": "Judge A"})

    async def test_helper_input_with_component_pid_is_refused_in_configurator(self):
        response, _ = await self.run_with(
            [{"name": "helper:judge", "pid": str(self.f.llm.pid), "value": {"model": "gpt-4o-mini"}}],
            helpers=[listed(self.f.platform_pid)])
        self.assertEqual(response.status_code, 400)
        self.assertIn(FROM_MANAGE, response.json()["detail"])
        self.assertFalse(await Evaluation.objects.aexists())

    async def test_helper_of_another_project_is_refused(self):
        elsewhere = uuid.uuid4()
        response, _ = await self.run_with(
            [{"name": "helper:judge", "helper_ref": f"helper:{elsewhere}/judge-a", "value": {"model": "m"}}],
            helpers=[listed(elsewhere)])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(await Evaluation.objects.aexists())

    async def test_helper_not_allowed_for_role_is_refused(self):
        response, _ = await self.run_with(
            [{"name": "helper:judge", "helper_ref": self.f.ref, "value": {"model": "m"}}],
            helpers=[listed(self.f.platform_pid, roles=("grader",))])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(await Evaluation.objects.aexists())

    async def test_helper_the_platform_does_not_list_is_refused(self):
        response, _ = await self.run_with(
            [{"name": "helper:judge", "helper_ref": self.f.ref, "value": {"model": "m"}}], helpers=[])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(await Evaluation.objects.aexists())

    async def test_helper_without_a_model_is_refused(self):
        response, _ = await self.run_with(
            [{"name": "helper:judge", "helper_ref": self.f.ref, "value": {}}],
            helpers=[listed(self.f.platform_pid)])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(await Evaluation.objects.aexists())

    async def test_a_helper_reference_on_another_input_is_refused(self):
        response, _ = await self.run_with(
            [{"name": "prompts", "helper_ref": self.f.ref, "value": {"model": "m"}}],
            helpers=[listed(self.f.platform_pid)])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(await Evaluation.objects.aexists())

    async def test_helpers_that_cannot_be_listed_refuse_the_run(self):
        no_errors = {"missing": [], "invalid": [], "ambiguous": []}
        with mock.patch.object(evaluation_routes.plugin_loader, "load_plugin", return_value=UsesAJudge), \
             mock.patch.object(evaluation_routes, "validate_plugin_settings",
                               new=mock.AsyncMock(return_value=no_errors)), \
             mock.patch.object(platform_helpers, "list_helpers",
                               side_effect=platform_helpers.HelpersUnavailable("the platform is down")):
            response = await evaluations.post(
                "/task", headers=SIGNED_IN,
                json={"project_pid": str(self.f.project.pid), "plugins_to_run": [{"name": "Judged", "inputs": [
                    {"name": "helper:judge", "helper_ref": self.f.ref, "value": {"model": "m"}}]}]})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(await Evaluation.objects.aexists())


@override_settings(AISC_DEPLOYMENT="configurator")
class TheHelperDefinition(TestCase):
    def test_helper_definition_type_is_helper(self):
        with mock.patch.object(target_input, "upstream_input_definitions",
                               mock.AsyncMock(return_value=list(JudgesTheCardTarget._input_definitions))), \
             mock.patch.object(target_input, "loaded_plugin", mock.AsyncMock(return_value=JudgesTheCardTarget)), \
             mock.patch.object(target_input, "ensure_system_target", mock.AsyncMock()):
            out = async_to_sync(target_input.input_definitions_with_target)(mock.Mock(), uuid.uuid4())
        [helper] = [d for d in out if d.name == "helper:judge"]
        self.assertEqual(helper.input_type, "helper")
        self.assertEqual(helper.label, "Helper model · Judge (not assessed)")


class StoredHelperInputs(TestCase):
    """What reads a run's inputs: the worker's feed, the form's prefill, the run's details."""

    def setUp(self):
        self.f = Fixture()
        self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=self.f.project)
        self.run_plugin = EvaluationPlugin.objects.create(
            evaluation=self.evaluation, plugin_config=PluginConfig.objects.get())
        self.helper = EvaluationInput.objects.create(
            evaluation_plugin=self.run_plugin, name="helper:judge", component=None, helper_ref=self.f.ref,
            value={"model": "gpt-4o-mini", "label": "Judge A"})

    def test_an_input_is_either_a_component_or_a_helper_reference(self):
        for component, ref in ((None, None), (self.f.llm, self.f.ref)):
            with self.subTest(component=component, ref=ref), self.assertRaises(IntegrityError), \
                 transaction.atomic():
                EvaluationInput.objects.create(evaluation_plugin=self.run_plugin, name="other",
                                               component=component, helper_ref=ref)

    async def test_worker_inputs_carry_helper_ref(self):
        with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}), \
             mock.patch.object(plugin_routes.plugin_loader, "load_plugin", return_value=UsesAJudge):
            response = await internal.get(f"/evaluations/{self.evaluation.pid}/inputs",
                                          headers={"X-Internal-Secret": INTERNAL_SECRET})
        self.assertEqual(response.status_code, 200, response.content)
        [entry] = response.json()[str(self.run_plugin.pid)]
        self.assertEqual(
            {k: entry[k] for k in ("name", "component_type", "helper_ref", "value")},
            {"name": "helper:judge", "component_type": "helper", "helper_ref": self.f.ref,
             "value": {"model": "gpt-4o-mini", "label": "Judge A"}})
        # no component: nothing of the AI system, no endpoint, no key
        self.assertFalse(entry["component_name"] or entry["data"] or entry["json_value"])
        self.assertIsNone(entry["endpoint_url"])
        self.assertIsNone(entry["secret_key"])
        self.assertIsNone(entry["secret_encrypted_value"])

    async def test_the_forms_prefill_carries_the_reference(self):
        response = await projects.get(f"/{self.f.project.pid}/evaluation-inputs-template")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["Judged"]["helper:judge"],
                         {"helper_ref": self.f.ref, "value": {"model": "gpt-4o-mini", "label": "Judge A"}})

    def test_the_runs_details_show_the_helper_not_a_component(self):
        shown = EvaluationInputOutSchema.from_orm(self.helper).model_dump()
        self.assertEqual((shown["input_type"], shown["input_file"], shown["helper_ref"]),
                         ("helper", None, self.f.ref))


@override_settings(AISC_DEPLOYMENT="configurator")
class TheProjectsHelpers(TestCase):
    def setUp(self):
        self.f = Fixture()

    async def test_the_projects_helpers_are_listed_by_reference(self):
        with mock.patch.object(platform_helpers, "list_helpers",
                               return_value=[listed(self.f.platform_pid)]) as listing:
            response = await helpers_client.get(f"/{self.f.project.pid}/helpers?role=judge", headers=SIGNED_IN)
        self.assertEqual(response.status_code, 200, response.content)
        listing.assert_called_once_with(str(self.f.platform_pid), "judge")
        self.assertEqual(response.json(), [{"ref": self.f.ref, "label": "Judge A", "roles": ["judge"],
                                            "endpoint_host": "llm.example.org"}])

    async def test_helpers_that_cannot_be_listed_answer_503(self):
        with mock.patch.object(platform_helpers, "list_helpers",
                               side_effect=platform_helpers.HelpersUnavailable("the platform is down")):
            response = await helpers_client.get(f"/{self.f.project.pid}/helpers", headers=SIGNED_IN)
        self.assertEqual(response.status_code, 503)


class AskingThePlatform(TestCase):
    """platform_helpers asks the platform's internal list route with the engine's token."""

    PLATFORM = {"PLATFORM_URL": "http://platform:8000/", "PLATFORM_ENGINE_TOKEN": "engine-token"}

    def test_the_list_route_is_asked_with_the_engine_token(self):
        pid = str(uuid.uuid4())
        with mock.patch.dict(os.environ, self.PLATFORM), \
             mock.patch.object(platform_helpers, "_get", return_value=(200, {"helpers": [listed(pid)]})) as get:
            out = platform_helpers.list_helpers(pid, "judge")
        self.assertEqual(out, [listed(pid)])
        get.assert_called_once_with(f"http://platform:8000/internal/projects/{pid}/helpers?role=judge",
                                    {"X-AISC-Service-Token": "engine-token"})

    def test_no_platform_settings_means_unavailable(self):
        with mock.patch.dict(os.environ, {"PLATFORM_URL": "", "PLATFORM_ENGINE_TOKEN": ""}), \
             self.assertRaises(platform_helpers.HelpersUnavailable):
            platform_helpers.list_helpers(str(uuid.uuid4()), None)

    def test_a_platform_that_does_not_answer_is_unavailable(self):
        for outcome in (mock.Mock(side_effect=OSError("refused")), mock.Mock(return_value=(401, None)),
                        mock.Mock(return_value=(200, {"nothing": []}))):
            with self.subTest(outcome=outcome), mock.patch.dict(os.environ, self.PLATFORM), \
                 mock.patch.object(platform_helpers, "_get", outcome), \
                 self.assertRaises(platform_helpers.HelpersUnavailable):
                platform_helpers.list_helpers(str(uuid.uuid4()), "judge")

    def test_a_role_the_platform_does_not_know_has_no_helpers(self):
        with mock.patch.dict(os.environ, self.PLATFORM), \
             mock.patch.object(platform_helpers, "_get", return_value=(422, None)):
            self.assertEqual(platform_helpers.list_helpers(str(uuid.uuid4()), "oracle"), [])


@override_settings(AISC_DEPLOYMENT="standalone")
class Standalone(RunsWithHelpers):
    async def test_standalone_keeps_llm_component_path(self):
        definitions = list(UsesAJudge._input_definitions)
        with mock.patch.object(target_input, "upstream_input_definitions", mock.AsyncMock(return_value=definitions)):
            out = await target_input.input_definitions_with_target(mock.Mock(), uuid.uuid4())
        [helper] = [d for d in out if d.name == "helper:judge"]
        self.assertEqual(helper.input_type, InputType.LLM)

        response, listing = await self.run_with(
            [{"name": "helper:judge", "pid": str(self.f.llm.pid), "value": {"model": "gpt-4o-mini"}}])
        self.assertEqual(response.status_code, 200, response.content)
        listing.assert_not_called()
        [stored] = await self.stored()
        self.assertEqual((stored.component_id, stored.helper_ref), (self.f.llm.id, None))

        response, _ = await self.run_with(
            [{"name": "helper:judge", "helper_ref": self.f.ref, "value": {"model": "gpt-4o-mini"}}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(await Evaluation.objects.acount(), 1)
