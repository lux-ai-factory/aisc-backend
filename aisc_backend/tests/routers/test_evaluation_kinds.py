"""An evaluation is refused when what it assesses is not what the plugin declared.

In configurator mode POST /evaluations/task checks, for a plugin that declares what it assesses
(@assesses, or an older declaration alone, read as a card target), that the run names only a
declared kind: a card target of this project (with a connection when the plugin's card access calls
the target), or the plugin's local model file, and for a plugin declaring both, exactly one of them. A refusal is a 422 naming the plugin and the
reason, and nothing is stored. Plugins that declare nothing, and standalone mode, are unchanged.
"""
import unittest.mock as mock
import uuid

from django.test import SimpleTestCase, TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend.auth import keycloak
from aisc_backend.models import (
    AIComponent, AIComponentType, AISystem, Evaluation, EvaluationInput, EvaluationPlugin, Plugin,
    PluginConfig, Project, ProjectStatus,
)
from aisc_backend.routers import evaluation as evaluation_routes
from aisc_plugin_interface import (
    BaseEvaluationPlugin, CsvInputProvider, InputType, OnnxInputProvider, assesses, evaluation_input,
    inputs_access, model_session, onnx, system_under_test_access,
)
from aisc_plugin_interface.system_under_test import system_under_test

SIGNED_IN = {"Authorization": "Bearer development"}
evaluations = TestAsyncClient(evaluation_routes.router)
ENV = {"AISC_TARGET_BASE_URL": "base_url"}

_auth_off = None


def setUpModule():
    global _auth_off
    _auth_off = mock.patch.object(keycloak, "AUTH_ENABLED", False)
    _auth_off.start()


def tearDownModule():
    _auth_off.stop()


def with_prompts(cls):
    return evaluation_input(name="prompts", label="Prompts", input_provider_class=CsvInputProvider,
                            input_type=InputType.DATASET)(cls)


def with_model_file(cls):
    return evaluation_input(name="model", label="Model", input_provider_class=OnnxInputProvider,
                            input_type=InputType.MODEL)(cls)


@assesses(card=system_under_test_access(protocols=("openai",), env=ENV))
@with_prompts
class CallsTheTarget(BaseEvaluationPlugin):
    pass


@assesses(card=inputs_access())
@with_prompts
class ReadsItsInputs(BaseEvaluationPlugin):
    pass


@assesses(local_model=onnx(input="model"))
@with_model_file
class LocalOnly(BaseEvaluationPlugin):
    pass


@assesses(card=model_session(outputs={"label": "label", "probability": "probability"}),
          local_model=onnx(input="model"))
@with_model_file
class CardOrLocal(BaseEvaluationPlugin):
    pass


@system_under_test(protocols=("openai",), env=ENV)
class OldDeclaration(BaseEvaluationPlugin):
    pass


@system_under_test(protocols=("openai",), env=ENV, required=False)
class OldOptionalDeclaration(BaseEvaluationPlugin):
    pass


class DeclaresNothing(BaseEvaluationPlugin):
    pass


class Fixture:
    """A project linked to the platform, its card targets' mirrors, and other components."""

    def __init__(self):
        self.platform_pid = uuid.uuid4()
        self.project = Project.objects.create(name="MCAS", status=ProjectStatus.Ready,
                                              platform_project_id=self.platform_pid)
        system = AISystem.objects.create(project=self.project)

        def component(name, kind, value=None):
            return AIComponent.objects.create(system=system, name=name, component_type=kind,
                                              json_value={"value": value} if value is not None else {})

        self.system_target = component("AI card · System: MCAS", AIComponentType.RESOURCE,
                                             f"target:{self.platform_pid}/system")
        self.other_projects_target = component("AI card · System: Elsewhere", AIComponentType.RESOURCE,
                                                     f"target:{uuid.uuid4()}/system")
        self.connection = component("Scoring API", AIComponentType.RESOURCE,
                                          f"connection:{self.platform_pid}/scoring")
        self.model_file = component("scorer.onnx", AIComponentType.MODEL)
        self.prompts = component("Prompts", AIComponentType.DATASET)


@override_settings(AISC_DEPLOYMENT="configurator")
class EvaluationKinds(TestCase):
    def setUp(self):
        self.f = Fixture()

    async def run_plugin(self, plugin_class, inputs, connected=None):
        plugin = await Plugin.objects.acreate(
            name="Scorer", display_name="Credit scorer", description="", package_name="scorer",
            version="0.1", project=self.f.project, enabled=True)
        config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        plugin.current_config = config
        await plugin.asave()
        no_errors = {"missing": [], "invalid": [], "ambiguous": []}
        with mock.patch.object(evaluation_routes.plugin_loader, "load_plugin", return_value=plugin_class), \
             mock.patch.object(evaluation_routes, "validate_plugin_settings",
                               new=mock.AsyncMock(return_value=no_errors)), \
             mock.patch.object(evaluation_routes, "card_target_connected", return_value=connected), \
             mock.patch.object(evaluation_routes.celery_service, "run_evaluation",
                               new=mock.AsyncMock(return_value=mock.Mock(task_id=str(uuid.uuid4())))):
            return await evaluations.post(
                "/task", headers=SIGNED_IN,
                json={"project_pid": str(self.f.project.pid), "plugins_to_run": [{"name": "Scorer", "inputs": [
                    {"name": name, "pid": str(component.pid)} for name, component in inputs]}]})

    async def assert_refused(self, response, reason):
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json(), {"detail": f"Credit scorer: {reason}"})
        self.assertEqual(await Evaluation.objects.acount(), 0)
        self.assertEqual(await EvaluationPlugin.objects.acount(), 0)
        self.assertEqual(await EvaluationInput.objects.acount(), 0)

    async def assert_accepted(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(await Evaluation.objects.acount(), 1)

    # refusals

    async def test_a_target_of_another_project_is_refused(self):
        response = await self.run_plugin(CallsTheTarget, [("target", self.f.other_projects_target),
                                                          ("prompts", self.f.prompts)])
        await self.assert_refused(response, "AI card · System: Elsewhere is not a target of this project's AI card")

    async def test_a_component_that_is_no_card_target_is_refused(self):
        response = await self.run_plugin(ReadsItsInputs, [("target", self.f.prompts)])
        await self.assert_refused(response, "Prompts is not a target of this project's AI card")

    async def test_a_card_target_without_a_connection_is_refused_when_the_plugin_calls_it(self):
        response = await self.run_plugin(CallsTheTarget, [("target", self.f.system_target)], connected=False)
        await self.assert_refused(response, "AI card · System: MCAS has no connection: set one under Manage,"
                                            " Components and connections")

    async def test_a_local_model_for_a_plugin_without_local_model_is_refused(self):
        response = await self.run_plugin(CallsTheTarget, [("target", self.f.model_file)])
        await self.assert_refused(response, "it assesses targets of the AI card, not a local model file")

    async def test_a_card_target_for_a_local_model_plugin_is_refused(self):
        response = await self.run_plugin(LocalOnly, [("model", self.f.model_file), ("target", self.f.system_target)])
        await self.assert_refused(response, "it assesses a local model file, not a target of the AI card")

    async def test_both_kinds_at_once_are_refused(self):
        response = await self.run_plugin(CardOrLocal, [("target", self.f.system_target), ("model", self.f.model_file)],
                                         connected=True)
        await self.assert_refused(response, "assess either a target of the AI card or a local model file, not both")

    async def test_neither_kind_is_refused_for_a_both_kinds_plugin(self):
        response = await self.run_plugin(CardOrLocal, [])
        await self.assert_refused(response, "pick what is assessed: a target of the AI card or a local model file")

    async def test_a_connection_reference_is_refused_for_an_adopted_plugin(self):
        response = await self.run_plugin(CallsTheTarget, [("target", self.f.system_target),
                                                          ("prompts", self.f.connection)], connected=True)
        await self.assert_refused(response, "Scoring API is a connection: what is assessed is a target of the AI card")

    # accepted

    async def test_a_connected_card_target_is_accepted(self):
        response = await self.run_plugin(CallsTheTarget, [("target", self.f.system_target),
                                                          ("prompts", self.f.prompts)], connected=True)
        await self.assert_accepted(response)

    async def test_a_card_target_is_accepted_when_the_engine_cannot_tell_its_connection(self):
        response = await self.run_plugin(CallsTheTarget, [("target", self.f.system_target)], connected=None)
        await self.assert_accepted(response)

    async def test_a_card_target_without_a_connection_is_accepted_when_the_plugin_only_reads_inputs(self):
        response = await self.run_plugin(ReadsItsInputs, [("target", self.f.system_target),
                                                          ("prompts", self.f.prompts)], connected=False)
        await self.assert_accepted(response)

    async def test_a_local_model_is_accepted(self):
        response = await self.run_plugin(LocalOnly, [("model", self.f.model_file)])
        await self.assert_accepted(response)

    async def test_one_kind_is_accepted_for_a_both_kinds_plugin(self):
        response = await self.run_plugin(CardOrLocal, [("model", self.f.model_file)])
        await self.assert_accepted(response)

    # unchanged

    # an older declaration alone is read as a card target, and checked as one

    async def test_an_old_declaration_is_refused_a_target_of_another_project(self):
        response = await self.run_plugin(OldDeclaration, [("target", self.f.other_projects_target)])
        await self.assert_refused(response, "AI card · System: Elsewhere is not a target of this project's AI card")

    async def test_an_old_declaration_accepts_a_connection_reference(self):
        # a connection bound directly, as the older declarations allow: only @assesses refuses it
        response = await self.run_plugin(OldDeclaration, [("target", self.f.connection)], connected=False)
        await self.assert_accepted(response)

    async def test_an_old_declaration_that_calls_its_target_is_refused_one_without_a_connection(self):
        response = await self.run_plugin(OldDeclaration, [("target", self.f.system_target)], connected=False)
        await self.assert_refused(response, "AI card · System: MCAS has no connection: set one under Manage,"
                                            " Components and connections")

    async def test_an_old_optional_declaration_accepts_a_target_without_a_connection(self):
        response = await self.run_plugin(OldOptionalDeclaration, [("target", self.f.system_target)], connected=False)
        await self.assert_accepted(response)

    async def test_an_old_declaration_accepts_a_connected_target_of_this_project(self):
        response = await self.run_plugin(OldDeclaration, [("target", self.f.system_target)], connected=True)
        await self.assert_accepted(response)

    async def test_a_plugin_that_declares_nothing_is_unchanged(self):
        response = await self.run_plugin(DeclaresNothing, [("target", self.f.other_projects_target)])
        await self.assert_accepted(response)


@override_settings(AISC_DEPLOYMENT="standalone")
class StandaloneIsUnchanged(EvaluationKinds):
    async def test_nothing_is_refused(self):
        response = await self.run_plugin(CardOrLocal, [("target", self.f.other_projects_target),
                                                       ("model", self.f.model_file)])
        await self.assert_accepted(response)

    # the refusals above do not apply here
    test_a_target_of_another_project_is_refused = None
    test_a_component_that_is_no_card_target_is_refused = None
    test_a_card_target_without_a_connection_is_refused_when_the_plugin_calls_it = None
    test_a_local_model_for_a_plugin_without_local_model_is_refused = None
    test_a_card_target_for_a_local_model_plugin_is_refused = None
    test_both_kinds_at_once_are_refused = None
    test_neither_kind_is_refused_for_a_both_kinds_plugin = None
    test_a_connection_reference_is_refused_for_an_adopted_plugin = None
    test_an_old_declaration_is_refused_a_target_of_another_project = None
    test_an_old_declaration_that_calls_its_target_is_refused_one_without_a_connection = None


ASKING = {"PLATFORM_URL": "http://platform:8000/", "PLATFORM_ENGINE_TOKEN": "engine-reachable-token"}
REACHABLE = "http://platform:8000/internal/projects/{pid}/targets/system/reachable"


class CardTargetConnected(SimpleTestCase):
    """The engine asks the platform, with its own token; when it cannot, it cannot tell (None) and
    accepts, and the run then fails in the worker with its reason."""

    def ask(self, reply=None, raises=None, env=ASKING):
        pid = uuid.uuid4()
        get = mock.Mock(return_value=reply, side_effect=raises)
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch.object(evaluation_routes, "_get", get):
            answer = evaluation_routes.card_target_connected(pid, "system")
        return answer, get, pid

    def test_the_platforms_answer_is_the_answer(self):
        for connected in (True, False):
            answer, get, pid = self.ask(reply=(200, {"connected": connected, "protocol": None}))
            self.assertIs(answer, connected)
            url, headers = get.call_args.args
            self.assertEqual(url, REACHABLE.format(pid=pid))
            self.assertEqual(headers, {"X-AISC-Service-Token": "engine-reachable-token"})

    def test_without_the_token_or_the_address_it_cannot_tell_and_says_so_without_secrets(self):
        for missing in ASKING:
            env = {k: v for k, v in ASKING.items() if k != missing}
            with self.assertLogs("aisc_backend.routers.evaluation", "WARNING") as logged:
                answer, get, _ = self.ask(env=env)
            self.assertIsNone(answer)
            get.assert_not_called()
            self.assertNotIn("engine-reachable-token", "".join(logged.output))

    def test_an_unreachable_platform_cannot_tell(self):
        with self.assertLogs("aisc_backend.routers.evaluation", "WARNING") as logged:
            answer, _, _ = self.ask(raises=OSError("connection refused"))
        self.assertIsNone(answer)
        self.assertNotIn("engine-reachable-token", "".join(logged.output))

    def test_any_other_answer_cannot_tell(self):
        for reply in ((503, {"detail": "closed"}), (401, {}), (404, {"detail": "no target 'system'"}),
                      (200, {"connected": "yes"})):
            with self.assertLogs("aisc_backend.routers.evaluation", "WARNING"):
                answer, _, _ = self.ask(reply=reply)
            self.assertIsNone(answer)

    def test_it_asks_over_http(self):
        import http.server
        import json
        import threading

        seen = {}

        class Platform(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen["path"], seen["token"] = self.path, self.headers.get("X-AISC-Service-Token")
                body = json.dumps({"connected": False, "protocol": None}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Platform)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        pid = uuid.uuid4()
        env = {**ASKING, "PLATFORM_URL": f"http://127.0.0.1:{server.server_port}"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertIs(evaluation_routes.card_target_connected(pid, "system"), False)
        self.assertEqual(seen, {"path": f"/internal/projects/{pid}/targets/system/reachable",
                                "token": "engine-reachable-token"})
