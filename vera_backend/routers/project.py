import uuid
from typing import Any

from ninja import Router, Schema, Query
from ninja.errors import HttpError

from vera_backend.models import EvaluationStatus, Dataset
from vera_backend.models.common import StorageContainer
from vera_backend.models.model import Model
from vera_backend.repositories.base_repository import BaseRepository
from vera_backend.repositories.dataset_repository import DatasetRepository
from vera_backend.repositories.evaluation_repository import EvaluationRepository
from vera_backend.repositories.project_repository import ProjectRepository
from vera_backend.repositories.measurement_repository import  MeasurementRepository
from vera_backend.schemas.dataset import DatasetOutSchema, DatasetInSchema
from vera_backend.schemas.evaluation import EvaluationDetailOutSchema
from vera_backend.schemas.measure import MeasurementAggregationResponse, MeasurementAggregationRequest, \
    DimensionKeysResponse, DimensionValuesResponse
from vera_backend.schemas.model import ModelOutSchema, ModelInSchema
from vera_backend.schemas.project import (
    ProjectOutSchema,
    ProjectInSchema,
    ProjectDetailsOutSchema,
)


router = Router(tags=["project"])

project_repository = ProjectRepository()
dataset_repository = DatasetRepository()
model_repository = BaseRepository(model=Model)
evaluation_repository = EvaluationRepository()
measurement_repository = MeasurementRepository()


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


@router.post("/{pid}/datasets", response=DatasetOutSchema)
async def create_project_dataset(request, pid: uuid.UUID, data: DatasetInSchema):
    project = await project_repository.get(pid)

    dataset = await dataset_repository.save(
        Dataset(
            **data.model_dump(),
            project=project,
            storage_container=StorageContainer.Datasets,
        )
    )

    return dataset


@router.post("/{pid}/models", response=ModelOutSchema)
async def create_project_model(request, pid: uuid.UUID, data: ModelInSchema):
    project = await project_repository.get(pid)

    model = Model(
        **data.model_dump(),
        project=project,
        public=True,
        storage_container=StorageContainer.Models,
    )
    return await model_repository.save(model)



@router.get("/{pid}/evaluations", response=list[EvaluationDetailOutSchema])
async def get_project_evaluations(
    request,
    pid: uuid.UUID,
    status: EvaluationStatus | None = None,
    exclude_status: list[EvaluationStatus] = Query([]),
):
    project = await project_repository.get(pid)
    filter: dict[str, Any] = {"project": project}
    exclude = None
    if status is not None:
        filter["status"] = status
    if exclude_status:
        exclude = {"status__in": exclude_status}
    evaluations = await evaluation_repository.filter_with_related(filter, exclude)
    return evaluations


class ProjectPluginConfigResponse(Schema):
    name: str
    dataset_pid: uuid.UUID


@router.get("/{pid}/plugins/{plugin_name}/config", response=dict | None)
async def get_project_plugin_config(request, pid: uuid.UUID, plugin_name: str):
    project = await project_repository.get(pid, True)
    plugin = next(
        (p for p in project.get_enabled_plugins() if p.name == plugin_name), None
    )
    if not plugin:
        raise HttpError(404, f"Project {pid} has no plugin {plugin_name}")
    return plugin.config

@router.post("/{pid}/measurements/aggregate", response=MeasurementAggregationResponse)
async def aggregate_project_measurements(
    request,
    pid: uuid.UUID,
    data: MeasurementAggregationRequest
):
    project = await project_repository.get(pid)
    queryset = await measurement_repository.filter_queryset(observation__evaluation__project=project)
    results = await measurement_repository.aggregate_measurements(
        queryset, data.group_by, data.filters, data.aggregations
    )
    return {"results": results}

@router.get("/{pid}/measurements/dimension-keys", response=DimensionKeysResponse)
async def get_project_dimension_keys(request, pid: uuid.UUID):
    project = await project_repository.get(pid)
    queryset = await measurement_repository.filter_queryset(
        observation__evaluation__project=project
    )
    keys = await measurement_repository.get_unique_dimension_keys(queryset)
    return {"keys": keys}

@router.get("/{pid}/measurements/dimension-values/{key}", response=DimensionValuesResponse)
async def get_project_dimension_values(request, pid: uuid.UUID, key: str):
    project = await project_repository.get(pid)
    queryset = await measurement_repository.filter_queryset(
        observation__evaluation__project=project
    )
    values = await measurement_repository.get_unique_dimension_values(queryset, key)
    return {"key": key, "values": values}