"""
Tests for the /authz surface (aisc_backend.routers.authz).

The one endpoint the isolated apps call to decide what a caller may do in a
project; must answer for strangers too (role null is an answer, not an error).
Auth is in the dev-bypass mode (AUTH_ENABLED off), so `request.auth` is True and
`caller_of` returns the development caller (subject "development", no roles).
"""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.authz import router as authz_router

client = TestAsyncClient(authz_router)


class AuthzRouterTestCase(TestCase):

    async def _project_with(self, role: str | None, subject: str = "development"):
        project = await Project.objects.acreate(
            name="a", description="", status="Ready"
        )
        if role is not None:
            await ProjectMember.objects.acreate(
                project=project, subject=subject, role=role
            )
        return project

    async def test_owner_sees_owner_and_may_write(self):
        project = await self._project_with(MembershipRole.Owner)
        resp = await client.get(f"/projects/{project.pid}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["role"], "owner")
        self.assertEqual(body["admin"], False)
        self.assertTrue(body["may_write"])

    async def test_viewer_cannot_write(self):
        project = await self._project_with(MembershipRole.Viewer)
        body = (await client.get(f"/projects/{project.pid}")).json()
        self.assertEqual(body["role"], "viewer")
        self.assertFalse(body["may_write"])

    async def test_stranger_gets_null_role_not_an_error(self):
        project = await self._project_with(None)
        resp = await client.get(f"/projects/{project.pid}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["role"])
        self.assertFalse(body["may_write"])

    async def test_unknown_project_is_404(self):
        resp = await client.get("/projects/does-not-exist")
        self.assertEqual(resp.status_code, 404)

    async def test_by_pid_and_by_name_both_answer(self):
        project = await self._project_with(MembershipRole.Owner)
        by_pid = await client.get(f"/projects/{project.pid}")
        by_name = await client.get(f"/projects/{project.name}")
        self.assertEqual(by_pid.status_code, 200)
        self.assertEqual(by_name.status_code, 200)
        self.assertEqual(by_pid.json()["role"], by_name.json()["role"])
