import uuid

from django.test import TransactionTestCase

from a4s_backend.repositories.project_repository import ProjectRepository

project_repository = ProjectRepository()


class ProjectRepositoryTestCase(TransactionTestCase):

    async def test_create_project(self):
        project = await project_repository.create(name="test")

        self.assertIsNotNone(project)
        self.assertIsNotNone(project.id)
        self.assertIsNotNone(project.pid)
        self.assertIsInstance(project.pid, uuid.UUID)
        self.assertEqual(project.name, "test")

    async def test_get_project(self):
        pid = (await project_repository.create("test")).pid
        self.assertIsNotNone(pid)

        project = await project_repository.get(pid)

        self.assertIsNotNone(project)
        self.assertIsNotNone(project.id)
        self.assertIsNotNone(project.pid)
        self.assertIsInstance(pid, uuid.UUID)
        self.assertEqual(pid, project.pid)
        self.assertEqual(project.name, "test")

    async def test_get_project_by_name(self):
        name = (await project_repository.create("test")).name
        self.assertIsNotNone(name)

        project = await project_repository.get_one(name=name)

        self.assertIsNotNone(project)
        self.assertIsNotNone(project.id)
        self.assertIsNotNone(project.pid)
        self.assertIsInstance(name, str)
        self.assertEqual(project.name, "test")