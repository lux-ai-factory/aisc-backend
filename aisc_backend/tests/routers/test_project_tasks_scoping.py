"""
Tests for project-scoped task reads.

GET /api/v1/projects/{pid}/tasks returns only the celery tasks of THAT
project's evaluations; a stranger gets 404. The celery layer is mocked so the
tests never touch a broker.
"""
import uuid
import unittest.mock as mock

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import Evaluation, EvaluationStatus
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.project import router as project_router

project_client = TestAsyncClient(project_router)


class ProjectTasksScopingTest(TestCase):

    async def _project(self, owner: bool = True):
        project = await Project.objects.acreate(
            name="tasks-project", description="", status="Ready"
        )
        if owner:
            await ProjectMember.objects.acreate(
                project=project, subject="development", role=MembershipRole.Owner
            )
        return project

    async def test_only_this_projects_tasks(self):
        mine = await self._project()
        theirs = await self._project(owner=False)   # owned by someone else
        await ProjectMember.objects.acreate(
            project=theirs, subject="other-user", role=MembershipRole.Owner
        )
        await Evaluation.objects.acreate(
            status=EvaluationStatus.Processing, project=mine, task=uuid.uuid4()
        )
        await Evaluation.objects.acreate(
            status=EvaluationStatus.Processing, project=theirs, task=uuid.uuid4()
        )

        with mock.patch(
            "aisc_backend.services.celery_service.get_evaluation_task_state",
            new=mock.AsyncMock(return_value="STARTED"),
        ):
            resp = await project_client.get(f"/{mine.pid}/tasks")

        self.assertEqual(resp.status_code, 200)
        tasks = resp.json()["tasks"]
        self.assertEqual(len(tasks), 1)   # the other project's task is absent

    async def test_stranger_gets_404(self):
        project = await self._project(owner=False)
        resp = await project_client.get(f"/{project.pid}/tasks")
        self.assertEqual(resp.status_code, 404)
