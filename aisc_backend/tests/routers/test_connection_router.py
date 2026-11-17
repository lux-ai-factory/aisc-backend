"""
Router tests for connections and adapters (CON-01): a project-scoped listing of
the connection components with their adapter and key presence, and an editor-only
adapter/helper-role choice. A stranger gets 404; a viewer reads only.
"""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import AIComponent, AIComponentType
from aisc_backend.models.ai_system import AISystem
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.connection import router as connection_router

client = TestAsyncClient(connection_router)


async def _project(name="conn-project", role=MembershipRole.Owner):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    if role is not None:
        await ProjectMember.objects.acreate(
            project=project, subject="development", role=role,
        )
    return project


async def _component(project, component_type, name, **kw):
    system = await AISystem.objects.filter(project=project).afirst()
    if system is None:
        system = await AISystem.objects.acreate(project=project, name="sys", description="")
    return await AIComponent.objects.acreate(
        component_type=component_type, system=system, name=name, description="", data="", **kw
    )


class ConnectionRouterTest(TestCase):

    async def test_lists_connections_with_default_adapters(self):
        project = await _project()
        await _component(
            project, AIComponentType.LLM, "judge",
            json_value={"endpoint_url": "http://llm.local", "secret_key": "shh"},
        )
        await _component(project, AIComponentType.DATASET, "train", json_value={})

        resp = await client.get(f"/{project.pid}/connections")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("openai", body["adapters"])
        by_name = {c["name"]: c for c in body["connections"]}
        self.assertEqual(by_name["judge"]["adapter"], "openai")
        self.assertEqual(by_name["judge"]["endpoint"], "http://llm.local")
        self.assertTrue(by_name["judge"]["key_set"])
        self.assertFalse(by_name["train"]["key_set"])
        self.assertNotIn("shh", resp.text)  # the secret is never read back

    async def test_editor_sets_adapter_and_helper_role(self):
        project = await _project()
        comp = await _component(project, AIComponentType.RESOURCE, "local-judge")

        resp = await client.put(
            f"/{project.pid}/connections/{comp.pid}",
            json={"adapter": "a2a", "adapter_version": "1.0", "helper_role": "judge"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["adapter"], "a2a")
        self.assertEqual(body["adapter_version"], "1.0")
        self.assertEqual(body["helper_role"], "judge")

    async def test_unknown_adapter_is_refused(self):
        project = await _project()
        comp = await _component(project, AIComponentType.LLM, "x")
        resp = await client.put(
            f"/{project.pid}/connections/{comp.pid}", json={"adapter": "grpc"},
        )
        self.assertEqual(resp.status_code, 400, resp.text)

    async def test_viewer_reads_but_cannot_change(self):
        project = await _project("viewer", role=MembershipRole.Viewer)
        comp = await _component(project, AIComponentType.LLM, "x")
        self.assertEqual((await client.get(f"/{project.pid}/connections")).status_code, 200)
        write = await client.put(
            f"/{project.pid}/connections/{comp.pid}", json={"adapter": "rest"},
        )
        self.assertEqual(write.status_code, 403, write.text)

    async def test_cross_project_component_is_not_found(self):
        project = await _project("mine")
        other = await _project("other")
        other_comp = await _component(other, AIComponentType.LLM, "theirs")
        resp = await client.put(
            f"/{project.pid}/connections/{other_comp.pid}", json={"adapter": "rest"},
        )
        self.assertEqual(resp.status_code, 404, resp.text)

    async def test_stranger_gets_404(self):
        project = await _project("stranger", role=None)
        await _component(project, AIComponentType.LLM, "x")
        resp = await client.get(f"/{project.pid}/connections")
        self.assertEqual(resp.status_code, 404, resp.text)
