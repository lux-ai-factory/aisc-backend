from django.test import TestCase
from ninja.testing import TestAsyncClient

from a4s_backend.routers.project import router as project_router
from a4s_backend.repositories.project_repository import ProjectRepository
from a4s_backend.schemas.project import ProjectInSchema, ProjectOutSchema

project_repository = ProjectRepository()
client = TestAsyncClient(project_router)

class ProjectRouterCase(TestCase):

    async def test_get_projects(self):
        await project_repository.create("test")

        response = await client.get('')

        self.assertEqual(200, response.status_code)
        self.assertEqual(len(response.data), 1)

    async def test_create_project(self):
        data = ProjectInSchema()
        data.name = "test"
        data.frequency = ""
        data.window_size = ""

        response = await client.post('', json=data.dict())
        self.assertEqual(200, response.status_code)
        self.assertIsNotNone(response.data)

        project = ProjectOutSchema.model_validate(response.data)
        self.assertIsNotNone(project.pid)
        self.assertEqual(project.name, data.name)
        self.assertEqual(project.frequency, data.frequency)
        self.assertEqual(project.window_size, data.window_size)
