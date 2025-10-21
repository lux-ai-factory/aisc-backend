import uuid

from a4s_backend.models import Project
from a4s_backend.models.project import ProjectStatus


async def create_project(name: str) -> Project:
    project = Project(
        name=name,
        status=ProjectStatus.Created
    )
    await project.asave()
    return project

async def get_project(pid: uuid.UUID) -> Project:
    return await Project.objects.aget(pid=pid)

async def get_project_by_name(name: str) -> Project:
    return await Project.objects.aget(name=name)

async def get_projects() -> list[Project]:
    return [p async for p in Project.objects.all()]

async def save_project(project: Project):
    await project.asave()

async def get_project_with_related_data(pid: uuid.UUID) -> Project:
    project = await (
        Project.objects
        .select_related(
            "expected_datashape",
            "expected_datashape__dataset"
        )
        .prefetch_related(
            "datasets",
            "datasets__models",

            "expected_datashape__features",
            "expected_datashape__date_feature",
            "expected_datashape__target_feature"
        )
        .aget(pid=pid)
    )
    return project