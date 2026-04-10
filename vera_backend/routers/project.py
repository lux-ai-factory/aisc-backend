import uuid
from typing import Any

from ninja import Router, Schema
from ninja.errors import HttpError

from vera_backend.models import EvaluationStatus, Dataset
from vera_backend.models.common import StorageContainer
from vera_backend.models.datashape import DataShape, DataShapeStatus
from vera_backend.models.model import Model
from vera_backend.repositories.base_repository import BaseRepository
from vera_backend.repositories.dataset_repository import DatasetRepository
from vera_backend.repositories.datashape_repository import DataShapeRepository
from vera_backend.repositories.evaluation_repository import EvaluationRepository
from vera_backend.repositories.project_repository import ProjectRepository
from vera_backend.schemas.dataset import DatasetOutSchema, DatasetInSchema
from vera_backend.schemas.datashape import DataShapeOutSchema, DataShapeInSchema
from vera_backend.schemas.evaluation import EvaluationDetailOutSchema
from vera_backend.schemas.model import ModelOutSchema, ModelInSchema
from vera_backend.schemas.project import ProjectOutSchema, ProjectInSchema, ProjectDetailsOutSchema


router = Router(tags=["project"])

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


@router.get("/{pid}/datashape", response=DataShapeOutSchema)
async def get_project_datashape(request, pid: uuid.UUID):
    project = await project_repository.get(pid, True)

    if project.expected_datashape is None:
        raise HttpError(404, f"Project {pid} has no expected datashape")

    return project.expected_datashape


@router.post("/{pid}/datasets", response=DatasetOutSchema)
async def create_project_dataset(request, pid: uuid.UUID, data: DatasetInSchema):
    project = await project_repository.get(pid)

    dataset = await dataset_repository.save(Dataset(**data.model_dump(), project=project, storage_container=StorageContainer.Datasets))

    await datashape_repository.save(DataShape(status=DataShapeStatus.Manual, dataset=dataset))

    return dataset


@router.post("/{pid}/models", response=ModelOutSchema)
async def create_project_model(request, pid: uuid.UUID, data: ModelInSchema):
    project = await project_repository.get(pid)

    model = Model(**data.model_dump(), project=project, public=True, storage_container=StorageContainer.Models)
    return await model_repository.save(model)


@router.patch("/{pid}/datashape", response=DataShapeOutSchema)
async def update_project_datashape(request, pid: uuid.UUID, data: DataShapeInSchema):
    project = await project_repository.get(pid, True)

    if project.expected_datashape is None:
        raise HttpError(404, f"Project {pid} has no expected datashape")

    return await datashape_repository.patch(project.expected_datashape, data)


@router.get("/{pid}/evaluations", response=list[EvaluationDetailOutSchema])
async def get_project_evaluations(request, pid: uuid.UUID, status: EvaluationStatus | None = None, exclude_status: EvaluationStatus | None = None):
    project = await project_repository.get(pid)
    filter: dict[str, Any] = {"project": project}
    exclude = None
    if status is not None:
        filter["status"] = status
    if exclude_status is not None:
        exclude = {"status": exclude_status}
    evaluations = await evaluation_repository.filter_with_related(filter, exclude)
    return evaluations


class ProjectPluginConfigResponse(Schema):
    name: str
    dataset_pid: uuid.UUID

@router.get("/{pid}/plugins/{plugin_name}/config", response=dict | None)
async def get_project_plugin_config(request, pid: uuid.UUID, plugin_name: str):
    project = await project_repository.get(pid, True)
    plugin = next((p for p in project.get_enabled_plugins() if p.name == plugin_name), None)
    if not plugin:
        raise HttpError(404, f"Project {pid} has no plugin {plugin_name}")
    return plugin.config