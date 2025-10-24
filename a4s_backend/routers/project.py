import uuid

from ninja import Router, Schema
from ninja.errors import HttpError

from a4s_backend.models import Evaluation
from a4s_backend.models.datashape import DataShape, DataShapeStatus
from a4s_backend.models.dataset import Dataset
from a4s_backend.models.model import Model
from a4s_backend.repositories import project_repository
from a4s_backend.repositories.datashape_repository import save_datashape
from a4s_backend.schemas.common import RecordPid
from a4s_backend.schemas.dataset import DatasetOutScheme, DatasetInScheme
from a4s_backend.schemas.datashape import DataShapeOutScheme, DataShapeInScheme
from a4s_backend.schemas.model import ModelOutScheme
from a4s_backend.schemas.project import ProjectOutSchema, ProjectInSchema, ProjectDetailsOutSchema


router = Router(tags=["projects"])


@router.post("", response=ProjectOutSchema)
async def create_project(request, data: ProjectInSchema):
    return await project_repository.create_project(data.name)


@router.patch("/{pid}", response=ProjectOutSchema)
async def update_project(request, pid: uuid.UUID, data: ProjectInSchema):
    project = await project_repository.get_project(pid)

    updated_fields = data.dict(exclude_unset=True)

    for attr, value in updated_fields.items():
        setattr(project, attr, value)

    await project_repository.save_project(project)
    return project


@router.get("", response=list[ProjectOutSchema])
async def list_projects(request):
    return await project_repository.get_projects()


@router.get("/by-name/{name}", response=ProjectOutSchema)
async def project_by_name(request, name):
    project = await project_repository.get_project_by_name(name)

    return project


@router.get("/{pid}", response=ProjectDetailsOutSchema)
async def project_details(request, pid: uuid.UUID):
    project = await project_repository.get_project_with_related_data(pid)

    return project


@router.get("/{pid}/datashape", response=DataShapeOutScheme)
async def project_datashape(request, pid: uuid.UUID):
    project = await project_repository.get_project_with_related_data(pid)

    if project.expected_datashape is None:
        raise HttpError(404, f"Project {pid} has no expected datashape")

    return project.expected_datashape


@router.post("/{pid}/datasets", response=DatasetOutScheme)
async def project_datasets(request, pid: uuid.UUID, data: DatasetInScheme):
    project = await project_repository.get_project(pid)

    dataset = Dataset(**data.dict())
    dataset.project = project
    await dataset.asave()

    await DataShape.objects.acreate(status=DataShapeStatus.Manual, dataset=dataset)
    return dataset


class ProjectModelsRequest(Schema):
    name: str
    dataset_pid: str

@router.post("/{pid}/models", response=ModelOutScheme)
async def project_models(request, pid: uuid.UUID, data: ProjectModelsRequest):
    project = await project_repository.get_project(pid=pid)
    dataset = await Dataset.objects.select_related("project").aget(pid=data.dataset_pid)

    if project.pid != dataset.project.pid:
        raise HttpError(409, "Dataset does not belong to project")

    model = Model(name=data.name)
    model.dataset = dataset
    model.public = True

    await model.asave()
    return model

@router.patch("/{pid}/datashape", response=DataShapeOutScheme)
async def update_project_datashape(request, pid: uuid.UUID, data: DataShapeInScheme):
    project = await project_repository.get_project_with_related_data(pid=pid)

    datashape = project.expected_datashape

    if datashape is None:
        raise HttpError(404, f"Project {pid} has no expected datashape")

    datashape = await save_datashape(datashape, data)

    return datashape

@router.get("/{pid}/evaluations", response=list[RecordPid])
async def project_evaluations(request, pid: uuid.UUID, status: str):
    project = await project_repository.get_project(pid)

    if project is None:
        raise HttpError(404, f"Project {pid} not found")

    evaluations = [e async for e in Evaluation.objects.filter(status=status, project=project).all()]

    response = [RecordPid(pid=e.pid) for e in evaluations]

    return response