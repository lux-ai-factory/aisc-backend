"""
Router tests for connections and adapters (CON-01): CRUD and roles, the target
link, the live try through each protocol against a real local endpoint, A2A card
discovery, the key kept in the project's encrypted store (never in a body out), and
the snapshot a run records so a later edit does not rewrite it.
"""
import base64
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.connection import Connection, Helper, RunBinding
from aisc_backend.models.evaluation import Evaluation, EvaluationPlugin
from aisc_backend.models.plugin import Plugin, PluginConfig
from aisc_backend.models.project import Project
from aisc_backend.models.project_config import ProjectConfig, ProjectConfigCategory
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.connection import router as connection_router
from aisc_backend.utils.encryption import decrypt_value

client = TestAsyncClient(connection_router)


class _Endpoint(BaseHTTPRequestHandler):
    """One local endpoint serving every protocol's response, so `/test` runs the
    real httpx path rather than a stubbed transport."""

    def log_message(self, *args):  # keep the test output clean
        pass

    def _reply(self, status: int, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/.well-known/agent-card.json"):
            self._reply(200, {
                "name": "probe-agent",
                "description": "an agent",
                "url": f"http://{self.headers.get('Host')}/a2a",
                "capabilities": {"streaming": False},
            })
        else:
            self._reply(404, {"detail": "no such path"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/chat":
            self._reply(200, {"answer": "hello from rest"})
        elif self.path == "/v1/chat/completions":
            self._reply(200, {"choices": [{"message": {"content": "hello from openai"}}]})
        elif self.path == "/a2a":
            self._reply(200, {"jsonrpc": "2.0", "id": "1", "result": {
                "artifacts": [{"parts": [{"kind": "text", "text": "hello from a2a"}]}],
            }})
        elif self.path == "/v2/models/m1/infer":
            encoded = base64.b64encode(b"hello from oip").decode()
            self._reply(200, {"outputs": [{"name": "text", "datatype": "BYTES", "data": [encoded]}]})
        elif self.path == "/grounded":
            self._reply(502, {"detail": {"error": "answer_not_grounded", "reason": "ungrounded"}})
        else:
            self._reply(404, {"detail": "no such path"})


@contextmanager
def _endpoint():
    server = HTTPServer(("127.0.0.1", 0), _Endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def _project(name="connections-project", role=MembershipRole.Owner):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    await ProjectMember.objects.acreate(project=project, subject="development", role=role)
    return project


async def _run(project) -> EvaluationPlugin:
    plugin = await Plugin.objects.acreate(
        project=project, name="fairness", description="", package_name="fairness",
        version="1.0", display_name="Fairness", enabled=True,
    )
    evaluation = await Evaluation.objects.acreate(project=project, status="Done")
    config = await PluginConfig.objects.acreate(plugin=plugin, name="cfg", description="", config={})
    return await EvaluationPlugin.objects.acreate(
        evaluation=evaluation, plugin_config=config, name="fairness", description="", status="Done",
    )


class ConnectionRouterTest(TestCase):

    async def test_viewer_reads_editor_writes_and_a_stranger_gets_404(self):
        stranger = await _project("stranger")
        await ProjectMember.objects.filter(project=stranger).adelete()
        answer = await client.get(f"/{stranger.pid}/connections")
        self.assertEqual(answer.status_code, 404, answer.text)

        project = await _project("viewer-project", role=MembershipRole.Viewer)
        read = await client.get(f"/{project.pid}/connections")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(sorted(a["kind"] for a in read.json()["adapters"]),
                         ["a2a", "oip", "openai", "rest"])
        refused = await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "rest", "base_url": "http://example.com",
        })
        self.assertEqual(refused.status_code, 403, refused.text)

    async def test_an_unknown_protocol_is_refused(self):
        project = await _project()
        answer = await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "carrier-pigeon", "base_url": "http://example.com",
        })
        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertIn("unknown protocol", answer.json()["detail"])

    async def test_the_key_is_stored_encrypted_and_never_comes_back(self):
        project = await _project()
        created = await client.put(f"/{project.pid}/connections/c1", json={
            "label": "the system", "kind": "openai", "base_url": "https://llm.example.com/v1",
            "model": "gpt-4o-mini", "secret": "s3cret-key",
        })
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertTrue(body["has_secret"])
        self.assertNotIn("s3cret-key", created.text)
        self.assertEqual(body["protocol"], "OpenAI-compatible, gpt-4o-mini")

        row = await ProjectConfig.objects.filter(
            project=project, category=ProjectConfigCategory.SECRETS,
            key="connection:c1:key",
        ).afirst()
        self.assertIsNotNone(row)
        self.assertNotEqual(row.encrypted_value, "s3cret-key")
        self.assertEqual(decrypt_value(row.encrypted_value), "s3cret-key")

        listed = await client.get(f"/{project.pid}/connections")
        self.assertNotIn("s3cret-key", listed.text)

        # an empty secret clears it
        cleared = await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "openai", "base_url": "https://llm.example.com/v1", "secret": "",
        })
        self.assertFalse(cleared.json()["has_secret"])

    async def test_secret_headers_are_named_and_removed_by_name(self):
        project = await _project()
        await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "rest", "base_url": "http://example.com",
            "secret_headers": {"X-Token": "abc"}, "secret_header": "X-Token: {{secret}}",
        })
        body = (await client.get(f"/{project.pid}/connections")).json()["connections"][0]
        self.assertEqual(body["secret_headers"], ["X-Token"])
        self.assertNotIn("abc", json.dumps(body))
        await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "rest", "base_url": "http://example.com", "secret_headers": {"X-Token": ""},
        })
        body = (await client.get(f"/{project.pid}/connections")).json()["connections"][0]
        self.assertEqual(body["secret_headers"], [])

    async def test_put_is_an_upsert_and_a_bad_name_is_refused(self):
        project = await _project()
        first = await client.put(f"/{project.pid}/connections/c1", json={
            "label": "one", "kind": "rest", "base_url": "http://a.example.com",
        })
        self.assertEqual(first.status_code, 200, first.text)
        second = await client.put(f"/{project.pid}/connections/c1", json={
            "label": "one again", "kind": "rest", "base_url": "http://b.example.com",
        })
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(await Connection.objects.filter(project=project).acount(), 1)
        self.assertEqual(second.json()["base_url"], "http://b.example.com")
        bad = await client.put(f"/{project.pid}/connections/Not A Slug", json={
            "kind": "rest", "base_url": "http://a.example.com",
        })
        self.assertIn(bad.status_code, (400, 404))

    async def test_delete_removes_the_connection_and_its_secret(self):
        project = await _project()
        await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "rest", "base_url": "http://example.com", "secret": "k",
        })
        removed = await client.delete(f"/{project.pid}/connections/c1")
        self.assertEqual(removed.status_code, 204, removed.text)
        self.assertEqual(await Connection.objects.filter(project=project).acount(), 0)
        self.assertFalse(await ProjectConfig.objects.filter(
            project=project, key="connection:c1:key",
        ).aexists())

    async def test_target_links_a_component_of_the_project_once(self):
        project = await _project()
        from aisc_backend.models.ai_system import AIComponent, AIComponentType, AISystem

        system = await AISystem.objects.acreate(project=project, name="sys", description="")
        component = await AIComponent.objects.acreate(
            system=system, name="the llm", description="", component_type=AIComponentType.LLM,
        )
        created = await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "openai", "base_url": "https://llm.example.com/v1", "target": str(component.pid),
        })
        self.assertEqual(created.json()["target"]["name"], "the llm")
        # a later save does not move the target
        await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "openai", "base_url": "https://llm.example.com/v1", "target": None,
        })
        again = (await client.get(f"/{project.pid}/connections")).json()["connections"][0]
        self.assertEqual(again["target"]["key"], str(component.pid))
        unknown = await client.post(f"/{project.pid}/connections/c1/target", json={"key": "nope"})
        self.assertEqual(unknown.status_code, 400, unknown.text)

    async def test_try_runs_every_protocol_against_a_real_endpoint(self):
        project = await _project()
        with _endpoint() as base:
            cases = [
                ("rest-c", {"kind": "rest", "base_url": base, "path": "/chat",
                            "method": "POST", "body_template": {"q": "{{input}}"},
                            "response_path": "answer"}, "hello from rest"),
                ("openai-c", {"kind": "openai", "base_url": f"{base}/v1", "model": "m"},
                 "hello from openai"),
                ("a2a-c", {"kind": "a2a", "base_url": base, "path": "/a2a", "protocol_version": "1.0"},
                 "hello from a2a"),
                ("oip-c", {"kind": "oip", "base_url": base, "model": "m1"}, "hello from oip"),
            ]
            for name, fields, expected in cases:
                created = await client.put(f"/{project.pid}/connections/{name}", json=fields)
                self.assertEqual(created.status_code, 200, created.text)
                tried = await client.post(f"/{project.pid}/connections/{name}/test", json={})
                self.assertEqual(tried.status_code, 200, tried.text)
                self.assertTrue(tried.json()["ok"], tried.text)
                self.assertEqual(tried.json()["answer"], expected)

        # the last try is recorded on the connection
        row = await Connection.objects.filter(project=project, name="rest-c").afirst()
        self.assertTrue(row.last_test_ok)
        self.assertIsNotNone(row.last_test_at)

    async def test_try_reads_the_refusal_body(self):
        project = await _project()
        with _endpoint() as base:
            await client.put(f"/{project.pid}/connections/c1", json={
                "kind": "rest", "base_url": base, "path": "/grounded", "method": "POST",
                "response_path": "detail.reason",
                "refusal": {"status": [502], "path": "detail.reason",
                            "match": {"detail.error": "answer_not_grounded"}},
            })
            tried = await client.post(f"/{project.pid}/connections/c1/test", json={})
        self.assertTrue(tried.json()["ok"], tried.text)
        self.assertTrue(tried.json()["refused"])
        self.assertEqual(tried.json()["refusal_reason"], "ungrounded")

    async def test_try_reports_an_unreachable_endpoint(self):
        project = await _project()
        await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "openai", "base_url": "http://127.0.0.1:1/v1", "model": "m", "timeout_s": 2,
        })
        tried = await client.post(f"/{project.pid}/connections/c1/test", json={})
        self.assertEqual(tried.status_code, 200, tried.text)
        self.assertFalse(tried.json()["ok"])
        self.assertEqual(tried.json()["error"], "unreachable")

    async def test_discover_reads_the_a2a_agent_card(self):
        project = await _project()
        with _endpoint() as base:
            await client.put(f"/{project.pid}/connections/agent", json={
                "kind": "a2a", "base_url": base, "path": "/a2a", "protocol_version": "1.0",
            })
            found = await client.post(f"/{project.pid}/connections/agent/discover", json={})
        self.assertEqual(found.status_code, 200, found.text)
        self.assertEqual(found.json()["name"], "probe-agent")
        self.assertTrue(found.json()["url"].endswith("/.well-known/agent-card.json"))
        other = await client.post(
            f"/{project.pid}/connections/none/discover", json={},
        )
        self.assertEqual(other.status_code, 404, other.text)

    async def test_a_run_binding_snapshots_the_adapter_and_is_not_rewritten(self):
        project = await _project()
        plugin = await _run(project)
        helper = await Helper.objects.acreate(
            project=project, name="judge-1", description="", label="Judge",
            endpoint_url="https://judge.example.com/v1", roles=["judge"],
        )
        await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "openai", "base_url": "https://llm.example.com/v1", "model": "gpt-4o-mini",
        })
        bound = await client.put(f"/{project.pid}/runs/{plugin.pid}/bindings", json={
            "role": "", "connection": "c1",
        })
        self.assertEqual(bound.status_code, 200, bound.text)
        self.assertEqual(bound.json()["kind"], "openai")
        self.assertEqual(bound.json()["model"], "gpt-4o-mini")
        self.assertEqual(bound.json()["base_url"], "https://llm.example.com/v1")

        for_role = await client.put(f"/{project.pid}/runs/{plugin.pid}/bindings", json={
            "role": "judge", "helper": "judge-1",
        })
        self.assertEqual(for_role.status_code, 200, for_role.text)
        self.assertEqual(for_role.json()["helper"], "judge-1")

        # the run keeps what it used, even after the connection changes
        await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "a2a", "base_url": "https://agent.example.com", "path": "/a2a",
        })
        bindings = (await client.get(f"/{project.pid}/runs/{plugin.pid}/bindings")).json()["bindings"]
        assessed = next(b for b in bindings if b["role"] == "")
        self.assertEqual(assessed["kind"], "openai")
        self.assertEqual(assessed["base_url"], "https://llm.example.com/v1")
        self.assertEqual(await RunBinding.objects.filter(evaluation_plugin=plugin).acount(), 2)

    async def test_a_role_must_bind_a_helper_and_an_unknown_role_is_refused(self):
        project = await _project()
        plugin = await _run(project)
        await client.put(f"/{project.pid}/connections/c1", json={
            "kind": "openai", "base_url": "https://llm.example.com/v1", "model": "m",
        })
        no_helper = await client.put(f"/{project.pid}/runs/{plugin.pid}/bindings", json={
            "role": "judge", "connection": "c1",
        })
        self.assertEqual(no_helper.status_code, 400, no_helper.text)
        await Helper.objects.acreate(
            project=project, name="judge-1", description="", label="Judge",
            endpoint_url="https://judge.example.com/v1", roles=["judge"],
        )
        unknown = await client.put(f"/{project.pid}/runs/{plugin.pid}/bindings", json={
            "role": "necromancer", "helper": "judge-1",
        })
        self.assertEqual(unknown.status_code, 400, unknown.text)
