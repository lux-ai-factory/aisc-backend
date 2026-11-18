"""An evaluation and a stored file are reached by their own id, not by a project
id in the path, so the project-membership door has to be walked explicitly.

A caller who is not in the project that owns the evaluation/artifact gets 404
(deny by default); a member gets the data. Without this, any logged-in user could
read another project's evaluation or download its files.
"""
from unittest.mock import AsyncMock, patch

from django.http import StreamingHttpResponse
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import (
    AIComponent,
    AIComponentType,
    AISystem,
    Evaluation,
    EvaluationPlugin,
    EvaluationStatus,
    Project,
    ProjectMember,
    ProjectStatus,
)
from aisc_backend.models.artifact import Artifact
from aisc_backend.routers.evaluation import router as evaluation_router
from aisc_backend.routers.file import router as file_router

evaluation_client = TestAsyncClient(evaluation_router)
file_client = TestAsyncClient(file_router)


async def _project(name: str, subject: str) -> Project:
    project = await Project.objects.acreate(name=name, status=ProjectStatus.Ready)
    await ProjectMember.objects.acreate(project=project, subject=subject, role="owner")
    return project


async def _evaluation(project: Project) -> Evaluation:
    return await Evaluation.objects.acreate(
        status=EvaluationStatus.Pending, project=project
    )


async def _dataset_file(project: Project, data: str) -> AIComponent:
    system = await AISystem.objects.acreate(project=project)
    return await AIComponent.objects.acreate(
        name=f"dataset-{data}",
        description="",
        data=data,
        component_type=AIComponentType.DATASET,
        system=system,
    )


async def _artifact(evaluation: Evaluation, data: str) -> Artifact:
    evaluation_plugin = await EvaluationPlugin.objects.acreate(
        evaluation=evaluation, status="Pending"
    )
    return await Artifact.objects.acreate(
        name="report.csv", description="", data=data, evaluation_plugin=evaluation_plugin
    )


class CrossProjectEvaluationAndFileAccessTest(TestCase):
    """The caller here is the development identity (the only one an auth-less run
    has); `mine` is theirs, `theirs` belongs to somebody else."""

    async def test_a_stranger_gets_404_and_a_member_gets_the_data(self):
        mine = await _project("mine", "development")
        theirs = await _project("theirs", "someone-else")

        my_evaluation = await _evaluation(mine)
        their_evaluation = await _evaluation(theirs)

        # evaluation by id: served to a member, 404 to a non-member
        self.assertEqual(
            (await evaluation_client.get(f"/{my_evaluation.pid}/plugins/status")).status_code,
            200,
        )
        self.assertEqual(
            (await evaluation_client.get(f"/{their_evaluation.pid}/plugins/status")).status_code,
            404,
        )
        self.assertEqual(
            (await evaluation_client.get(f"/{their_evaluation.pid}")).status_code, 404
        )

        # a stored artifact: downloadable by a member, 404 to a non-member
        my_artifact = await _artifact(my_evaluation, "mine-artifact.csv")
        their_artifact = await _artifact(their_evaluation, "theirs-artifact.csv")
        my_dataset = await _dataset_file(mine, "mine-dataset.csv")
        their_dataset = await _dataset_file(theirs, "theirs-dataset.csv")

        stream = AsyncMock(return_value=StreamingHttpResponse(iter([b"data"])))
        with patch("aisc_backend.routers.file.file_repository.get_s3_file_stream", new=stream):
            self.assertEqual(
                (await file_client.get(f"/artifact/{my_artifact.data}")).status_code, 200
            )
            self.assertEqual(
                (await file_client.get(f"/dataset/{my_dataset.data}")).status_code, 200
            )

        self.assertEqual(
            (await file_client.get(f"/artifact/{their_artifact.data}")).status_code, 404
        )
        self.assertEqual(
            (await file_client.get(f"/dataset/{their_dataset.data}")).status_code, 404
        )
