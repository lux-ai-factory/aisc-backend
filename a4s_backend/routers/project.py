import uuid

from ninja import Router, Schema
from ninja.errors import HttpError

from a4s_backend.models import EvaluationStatus
from a4s_backend.models.datashape import DataShape, DataShapeStatus
from a4s_backend.models.model import Model
from a4s_backend.repositories.base_repository import BaseRepository
from a4s_backend.repositories.dataset_repository import DatasetRepository
from a4s_backend.repositories.datashape_repository import DataShapeRepository
from a4s_backend.repositories.evaluation_repository import EvaluationRepository
from a4s_backend.repositories.project_repository import ProjectRepository
from a4s_backend.schemas.common import RecordPid
from a4s_backend.schemas.dataset import DatasetOutScheme, DatasetInScheme
from a4s_backend.schemas.datashape import DataShapeOutScheme, DataShapeInScheme
from a4s_backend.schemas.model import ModelOutScheme
from a4s_backend.schemas.project import ProjectOutSchema, ProjectInSchema, ProjectDetailsOutSchema


router = Router(tags=["projects"])

project_repository = ProjectRepository()
dataset_repository = DatasetRepository()
datashape_repository = DataShapeRepository()
model_repository = BaseRepository(model=Model)
evaluation_repository = EvaluationRepository()


@router.post("", response=ProjectOutSchema)
async def create_project(request, data: ProjectInSchema):
    return await project_repository.create(data.name)


@router.patch("/{pid}", response=ProjectOutSchema)
async def update_project(request, pid: uuid.UUID, data: ProjectInSchema):
    project = await project_repository.get(pid)
    return await project_repository.patch(project, data)


@router.get("", response=list[ProjectOutSchema])
async def get_projects(request):
    return await project_repository.get_all()


@router.get("/by-name/{name}", response=ProjectOutSchema)
async def get_project_by_name(request, name):
    return await project_repository.get_one(name=name)


@router.get("/{pid}", response=ProjectDetailsOutSchema)
async def get_project_details(request, pid: uuid.UUID):
    return await project_repository.get(pid, True)


@router.get("/{pid}/datashape", response=DataShapeOutScheme)
async def get_project_datashape(request, pid: uuid.UUID):
    project = await project_repository.get(pid, True)

    if project.expected_datashape is None:
        raise HttpError(404, f"Project {pid} has no expected datashape")

    return project.expected_datashape


@router.post("/{pid}/datasets", response=DatasetOutScheme)
async def create_project_dataset(request, pid: uuid.UUID, data: DatasetInScheme):
    project = await project_repository.get(pid)

    dataset = await dataset_repository.create(data)
    dataset.project = project
    await dataset_repository.save(dataset)

    await datashape_repository.save(DataShape(status=DataShapeStatus.Manual, dataset=dataset))

    return dataset


class ProjectModelsRequest(Schema):
    name: str
    dataset_pid: uuid.UUID

@router.post("/{pid}/models", response=ModelOutScheme)
async def create_project_model(request, pid: uuid.UUID, data: ProjectModelsRequest):
    project = await project_repository.get(pid)
    dataset = await dataset_repository.get(data.dataset_pid, True)

    if project.pid != dataset.project.pid:
        raise HttpError(409, "Dataset does not belong to project")

    model = Model(name=data.name, dataset=dataset, public=True)
    return await model_repository.save(model)


@router.patch("/{pid}/datashape", response=DataShapeOutScheme)
async def update_project_datashape(request, pid: uuid.UUID, data: DataShapeInScheme):
    project = await project_repository.get(pid, True)

    if project.expected_datashape is None:
        raise HttpError(404, f"Project {pid} has no expected datashape")

    return await datashape_repository.patch(project.expected_datashape, data)


@router.get("/{pid}/evaluations", response=list[RecordPid])
async def get_project_evaluations(request, pid: uuid.UUID, status: EvaluationStatus):
    project = await project_repository.get(pid)

    evaluations = await evaluation_repository.filter(status=status, project=project)

    response = [RecordPid(pid=e.pid) for e in evaluations]
    return response