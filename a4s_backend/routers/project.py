import uuid
from http.client import HTTPException

from ninja import Router, Schema
from ninja.errors import HttpError

from a4s_backend.models.dataset import Dataset
from a4s_backend.models.datashape import DataShape, DataShapeStatus
from a4s_backend.models.model import Model
from a4s_backend.models.project import Project, ProjectStatus
from a4s_backend.schemas.dataset import DatasetOutScheme, DatasetInScheme
from a4s_backend.schemas.datashape import DataShapeOutScheme
from a4s_backend.schemas.model import ModelOutScheme
from a4s_backend.schemas.project import ProjectOutSchema, ProjectInSchema, ProjectDetailsOutSchema


router = Router(tags=["projects"])


@router.post("", response=ProjectOutSchema)
async def create_project(request, data: ProjectInSchema):
    project = Project(
        name = data.name,
        status = ProjectStatus.Created,
        datashape = await DataShape.objects.acreate(status=DataShapeStatus.Manual)
    )
    await project.asave()
    return project

@router.patch("/{pid}", response=ProjectOutSchema)
async def update_project(request, pid: uuid.UUID, data: ProjectInSchema):
    project = await Project.objects.aget(pid=pid)

    updated_fields = data.dict(exclude_unset=True)

    for attr, value in updated_fields.items():
        setattr(project, attr, value)

    await project.asave()
    return project


@router.get("", response=list[ProjectOutSchema])
async def list_projects(request):
    projects = [p async for p in Project.objects.all()]
    return projects


@router.get("/by-name/{name}", response=ProjectOutSchema)
async def project_by_name(request, name):
    project = await Project.objects.aget(name=name)

    return project


@router.get("/{pid}", response=ProjectDetailsOutSchema)
async def project_details(request, pid: uuid.UUID):
    project = await (
        Project.objects
        .select_related("datashape")
        .prefetch_related("datashape__datasets", "datashape__datasets__models")
        .aget(pid=pid)
    )

    return project


@router.get("/{pid}/datashape", response=DataShapeOutScheme)
async def project_datashape(request, pid: uuid.UUID):
    project = await (
        Project.objects
        .select_related("datashape")
        .prefetch_related("datashape__datasets", "datashape__features")
        .aget(pid=pid)
    )

    return project.datashape


@router.post("/{pid}/datasets", response=DatasetOutScheme)
async def project_datasets(request, pid: uuid.UUID, data: DatasetInScheme):
    project = await Project.objects.select_related("datashape").aget(pid=pid)
    datashape = project.datashape

    dataset = Dataset(**data.dict())
    dataset.datashape = datashape

    await dataset.asave()
    return dataset


class ProjectModelsRequest(Schema):
    name: str
    dataset_pid: str

@router.post("/{pid}/models", response=ModelOutScheme)
async def project_models(request, pid: uuid.UUID, data: ProjectModelsRequest):
    project = await Project.objects.aget(pid=pid)
    dataset = await Dataset.objects.aget(pid=data.dataset_pid)

    if project.datashape.pid != dataset.datashape.pid:
        raise HttpError(409, "Dataset does not belong to project")

    model = Model(name=data.name)
    model.dataset = dataset
    model.public = True

    await model.asave()
    return model
