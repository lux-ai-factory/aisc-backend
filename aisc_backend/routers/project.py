import uuid
from typing import Any

from asgiref.sync import sync_to_async
from ninja import Router, Schema, Query
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action

from aisc_backend.models import (
    EvaluationStatus,
    Evaluation,
    AIComponent,
    AISystem,
    AIComponentType,
    ProjectConfig,
    ProjectConfigCategory,
)
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories.base_repository import BaseRepository
from aisc_backend.repositories.evaluation_repository import EvaluationRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.repositories.measurement_repository import  MeasurementRepository
from aisc_backend.schemas.ai_system import (
    AISystemDetailOutSchema,
    AISystemOutSchema,
    AIComponentOutSchema,
    AIComponentInSchema,
)
from aisc_backend.schemas.evaluation import EvaluationDetailOutSchema
from aisc_backend.schemas.measure import MeasurementAggregationResponse, MeasurementAggregationRequest, \
    DimensionKeysResponse, DimensionValuesResponse
from aisc_backend.schemas.project import (
    ProjectOutSchema,
    ProjectInSchema,
    ProjectDetailsOutSchema,
)


router = Router(tags=["project"])

project_repository = ProjectRepository()
ai_system_repository = BaseRepository(model=AISystem)
ai_component_repository = BaseRepository(model=AIComponent)
evaluation_repository = EvaluationRepository()
measurement_repository = MeasurementRepository()


@router.post("", response=ProjectOutSchema)
async def create_project(request, data: ProjectInSchema):
    project = await project_repository.create(data.name)
    # AUDIT: who created which project (best-effort; never breaks the create)
    await sync_to_async(log_action)(
        request, action="create", resource_type="project",
        resource_id=str(project.pid), metadata={"name": project.name})
    return project


@router.patch("/{pid}", response=ProjectOutSchema)
async def update_project(request, pid: uuid.UUID, data: ProjectInSchema):
    project = await project_repository.get(pid)
    updated = await project_repository.patch(project, data)
    # AUDIT: who renamed which project, and to what
    await sync_to_async(log_action)(
        request, action="update", resource_type="project",
        resource_id=str(pid), metadata={"name": data.name})
    return updated


@router.get("", response=list[ProjectOutSchema])
async def get_projects(request):
    return await project_repository.get_all()


@router.get("/by-name/{name}", response=ProjectOutSchema)
async def get_project_by_name(request, name):
    return await project_repository.get_one(name=name)


@router.get("/{pid}", response=ProjectDetailsOutSchema)
async def get_project_details(request, pid: uuid.UUID):
    return await project_repository.get(pid, True)


async def get_or_create_aisystem(project):
    """Return (or create) the project's single AISystem."""
    system = await AISystem.objects.filter(project=project).afirst()
    if system is None:
        system = AISystem(project=project, name=f"{project.name} system",
                          description="")
        system = await ai_system_repository.save(system)
    return system


async def derive_datashape(source_dataset: AIComponent) -> dict:
    """Best-effort derivation of a datashape document from a dataset component."""
    from pathlib import Path
    from aisc_backend.repositories import file_repository
    from aisc_backend.services.feature_derivation import derive_features

    try:
        suffix = Path(source_dataset.data).suffix.lower()
        fmt = suffix.removeprefix(".")
        if fmt not in ("csv", "parquet") or not source_dataset.data:
            return {}
        response = await sync_to_async(file_repository.get_object)(
            source_dataset.storage_container, source_dataset.data)
        if not response:
            return {}
        return derive_features(response["Body"].read(), fmt, str(source_dataset.pid))
    except Exception:
        return {}


@router.get("/{pid}/aisystem", response=AISystemDetailOutSchema)
async def get_project_aisystem(request, pid: uuid.UUID):
    project = await project_repository.get(pid, True)
    system = await (
        AISystem.objects.filter(project=project)
        .prefetch_related("components",
                          "components__source_dataset")
        .afirst()
    )
    if system is None:
        system = await get_or_create_aisystem(project)
    return system


@router.post("/{pid}/components", response=AIComponentOutSchema)
async def create_project_component(request, pid: uuid.UUID, data: AIComponentInSchema):
    if not data.name or not data.name.strip():
        raise HttpError(400, "Component name is required")
    project = await project_repository.get(pid)
    system = await get_or_create_aisystem(project)

    source_dataset = None
    json_value = data.json_value or {}
    if data.source_dataset_pid:
        source_dataset = await ai_component_repository.get(data.source_dataset_pid)
        if source_dataset is None or source_dataset.component_type != AIComponentType.DATASET:
            raise HttpError(400, "source_dataset must be a dataset component")
        if data.component_type == AIComponentType.DATASHAPE and not json_value:
            json_value = await derive_datashape(source_dataset)

    if data.component_type == AIComponentType.LLM:
        secret_key = json_value.get("secret_key") or ""
        if secret_key:
            secret_exists = await ProjectConfig.objects.filter(
                project=project,
                key=secret_key,
                category=ProjectConfigCategory.SECRETS,
            ).aexists()
            if not secret_exists:
                raise HttpError(400, "Configured API key secret does not exist in this project")

    component = AIComponent(
        name=data.name,
        description=data.description or "",
        component_type=data.component_type or AIComponentType.MODEL,
        source_dataset=source_dataset,
        json_value=json_value,
        system=system,
        storage_container=(
            StorageContainer.Datasets
            if data.component_type == AIComponentType.DATASET
            else StorageContainer.Models
        ),
    )
    saved = await ai_component_repository.save(component)
    await sync_to_async(log_action)(
        request, action="create", resource_type="component",
        resource_id=str(getattr(saved, "pid", "")),
        metadata={"projectPid": str(pid), "name": getattr(saved, "name", None),
                  "component_type": getattr(saved, "component_type", None)})
    return saved


@router.post("/{pid}/datasets", response=AIComponentOutSchema)
async def create_project_dataset(request, pid: uuid.UUID, data: AIComponentInSchema):
    data.component_type = AIComponentType.DATASET
    return await create_project_component(request, pid, data)


@router.post("/{pid}/models", response=AIComponentOutSchema)
async def create_project_model(request, pid: uuid.UUID, data: AIComponentInSchema):
    data.component_type = AIComponentType.MODEL
    return await create_project_component(request, pid, data)



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


@router.get("/{pid}/evaluation-inputs-template", response=dict)
async def get_project_evaluation_inputs_template(request, pid: uuid.UUID):
    """Return the most recent evaluation's input selections per plugin, so the
    evaluation form can prefill inputs from a previous run."""
    project = await project_repository.get(pid)
    latest = await (
        Evaluation.objects.filter(project=project).order_by("-created_at").afirst()
    )
    if latest is None:
        return {}

    template: dict[str, dict[str, dict]] = {}
    async for evaluation_plugin in (
        latest.evaluation_plugins.select_related("plugin_config__plugin").all()
    ):
        plugin_config = evaluation_plugin.plugin_config
        if plugin_config is None or plugin_config.plugin_id is None:
            continue
        plugin_name = plugin_config.plugin.name
        entries = template.setdefault(plugin_name, {})
        async for inp in evaluation_plugin.evaluation_inputs.select_related("component").all():
            entries[inp.name] = {
                "component_pid": str(inp.component.pid),
                "value": inp.value,
            }
    return template


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