"""
Router tests for helper models (CON-01): the role catalogue, CRUD with role
validation, the key in the project's encrypted store, and the one small call the
`/test` endpoint makes with it (the status only).
"""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.connection import Helper
from aisc_backend.models.project import Project
from aisc_backend.models.project_config import ProjectConfig, ProjectConfigCategory
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.helper import router as helper_router
from aisc_backend.utils.encryption import decrypt_value

client = TestAsyncClient(helper_router)


class _Llm(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps({"choices": [{"message": {"content": "the helper answered"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def _llm():
    server = HTTPServer(("127.0.0.1", 0), _Llm)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def _project(name="helpers-project", role=MembershipRole.Owner):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    await ProjectMember.objects.acreate(project=project, subject="development", role=role)
    return project


class HelperRouterTest(TestCase):

    async def test_list_names_the_roles_and_who_may_manage(self):
        project = await _project()
        listed = await client.get(f"/{project.pid}/helpers")
        self.assertEqual(listed.status_code, 200, listed.text)
        body = listed.json()
        self.assertTrue(body["can_manage"])
        self.assertEqual(body["roles"], ["judge", "attacker", "grader", "embedder", "generator"])
        self.assertEqual(body["helpers"], [])

        viewer = await _project("viewer-project", role=MembershipRole.Viewer)
        read = await client.get(f"/{viewer.pid}/helpers")
        self.assertFalse(read.json()["can_manage"])

    async def test_put_stores_the_key_encrypted_and_never_returns_it(self):
        project = await _project()
        created = await client.put(f"/{project.pid}/helpers/judge-1", json={
            "label": "Judge", "endpoint_url": "https://judge.example.com/v1",
            "roles": ["judge", "grader"], "key": "helper-key",
        })
        self.assertEqual(created.status_code, 200, created.text)
        self.assertTrue(created.json()["has_key"])
        self.assertNotIn("helper-key", created.text)
        self.assertEqual(created.json()["roles"], ["judge", "grader"])

        row = await ProjectConfig.objects.filter(
            project=project, category=ProjectConfigCategory.SECRETS, key="helper:judge-1:key",
        ).afirst()
        self.assertIsNotNone(row)
        self.assertEqual(decrypt_value(row.encrypted_value), "helper-key")
        self.assertNotIn("helper-key", (await client.get(f"/{project.pid}/helpers")).text)

    async def test_an_unknown_role_or_a_missing_endpoint_is_refused(self):
        project = await _project()
        bad_role = await client.put(f"/{project.pid}/helpers/h1", json={
            "label": "h", "endpoint_url": "https://h.example.com/v1", "roles": ["wizard"],
        })
        self.assertEqual(bad_role.status_code, 400, bad_role.text)
        no_endpoint = await client.put(f"/{project.pid}/helpers/h1", json={
            "label": "h", "endpoint_url": "", "roles": ["judge"],
        })
        self.assertEqual(no_endpoint.status_code, 400, no_endpoint.text)

    async def test_delete_removes_the_helper_and_its_key(self):
        project = await _project()
        await client.put(f"/{project.pid}/helpers/h1", json={
            "label": "h", "endpoint_url": "https://h.example.com/v1", "roles": ["judge"], "key": "k",
        })
        removed = await client.delete(f"/{project.pid}/helpers/h1")
        self.assertEqual(removed.status_code, 204, removed.text)
        self.assertEqual(await Helper.objects.filter(project=project).acount(), 0)
        self.assertFalse(await ProjectConfig.objects.filter(
            project=project, key="helper:h1:key",
        ).aexists())

    async def test_test_calls_the_helper_with_its_key_and_reports_the_status_only(self):
        project = await _project()
        with _llm() as endpoint:
            await client.put(f"/{project.pid}/helpers/judge-1", json={
                "label": "Judge", "endpoint_url": endpoint, "roles": ["judge"], "key": "k",
            })
            tried = await client.post(f"/{project.pid}/helpers/judge-1/test", json={"model": "m1"})
        self.assertEqual(tried.status_code, 200, tried.text)
        self.assertTrue(tried.json()["ok"], tried.text)
        self.assertEqual(tried.json()["status"], "reachable")
        self.assertNotIn("the helper answered", tried.text)

        missing_model = await client.post(f"/{project.pid}/helpers/judge-1/test", json={"model": ""})
        self.assertEqual(missing_model.status_code, 400, missing_model.text)
