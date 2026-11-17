"""An LLM component's model listing sends the project's key to the component's address: it goes there only under
the project's network rule (from the platform for a project linked to one, else the engine's
CONNECTIONS_ALLOWED_HOSTS), follows no redirect, and never puts the endpoint's answer body in its error.

A local server plays both the platform (the rule) and the model endpoint; it records every request it gets."""
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import AIComponent, AIComponentType, ProjectConfig, ProjectConfigCategory
from aisc_backend.models.ai_system import AISystem
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.component import router
from aisc_backend.utils.encryption import encrypt_value

client = TestAsyncClient(router)
KEY = "sk-listing-key-for-tests"
ENGINE_TOKEN = "engine-token-for-listing-tests"
ECHO = "the-endpoint-said-something-private"


class _Server:
    """A local server: `answers[path]` is (status, headers, body) for a GET of that path; every path asked is kept."""

    def __init__(self):
        self.answers: dict[str, tuple[int, dict, bytes]] = {}
        self.asked: list[tuple[str, dict]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - the handler's name
                outer.asked.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                status, headers, body = outer.answers.get(self.path, (404, {}, b"no such page"))
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        self.base = f"http://{self.host}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def paths(self) -> list[str]:
        return [path for path, _ in self.asked]


class ModelListingKeepsTheKeyWhereTheRuleAllows(TestCase):
    def setUp(self):
        self.server = _Server()
        self.addCleanup(self.server.stop)
        self.platform_pid = uuid.uuid4()
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready,
                                              platform_project_id=self.platform_pid)
        self.system = AISystem.objects.create(project=self.project, name="sys", description="")
        ProjectConfig.objects.create(project=self.project, name="Key", key="k", category=ProjectConfigCategory.SECRETS,
                                     encrypted_value=encrypt_value(KEY))
        env = patch.dict(os.environ, {"PLATFORM_URL": self.server.base, "PLATFORM_ENGINE_TOKEN": ENGINE_TOKEN,
                                      "CONNECTIONS_ALLOWED_HOSTS": ""})
        env.start()
        self.addCleanup(env.stop)
        self.server.answers["/v1/models"] = (200, {"Content-Type": "application/json"},
                                             json.dumps({"data": [{"id": "gpt-4o"}]}).encode())

    def rule(self, allowed: list[str]):
        self.server.answers[f"/internal/projects/{self.platform_pid}/network-rule"] = (
            200, {"Content-Type": "application/json"},
            json.dumps({"allowed_hosts": allowed, "denied_addresses": [], "floor_hosts": allowed}).encode())

    async def component(self, endpoint_url: str) -> AIComponent:
        return await AIComponent.objects.acreate(component_type=AIComponentType.LLM, system=self.system, name="llm",
                                                 description="", data="",
                                                 json_value={"endpoint_url": endpoint_url, "secret_key": "k"})

    async def listed(self, endpoint_url: str) -> dict:
        comp = await self.component(endpoint_url)
        res = await client.get(f"/{comp.pid}/models")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn(KEY, res.text)
        return res.json()

    async def test_an_address_the_rule_allows_lists_its_models_with_the_key(self):
        self.rule([self.server.host])
        got = await self.listed(self.server.base)
        self.assertEqual(got, {"models": ["gpt-4o"], "error": None})
        asked = dict(self.server.asked)
        self.assertEqual(asked["/v1/models"].get("authorization"), f"Bearer {KEY}")
        self.assertEqual(asked[f"/internal/projects/{self.platform_pid}/network-rule"].get("x-aisc-service-token"),
                         ENGINE_TOKEN)

    async def test_an_address_the_projects_rule_refuses_is_never_asked(self):
        self.rule([])
        got = await self.listed(self.server.base)
        self.assertEqual(got["models"], [])
        self.assertIn("127.0.0.1", got["error"])
        self.assertNotIn("/v1/models", self.server.paths(), "the key was sent to an address the rule refuses")

    async def test_a_redirect_is_not_followed(self):
        self.rule([self.server.host])
        self.server.answers["/v1/models"] = (302, {"Location": f"{self.server.base}/elsewhere/v1/models"}, b"")
        got = await self.listed(self.server.base)
        self.assertEqual(got["models"], [])
        self.assertIn("redirect", got["error"])
        self.assertNotIn("/elsewhere/v1/models", self.server.paths(), "the redirect was followed with the key")

    async def test_the_endpoints_answer_is_not_in_the_error(self):
        self.rule([self.server.host])
        self.server.answers["/v1/models"] = (401, {"Content-Type": "text/plain"}, ECHO.encode())
        got = await self.listed(self.server.base)
        self.assertEqual(got["models"], [])
        self.assertIn("401", got["error"])
        self.assertNotIn(ECHO, got["error"])

    async def test_a_project_whose_rule_cannot_be_read_lists_nothing(self):
        self.server.answers[f"/internal/projects/{self.platform_pid}/network-rule"] = (500, {}, b"down")
        got = await self.listed(self.server.base)
        self.assertEqual(got["models"], [])
        self.assertIn("network rule", got["error"])
        self.assertNotIn("/v1/models", self.server.paths())


class ModelListingWithoutAPlatformProject(TestCase):
    """A project linked to no platform project has no project rule: the engine's CONNECTIONS_ALLOWED_HOSTS is it,
    as for any direct call of the plugin interface."""

    def setUp(self):
        self.server = _Server()
        self.addCleanup(self.server.stop)
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)
        self.system = AISystem.objects.create(project=self.project, name="sys", description="")
        ProjectConfig.objects.create(project=self.project, name="Key", key="k", category=ProjectConfigCategory.SECRETS,
                                     encrypted_value=encrypt_value(KEY))
        self.server.answers["/v1/models"] = (200, {"Content-Type": "application/json"},
                                             json.dumps({"data": [{"id": "gpt-4o"}]}).encode())

    async def listed(self, allowed: str) -> dict:
        comp = await AIComponent.objects.acreate(component_type=AIComponentType.LLM, system=self.system, name="llm",
                                                 description="", data="",
                                                 json_value={"endpoint_url": self.server.base, "secret_key": "k"})
        with patch.dict(os.environ, {"CONNECTIONS_ALLOWED_HOSTS": allowed}):
            res = await client.get(f"/{comp.pid}/models")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    async def test_an_internal_address_not_allowed_is_never_asked(self):
        got = await self.listed("")
        self.assertEqual(got["models"], [])
        self.assertNotIn("/v1/models", self.server.paths())

    async def test_an_internal_address_the_engine_allows_lists_its_models(self):
        got = await self.listed(self.server.host)
        self.assertEqual(got, {"models": ["gpt-4o"], "error": None})
