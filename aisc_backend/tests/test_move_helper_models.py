"""Start moves the `llm` components older runs used as helpers to the platform, and hides them.

Before helper models lived on the platform, a judge was an `llm` component of the engine's AI system,
its key an engine project secret. `manage.py move_helper_models` (run by start, configurator only)
hands each component a run bound to a `helper:<role>` input to the platform as a helper model, with
its decrypted key and the roles it was used for, then marks the engine row hidden: never listed with
the AI system's components, never offered as a target, never deleted, so the runs that used it read as
before. An `llm` component no run used as a helper is left as it is and named in the output.
"""
import io
import uuid
from unittest import mock

from asgiref.sync import async_to_sync
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend import platform_helpers
from aisc_backend.auth import keycloak
from aisc_backend.models import (
    AIComponent, AIComponentType, AISystem, Evaluation, EvaluationInput, EvaluationPlugin, EvaluationStatus,
    Plugin, PluginConfig, Project, ProjectConfig, ProjectConfigCategory, ProjectStatus,
)
from aisc_backend.routers.project import router as project_router
from aisc_backend.utils.encryption import encrypt_value

SIGNED_IN = {"Authorization": "Bearer development"}
JUDGE_KEY = "sk-old-judge-key-123"
projects = TestAsyncClient(project_router)

_auth_off = None


def setUpModule():
    global _auth_off
    _auth_off = mock.patch.object(keycloak, "AUTH_ENABLED", False)
    _auth_off.start()


def tearDownModule():
    _auth_off.stop()


class FakePlatform:
    """The platform's create route: records each helper it is handed; a name already taken is refused."""

    def __init__(self, taken=()):
        self.created = []
        self.taken = set(taken)

    def __call__(self, project_pid, *, name, label, endpoint_url, roles, api_key):
        if name in self.taken:
            return None
        self.taken.add(name)
        self.created.append({"pid": project_pid, "name": name, "label": label, "endpoint_url": endpoint_url,
                             "roles": roles, "api_key": api_key})
        return {"name": name, "ref": f"helper:{project_pid}/{name}"}


def down(*args, **kwargs):
    raise platform_helpers.HelpersUnavailable("the platform did not answer at http://platform:8000")


class OldInstall:
    """A project with two `llm` components: `judge-x`, which a run used as its judge, and `sut-llm`,
    the system under test, never used as a helper."""

    def __init__(self):
        self.platform_pid = uuid.uuid4()
        self.project = Project.objects.create(name="MCAS", status=ProjectStatus.Ready,
                                              platform_project_id=self.platform_pid)
        system = AISystem.objects.create(project=self.project)
        ProjectConfig.objects.create(project=self.project, key="judge_key", category=ProjectConfigCategory.SECRETS,
                                     encrypted_value=encrypt_value(JUDGE_KEY))
        self.judge = AIComponent.objects.create(
            system=system, name="judge-x", component_type=AIComponentType.LLM,
            json_value={"endpoint_url": "https://judge.example.com/v1", "secret_key": "judge_key"})
        self.sut = AIComponent.objects.create(
            system=system, name="sut-llm", component_type=AIComponentType.LLM,
            json_value={"endpoint_url": "https://sut.example.com/v1", "secret_key": None})
        plugin = Plugin.objects.create(name="Judged", display_name="Judged test", description="",
                                       package_name="judged", version="0.1", project=self.project, enabled=True)
        config = PluginConfig.objects.create(plugin=plugin, config={})
        evaluation = Evaluation.objects.create(project=self.project, status=EvaluationStatus.Done)
        run = EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=config, name="Judged")
        self.judged = EvaluationInput.objects.create(evaluation_plugin=run, name="helper:judge",
                                                     component=self.judge, value={"model": "gpt-4o-mini"})
        EvaluationInput.objects.create(evaluation_plugin=run, name="target", component=self.sut, value={})


def move(platform):
    out = io.StringIO()
    with mock.patch.object(platform_helpers, "create_helper", side_effect=platform):
        call_command("move_helper_models", stdout=out)
    return out.getvalue()


@override_settings(AISC_DEPLOYMENT="configurator")
class InTheConfigurator(TestCase):
    def setUp(self):
        self.old = OldInstall()

    def test_move_only_components_used_as_helpers(self):
        platform = FakePlatform()
        output = move(platform)
        self.assertEqual(platform.created, [{
            "pid": str(self.old.platform_pid), "name": "judge-x", "label": "judge-x",
            "endpoint_url": "https://judge.example.com/v1", "roles": ["judge"], "api_key": JUDGE_KEY}])
        self.old.judge.refresh_from_db()
        self.old.sut.refresh_from_db()
        self.assertEqual(self.old.judge.hidden_as_helper, f"helper:{self.old.platform_pid}/judge-x")
        self.assertIsNone(self.old.sut.hidden_as_helper)
        self.assertIn("moved 1", output)
        self.assertIn("left 1: MCAS/sut-llm", output)
        self.assertNotIn(JUDGE_KEY, output)

    def test_move_is_idempotent(self):
        move(FakePlatform())
        platform = FakePlatform()
        output = move(platform)
        self.assertEqual(platform.created, [])
        self.assertIn("moved 0", output)

    def test_move_takes_the_next_free_name(self):
        platform = FakePlatform(taken={"judge-x", "judge-x-2"})
        move(platform)
        self.assertEqual([c["name"] for c in platform.created], ["judge-x-3"])
        self.old.judge.refresh_from_db()
        self.assertEqual(self.old.judge.hidden_as_helper, f"helper:{self.old.platform_pid}/judge-x-3")

    def test_move_changes_nothing_when_platform_down(self):
        err = io.StringIO()
        with mock.patch.object(platform_helpers, "create_helper", side_effect=down), \
                self.assertRaises(SystemExit) as stopped:
            call_command("move_helper_models", stdout=io.StringIO(), stderr=err)
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("the platform did not answer at", err.getvalue())
        self.assertFalse(AIComponent.objects.exclude(hidden_as_helper=None).exists())

    def test_hidden_component_is_not_listed_and_old_run_still_reads(self):
        move(FakePlatform())

        details = async_to_sync(projects.get)(f"/{self.old.project.pid}", headers=SIGNED_IN)
        self.assertEqual(details.status_code, 200, details.content)
        self.assertEqual([c["name"] for c in details.json()["components"]], ["sut-llm"])
        system = async_to_sync(projects.get)(f"/{self.old.project.pid}/aisystem", headers=SIGNED_IN)
        self.assertEqual([c["name"] for c in system.json()["components"]], ["sut-llm"])

        self.old.judged.refresh_from_db()
        self.assertEqual(self.old.judged.component_id, self.old.judge.pk)
        self.assertTrue(AIComponent.objects.filter(pk=self.old.judge.pk).exists())


class Standalone(TestCase):
    def test_standalone_moves_nothing(self):
        old = OldInstall()
        platform = FakePlatform()
        output = move(platform)
        self.assertEqual(output.strip(), "standalone engine: nothing to move")
        self.assertEqual(platform.created, [])
        old.judge.refresh_from_db()
        self.assertIsNone(old.judge.hidden_as_helper)


class Answer(io.BytesIO):
    def __init__(self, status, body=b"{}"):
        super().__init__(body)
        self.status = status


class CreateHelperCall(TestCase):
    """platform_helpers.create_helper: the platform's create route, with the engine's token."""

    def call(self, urlopen):
        with mock.patch.dict("os.environ", {"PLATFORM_URL": "http://platform:8000/",
                                            "PLATFORM_ENGINE_TOKEN": "engine-token"}), \
                mock.patch.object(platform_helpers.urlrequest, "urlopen", side_effect=urlopen):
            return platform_helpers.create_helper("p-1", name="judge-x", label="judge-x",
                                                  endpoint_url="https://judge.example.com/v1",
                                                  roles=["judge"], api_key=JUDGE_KEY)

    def test_created_helper_is_returned(self):
        sent = []

        def urlopen(req, timeout):
            sent.append(req)
            return Answer(201, b'{"name": "judge-x", "ref": "helper:p-1/judge-x"}')

        self.assertEqual(self.call(urlopen), {"name": "judge-x", "ref": "helper:p-1/judge-x"})
        [req] = sent
        self.assertEqual(req.full_url, "http://platform:8000/internal/projects/p-1/helpers")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("X-aisc-service-token"), "engine-token")

    def test_taken_name_is_none(self):
        from urllib.error import HTTPError

        def urlopen(req, timeout):
            raise HTTPError(req.full_url, 409, "Conflict", {}, io.BytesIO(b'{"detail": "taken"}'))

        self.assertIsNone(self.call(urlopen))

    def test_silent_platform_is_unavailable_and_names_the_url_not_the_key(self):
        from urllib.error import URLError

        def urlopen(req, timeout):
            raise URLError("connection refused")

        with self.assertRaises(platform_helpers.HelpersUnavailable) as raised:
            self.call(urlopen)
        self.assertEqual(raised.exception.message, "the platform did not answer at http://platform:8000")
        self.assertNotIn(JUDGE_KEY, repr(raised.exception.__cause__) + str(raised.exception))

    def test_refusal_is_a_helper_refused_with_the_status(self):
        from urllib.error import HTTPError

        def urlopen(req, timeout):
            raise HTTPError(req.full_url, 422, "Unprocessable", {}, io.BytesIO(b'{"detail": "endpoint_url: no"}'))

        with self.assertRaises(platform_helpers.HelperRefused) as raised:
            self.call(urlopen)
        self.assertEqual(str(raised.exception), "the platform answered 422: endpoint_url: no")
