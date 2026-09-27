import uuid
from typing import Any

from pathlib import Path
from aisc_backend.repositories import file_repository
from aisc_backend.services.feature_derivation import derive_features

from asgiref.sync import sync_to_async
from ninja import Router, Schema, Query
from ninja.errors import HttpError
from pydantic import Field

from aisc_backend import deployment
from aisc_backend.audit.log import log_action
# In the Configurator a project belongs to the people in it: the platform writes that down and
# this reads it. Nothing here decides membership, it only enforces it. Standalone never asks.
from aisc_backend.auth import membership
from aisc_backend.auth.keycloak import router_auth
from aisc_backend.platform_projects import platform_project_name

from aisc_backend.models import (
    EvaluationStatus,
    AIComponent,
    AIComponentType,
    AISystem,
)
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories.ai_component_repository import AISystemRepository, AIComponentRepository
from aisc_backend.repositories.evaluation_repository import EvaluationRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.repositories.measurement_repository import MeasurementRepository
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_backend.schemas.ai_system import (
    AISystemDetailOutSchema,
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
from aisc_plugin_interface import LLMConfig


router = Router(tags=["project"], auth=router_auth())

project_repository = ProjectRepository()
ai_system_repository = AISystemRepository()
ai_component_repository = AIComponentRepository()
project_config_repository = ProjectConfigRepository()
evaluation_repository = EvaluationRepository()
measurement_repository = MeasurementRepository()


class EvaluationInputTemplateEntrySchema(Schema):
    component_pid: uuid.UUID
    value: dict = Field(default_factory=dict)


@router.post("", response=ProjectOutSchema)
async def create_project(request, data: ProjectInSchema):
    if deployment.is_configurator():
        raise HttpError(403, "projects are made on the Configurator's launcher")
    project = await project_repository.create(data.name, None)
    # AUDIT: who created which project (best-effort; never breaks the create)
    await sync_to_async(log_action)(
        request, action="create", resource_type="project",
        resource_id=str(project.pid), metadata={"name": project.name})
    return project


@router.patch("/{pid}", response=ProjectOutSchema)
async def update_project(request, pid: uuid.UUID, data: ProjectInSchema):
    project = await project_repository.get(pid)
    if deployment.is_configurator():
        await sync_to_async(membership.require)(request, project.platform_project_id, "editor")
    updated = await project_repository.patch(project, data)
    # AUDIT: who renamed which project, and to what
    await sync_to_async(log_action)(
        request, action="update", resource_type="project",
        resource_id=str(pid), metadata={"name": data.name})
    return updated


async def project_for_platform(request, platform_project_id: uuid.UUID):
    """This engine's row for a platform project, making it the first time (configurator only).

    There is no workspace to create here. The project is chosen once, on the
    launcher; opening the engine inside it means working on it, so the first
    visit makes the row, every visit after finds it, and nobody is asked to
    name anything. The name shown is the platform's own.
    """
    # Entering the engine inside a project is work on that project, so it takes
    # an editor. It is not a way into one: a stranger is told there is no such
    # project, the same as everywhere else.
    await sync_to_async(membership.require)(request, platform_project_id, "editor")

    existing = await project_repository.filter(platform_project_id=platform_project_id)
    if existing:
        return existing[0]

    name = await sync_to_async(platform_project_name)(platform_project_id)
    project = await project_repository.create(
        name or f"project-{str(platform_project_id)[:8]}", platform_project_id
    )
    await sync_to_async(log_action)(
        request, action="create", resource_type="project",
        resource_id=str(project.pid),
        metadata={"name": project.name, "platformProjectPid": str(platform_project_id)})
    return project


# Standalone has no platform, so the route does not exist there (404).
if deployment.is_configurator():
    router.post("/for-platform/{platform_project_id}", response=ProjectOutSchema)(project_for_platform)


@router.get("", response=list[ProjectOutSchema])
async def get_projects(request, platform_project_id: uuid.UUID | None = None):
    """Standalone: every project the engine has (Sean's list; the parameter is not used).

    Configurator: the workspaces of one platform project, or all of them, and of
    those only the ones this caller is in. Behind the door there is one project's
    database, so that is the door's project.
    """
    if not deployment.is_configurator():
        return await project_repository.get_all()
    found = (
        await project_repository.get_all()
        if platform_project_id is None
        else await project_repository.filter(platform_project_id=platform_project_id)
    )
    return await sync_to_async(membership.visible)(request, found)


@router.get("/by-name/{name}", response=ProjectOutSchema)
async def get_project_by_name(request, name):
    if not deployment.is_configurator():
        return await project_repository.get_one(name=name)
    # A name is often a customer's: a stranger, and a name nobody has, get the same 404.
    found = await project_repository.filter(name=name)
    if not found:
        raise HttpError(404, "no such project")
    project = found[0]
    await sync_to_async(membership.require)(request, project.platform_project_id)
    return project


@router.get("/{pid}", response=ProjectDetailsOutSchema)
async def get_project_details(request, pid: uuid.UUID):
    project = await project_repository.get(pid, True)
    if deployment.is_configurator():
        await sync_to_async(membership.require)(request, project.platform_project_id)
    return project


async def derive_datashape(source_dataset: AIComponent) -> dict:
    """Best-effort derivation of a full data shape from a dataset component."""
    try:
        response = file_repository.get_object(
            bucket_name=StorageContainer.Datasets, object_name=source_dataset.data
        )
        file_content = response["Body"].read()
        suffix = Path(source_dataset.data).suffix.lower()
        fmt = suffix.removeprefix(".")
        if fmt not in ("csv", "parquet") or not source_dataset.data:
            return {}
        if not response:
            return {}
        return derive_features(file_content, fmt, str(source_dataset.pid))
    except Exception:
        return {}


@router.get("/{pid}/aisystem", response=AISystemDetailOutSchema)
async def get_project_aisystem(request, pid: uuid.UUID):
    if deployment.is_configurator():
        await sync_to_async(membership.for_project_pid)(request, pid)
    project = await project_repository.get(pid, True)

    def with_components():
        return (AISystem.objects.filter(project=project)
                .prefetch_related("components", "components__source_dataset")
                .afirst())

    system = await with_components()
    if system is None:
        await ai_system_repository.get_or_create_for_project(project)
        system = await with_components()
    return system


@router.post("/{pid}/components", response=AIComponentOutSchema)
async def create_project_component(request, pid: uuid.UUID, data: AIComponentInSchema):
    if deployment.is_configurator():
        await sync_to_async(membership.for_project_pid)(request, pid, "editor")
    if not data.name or not data.name.strip():
        raise HttpError(400, "Component name is required")
    project = await project_repository.get(pid)
    system = await ai_system_repository.get_or_create_for_project(project)

    source_dataset = None
    json_value = data.json_value or {}
    if data.source_dataset_pid:
        source_dataset = await ai_component_repository.get(data.source_dataset_pid)
        if source_dataset is None or source_dataset.component_type != AIComponentType.DATASET:
            raise HttpError(400, "source_dataset must be a dataset component")
        if data.component_type == AIComponentType.DATASHAPE and not json_value:
            json_value = await derive_datashape(source_dataset)

    if data.component_type == AIComponentType.LLM:
        secret_key = LLMConfig.model_validate(json_value).secret_key
        if secret_key and not await project_config_repository.secret_exists(project.pid, secret_key):
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
    if deployment.is_configurator():
        await sync_to_async(membership.for_project_pid)(request, pid, "editor")
    data.component_type = AIComponentType.DATASET
    return await create_project_component(request, pid, data)


@router.post("/{pid}/models", response=AIComponentOutSchema)
async def create_project_model(request, pid: uuid.UUID, data: AIComponentInSchema):
    if deployment.is_configurator():
        await sync_to_async(membership.for_project_pid)(request, pid, "editor")
    data.component_type = AIComponentType.MODEL
    return await create_project_component(request, pid, data)


@router.get("/{pid}/evaluations", response=list[EvaluationDetailOutSchema])
async def get_project_evaluations(
    request,
    pid: uuid.UUID,
    status: EvaluationStatus | None = None,
    exclude_status: list[EvaluationStatus] = Query([]),
):
    if deployment.is_configurator():
        await sync_to_async(membership.for_project_pid)(request, pid)
    project = await project_repository.get(pid)
    filter: dict[str, Any] = {"project": project}
    exclude = None
    if status is not None:
        filter["status"] = status
    if exclude_status:
        exclude = {"status__in": exclude_status}
    evaluations = await evaluation_repository.filter_with_related(filter, exclude)
    return evaluations


@router.get("/{pid}/evaluation-inputs-template", response=dict[str, dict[str, EvaluationInputTemplateEntrySchema]])
async def get_project_evaluation_inputs_template(request, pid: uuid.UUID):
    """Return the most recent evaluation's input selections per plugin, so the
    evaluation form can prefill inputs from a previous run."""
    if deployment.is_configurator():
        await sync_to_async(membership.for_project_pid)(request, pid)
    project = await project_repository.get(pid)
    latest = await evaluation_repository.latest_for_project(project)
    if latest is None:
        return {}

    template: dict[str, dict[str, EvaluationInputTemplateEntrySchema]] = {}
    for evaluation_plugin in list(latest.evaluation_plugins.all()):
        plugin_config = evaluation_plugin.plugin_config
        if plugin_config is None or plugin_config.plugin_id is None:
            continue
        plugin_name = plugin_config.plugin.name
        entries = template.setdefault(plugin_name, {})
        for inp in list(evaluation_plugin.evaluation_inputs.all()):
            entries[inp.name] = EvaluationInputTemplateEntrySchema(
                component_pid=inp.component.pid,
                value=inp.value,
            )
    return template


@router.get("/{pid}/plugins/{plugin_name}/config", response=dict | None)
async def get_project_plugin_config(request, pid: uuid.UUID, plugin_name: str):
    if deployment.is_configurator():
        await sync_to_async(membership.for_project_pid)(request, pid)
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
    if deployment.is_configurator():
        await sync_to_async(membership.for_project_pid)(request, pid)
    project = await project_repository.get(pid)
    queryset = await measurement_repository.filter_queryset(observation__evaluation__project=project)
    results = await measurement_repository.aggregate_measurements(
        queryset, data.group_by, data.filters, data.aggregations
    )
    return {"results": results}


@router.get("/{pid}/measurements/dimension-keys", response=DimensionKeysResponse)
async def get_project_dimension_keys(request, pid: uuid.UUID):
    if deployment.is_configurator():
        await sync_to_async(membership.for_project_pid)(request, pid)
    project = await project_repository.get(pid)
    queryset = await measurement_repository.filter_queryset(
        observation__evaluation__project=project
    )
    keys = await measurement_repository.get_unique_dimension_keys(queryset)
    return {"keys": keys}


@router.get("/{pid}/measurements/dimension-values/{key}", response=DimensionValuesResponse)
async def get_project_dimension_values(request, pid: uuid.UUID, key: str):
    if deployment.is_configurator():
        await sync_to_async(membership.for_project_pid)(request, pid)
    project = await project_repository.get(pid)
    queryset = await measurement_repository.filter_queryset(
        observation__evaluation__project=project
    )
    values = await measurement_repository.get_unique_dimension_values(queryset, key)
    return {"key": key, "values": values}
