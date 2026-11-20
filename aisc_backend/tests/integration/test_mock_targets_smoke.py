"""
Native and bespoke mock targets, with a file input (CON-01; refactor-plan §3.1).

The acceptance row is "real core/native/bespoke/file-input smoke paths ... the run
records the selected adapter/config version and the mock target confirms the intended
request". This is that smoke, driven through the same endpoints the webapp's
connections page calls:

* **mock-openai** — a *native* target: it speaks OpenAI, so the `openai` adapter
  reaches it. A file input is read into the prompt.
* **mock-rest** — a *native* target with its own REST shape, reached by the `rest`
  adapter with the project's template.
* **mock-website** — deliberately **not** OpenAI-shaped: a JSON answer under
  `payload.text` at another path, so it *requires* a bespoke adapter (a `rest`
  connection with a custom body template and answer path). The test proves the native
  path fails on it.

Each target **records what it received**, and the file's bytes are verified by digest
at the target: that recording — not the status code — is the evidence that the adapter
actually drove the target the way it should have. The same run also proves the
allowed-hosts rule: the mock targets are internal, so the first call is refused and the
call after the address is listed succeeds.
"""
import base64
import hashlib
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_evaluation.models import Evaluation, EvaluationPlugin
from aisc_plugins.models import Plugin, PluginConfig
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.connection import router as connection_router
from aisc_backend.tests.local_target import allow, local_target

client = TestAsyncClient(connection_router)

INPUT_FILE = Path(__file__).resolve().parent.parent / "data" / "mock_input.csv"


class MockTargets(BaseHTTPRequestHandler):
    """Three mock targets in one server, each recording the call it received.

    `mock-openai` is the OpenAI shape; `mock-rest` answers under `answer`;
    `mock-website` answers under `payload.text` and takes the file as base64, which
    is what makes it bespoke. Nothing here knows about the platform: a request that
    is not the shape it expects is a 404 or a plain answer, exactly as a real system
    of that shape would be.
    """

    calls: list[dict] = []

    def log_message(self, *args):  # keep the test output clean
        pass

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - the stdlib's name
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        body = json.loads(raw or b"{}")
        type(self).calls.append({"path": self.path, "body": body})

        if self.path == "/v1/chat/completions":
            message = (body.get("messages") or [{}])[-1]
            self._reply(200, {"choices": [{"message": {"content": f"read {message.get('content')}"}}]})
        elif self.path == "/api/ask":
            self._reply(200, {"answer": f"answered {body.get('question')!r}"})
        elif self.path == "/collect":
            blob = base64.b64decode(body.get("file_b64") or "")
            self._reply(200, {
                "payload": {
                    "text": f"collected {len(blob)} bytes",
                    "sha256": hashlib.sha256(blob).hexdigest(),
                },
            })
        else:
            self._reply(404, {"detail": f"no such path {self.path}"})


class MockTargetsSmokeTest(TestCase):

    def setUp(self):
        MockTargets.calls = []

    async def _project(self) -> Project:
        project = await Project.objects.acreate(name="smoke-project", description="", status="Ready")
        await ProjectMember.objects.acreate(
            project=project, subject="development", role=MembershipRole.Owner
        )
        return project

    async def _run(self, project: Project, name: str = "smoke-plugin") -> EvaluationPlugin:
        plugin = await Plugin.objects.acreate(
            project=project, name=name, description="", package_name=name,
            version="1.0", display_name=name, enabled=True,
        )
        evaluation = await Evaluation.objects.acreate(project=project, status="Done")
        config = await PluginConfig.objects.acreate(plugin=plugin, name="cfg", description="", config={})
        return await EvaluationPlugin.objects.acreate(
            evaluation=evaluation, plugin_config=config, name=name, description="", status="Done",
        )

    async def test_native_and_bespoke_mock_targets_get_the_intended_request(self):
        project = await self._project()
        file_bytes = INPUT_FILE.read_bytes()
        file_b64 = base64.b64encode(file_bytes).decode()
        digest = hashlib.sha256(file_bytes).hexdigest()
        self.assertIn(b"is the model fair?", file_bytes)

        with local_target(MockTargets) as target:
            # ── the guard first: the mock targets are internal, so nothing reaches
            #    them until an owner lists the address ──
            await client.put(f"/{project.pid}/connections/bespoke", json={
                "kind": "rest", "base_url": target.url, "path": "/collect", "method": "POST",
                "body_template": {"prompt": "{{input}}", "file_b64": "{{params.file_b64}}"},
                "response_path": "payload.text",
            })
            blocked = await client.post(f"/{project.pid}/connections/bespoke/test", json={
                "input": "collect this", "params": {"file_b64": file_b64},
            })
            self.assertFalse(blocked.json()["ok"], blocked.text)
            self.assertEqual(blocked.json()["error"], "internal_not_allowed")
            self.assertEqual(MockTargets.calls, [], "the target was called anyway")
            await allow(project, target, note="the mock targets")

            # ── mock-openai: a native target, with the file input read into the prompt ──
            await client.put(f"/{project.pid}/connections/native-openai", json={
                "kind": "openai", "base_url": f"{target.url}/v1", "model": "mock-openai",
            })
            openai_try = await client.post(
                f"/{project.pid}/connections/native-openai/test",
                json={"input": file_bytes.decode()},
            )
            self.assertTrue(openai_try.json()["ok"], openai_try.text)
            self.assertEqual(openai_try.json()["answer"], f"read {file_bytes.decode()}")
            recorded = MockTargets.calls[-1]
            self.assertEqual(recorded["path"], "/v1/chat/completions")
            self.assertEqual(recorded["body"]["model"], "mock-openai")
            self.assertEqual(recorded["body"]["messages"][-1]["content"], file_bytes.decode())

            # ── mock-rest: a native REST target, with the project's own template ──
            await client.put(f"/{project.pid}/connections/native-rest", json={
                "kind": "rest", "base_url": target.url, "path": "/api/ask", "method": "POST",
                "body_template": {"question": "{{input}}"}, "response_path": "answer",
            })
            rest_try = await client.post(
                f"/{project.pid}/connections/native-rest/test", json={"input": "is the model fair?"}
            )
            self.assertTrue(rest_try.json()["ok"], rest_try.text)
            self.assertEqual(rest_try.json()["answer"], "answered 'is the model fair?'")
            self.assertEqual(
                MockTargets.calls[-1]["body"], {"question": "is the model fair?"}
            )

            # ── the bespoke target: the native path fails on it, the adapter reaches it ──
            await client.put(f"/{project.pid}/connections/bespoke-openai", json={
                "kind": "openai", "base_url": f"{target.url}/collect", "model": "mock-website",
            })
            wrong_shape = await client.post(
                f"/{project.pid}/connections/bespoke-openai/test", json={"input": "anything"}
            )
            self.assertFalse(wrong_shape.json()["ok"], wrong_shape.text)
            self.assertIn("HTTP", wrong_shape.json()["detail"])

            collected = await client.post(f"/{project.pid}/connections/bespoke/test", json={
                "input": "collect this", "params": {"file_b64": file_b64},
            })
            self.assertTrue(collected.json()["ok"], collected.text)
            self.assertEqual(collected.json()["answer"], f"collected {len(file_bytes)} bytes")
            self.assertEqual(MockTargets.calls[-1]["path"], "/collect")
            self.assertEqual(
                MockTargets.calls[-1]["body"],
                {"prompt": "collect this", "file_b64": file_b64},
            )
            # the bytes the target received are the file's, byte for byte
            self.assertEqual(
                hashlib.sha256(base64.b64decode(MockTargets.calls[-1]["body"]["file_b64"])).hexdigest(),
                digest,
            )

    async def test_the_run_records_the_adapter_that_reached_the_mock_target(self):
        project = await self._project()
        plugin = await self._run(project)
        with local_target(MockTargets) as target:
            await allow(project, target)
            await client.put(f"/{project.pid}/connections/bespoke", json={
                "kind": "rest", "base_url": target.url, "path": "/collect", "method": "POST",
                "body_template": {"prompt": "{{input}}"}, "response_path": "payload.text",
            })
            bound = await client.put(f"/{project.pid}/runs/{plugin.pid}/bindings", json={
                "role": "", "connection": "bespoke",
            })
            self.assertEqual(bound.status_code, 200, bound.text)
            self.assertEqual(bound.json()["kind"], "rest")
            # the adapter's revision is part of the record, not read back from today's code
            self.assertEqual(bound.json()["adapter_version"], "1")
            self.assertEqual(bound.json()["base_url"], target.url)

        # the run's record survives the connection being pointed somewhere else
        await client.put(f"/{project.pid}/connections/bespoke", json={
            "kind": "openai", "base_url": "https://elsewhere.example.com/v1", "model": "other",
        })
        bindings = (await client.get(f"/{project.pid}/runs/{plugin.pid}/bindings")).json()["bindings"]
        self.assertEqual(bindings[0]["kind"], "rest")
        self.assertEqual(bindings[0]["base_url"], target.url)
