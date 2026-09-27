import unittest.mock as mock

from aisc_backend.auth import keycloak

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.routers.project import router as project_router
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.project import ProjectInSchema, ProjectOutSchema

project_repository = ProjectRepository()
client = TestAsyncClient(project_router)

#: These tests are about the routes, not about Keycloak, and they send a token
#: that is not meant to verify. The switch is pinned off for the module so the
#: suite behaves the same wherever it runs: the container it is run in has
#: AUTH_ENABLED=true, and without this it would pass locally and fail there.
_auth_off = None


def setUpModule():
    global _auth_off
    _auth_off = mock.patch.object(keycloak, "AUTH_ENABLED", False)
    _auth_off.start()


def tearDownModule():
    _auth_off.stop()


# The project router names the bearer check, so a call needs a token. AUTH_ENABLED
# is off in the suite, so it is not verified and no role is read: what the
# header does is get past the deny-by-default check.
SIGNED_IN = {"Authorization": "Bearer development"}


class ProjectRouterTestCase(TestCase):

    async def test_get_projects(self):
        await project_repository.create("test")

        response = await client.get('', headers=SIGNED_IN)

        self.assertEqual(200, response.status_code)
        self.assertEqual(len(response.data), 1)

    async def test_create_project(self):
        data = ProjectInSchema()
        data.name = "test"

        response = await client.post('', json=data.dict(), headers=SIGNED_IN)
        self.assertEqual(200, response.status_code)
        self.assertIsNotNone(response.data)

        project = ProjectOutSchema.model_validate(response.data)
        self.assertIsNotNone(project.pid)
        self.assertEqual(project.name, data.name)
