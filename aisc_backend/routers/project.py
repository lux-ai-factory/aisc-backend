import uuid
from typing import Any

from pathlib import Path
from aisc_backend.repositories import file_repository
from aisc_backend.services.feature_derivation import derive_features

from asgiref.sync import sync_to_async
from django.db import transaction
from ninja import Router, Schema, Query
from ninja.errors import HttpError
from pydantic import Field

from aisc_backend.audit.clerk import clerk
from aisc_backend.audit.log import log_action
from aisc_backend.services import celery_service

from aisc_backend.auth.access import (
    caller_of,
    owner_or_403,
    refuse_losing_last_owner,
    role_or_404,
)
from aisc_backend.auth.membership import InvalidMembership, validate_role
from aisc_backend.repositories.membership_repository import MembershipRepository

from aisc_evaluation.models import EvaluationStatus
from aisc_backend.models import AIComponent, AIComponentType, Project, ProjectStatus
from aisc_backend.models.common import StorageContainer
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.repositories.ai_component_repository import AISystemRepository, AIComponentRepository
from aisc_backend.services import component_secrets as component_secrets
from aisc_backend.services import components as component_service
from aisc_evaluation.repositories.evaluation_repository import EvaluationRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_evaluation.repositories.measurement_repository import MeasurementRepository
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_backend.schemas.ai_system import (
    AISystemDetailOutSchema,
    AIComponentOutSchema,
    AIComponentInSchema,
)
from aisc_evaluation.schemas.evaluation import EvaluationDetailOutSchema
from aisc_evaluation.schemas.measure import MeasurementAggregationResponse, MeasurementAggregationRequest, \
    DimensionKeysResponse, DimensionValuesResponse
from aisc_backend.schemas.project import (
    ProjectOutSchema,
    ProjectInSchema,
    ProjectDetailsOutSchema,
)


router = Router(tags=["project"])

project_repository = ProjectRepository()
ai_system_repository = AISystemRepository()
ai_component_repository = AIComponentRepository()
project_config_repository = ProjectConfigRepository()
evaluation_repository = EvaluationRepository()
measurement_repository = MeasurementRepository()
membership_repository = MembershipRepository()


class EvaluationInputTemplateEntrySchema(Schema):
    component_pid: uuid.UUID
    value: dict = Field(default_factory=dict)


@router.post("", response=ProjectOutSchema)
async def create_project(request, data: ProjectInSchema):
    # The person who starts a project owns it — project never exists unowned.
    caller = caller_of(request)
    project = await sync_to_async(_create_project_with_owner)(
        data.name, caller.subject, caller.email
    )
    # AUDIT: who created which project (best-effort; never breaks the create)
    await sync_to_async(log_action)(
        request, action="create", resource_type="project",
        resource_id=str(project.pid), metadata={"projectPid": str(project.pid), "name": project.name})
    return project


def _create_project_with_owner(name: str, subject: str, email: str | None) -> Project:
    """Create the project and its first owner in one transaction.

    Sync ORM on purpose: Django's Atomic has no async context manager, so the
    two inserts must share one connection, which the async path cannot give.
    """
    with transaction.atomic():
        project = Project(name=name, status=ProjectStatus.Created)
        project.save()
        ProjectMember.objects.create(
            project=project, subject=subject, email=email or "",
            role=MembershipRole.Owner,
        )
    return project


@router.patch("/{pid}", response=ProjectOutSchema)
async def update_project(request, pid: uuid.UUID, data: ProjectInSchema):
    project = await project_repository.get(pid)
    await role_or_404(request, project)
    updated = await project_repository.patch(project, data)
    # AUDIT: who renamed which project, and to what
    await sync_to_async(log_action)(
        request, action="update", resource_type="project",
        resource_id=str(pid), metadata={"projectPid": str(pid), "name": data.name})
    return updated


@router.get("", response=list[ProjectOutSchema])
async def get_projects(request):
    caller = caller_of(request)
    if caller.has_role("admin"):
        return await project_repository.get_all()
    return await membership_repository.projects_for(caller.subject)


@router.get("/by-name/{name}", response=ProjectOutSchema)
async def get_project_by_name(request, name):
    project = await project_repository.get_one(name=name)
    await role_or_404(request, project)
    return project


@router.get("/{pid}", response=ProjectDetailsOutSchema)
async def get_project_details(request, pid: uuid.UUID):
    project = await project_repository.get(pid, True)
    await role_or_404(request, project)
    return project


class MemberIn(Schema):
    subject: str
    email: str | None = None
    role: str = "viewer"


class MemberRoleIn(Schema):
    role: str


class MemberOut(Schema):
    subject: str
    email: str
    role: str


@router.get("/{pid}/members", response=list[MemberOut])
async def project_members(request, pid: uuid.UUID):
    project = await project_repository.get(pid)
    await role_or_404(request, project)
    return await membership_repository.members(project)


@router.post("/{pid}/members", response=MemberOut)
async def add_project_member(request, pid: uuid.UUID, data: MemberIn):
    project = await project_repository.get(pid)
    await owner_or_403(request, project)
    try:
        role = validate_role(data.role)
    except InvalidMembership as exc:
        raise HttpError(422, str(exc))
    member = await membership_repository.add_member(
        project, data.subject, data.email, role
    )
    await sync_to_async(log_action)(
        request, action="member_added", resource_type="project_member",
        resource_id=data.subject, metadata={"projectPid": str(pid), "role": member["role"]})
    return MemberOut(**member)


@router.put("/{pid}/members/{subject}", response=MemberOut)
async def set_project_member_role(request, pid: uuid.UUID, subject: str, data: MemberRoleIn):
    project = await project_repository.get(pid)
    await owner_or_403(request, project)
    try:
        wanted = validate_role(data.role)
    except InvalidMembership as exc:
        raise HttpError(422, str(exc))
    if wanted != "owner":
        await refuse_losing_last_owner(project, subject)
    member = await membership_repository.add_member(project, subject, None, wanted)
    await sync_to_async(log_action)(
        request, action="member_role_changed", resource_type="project_member",
        resource_id=subject, metadata={"projectPid": str(pid), "role": member["role"]})
    return MemberOut(**member)


@router.delete("/{pid}/members/{subject}", response={204: None})
async def remove_project_member(request, pid: uuid.UUID, subject: str):
    project = await project_repository.get(pid)
    await owner_or_403(request, project)
    await refuse_losing_last_owner(project, subject)
    if not await membership_repository.remove_member(project, subject):
        raise HttpError(404, f"{subject!r} is not in this project")
    await sync_to_async(log_action)(
        request, action="member_removed", resource_type="project_member",
        resource_id=subject, metadata={"projectPid": str(pid)})
    return 204, None


@router.get("/{pid}/logs", response=dict)
async def get_project_logs(request, pid: uuid.UUID):
    """The project's ledger events, for any member of it. Same door as
    /audit/project/{pid}, under the project route for the webapp."""
    project = await project_repository.get(pid)
    await role_or_404(request, project)
    return {"events": clerk.list_events(limit=1000, project_id=str(pid))}


@router.get("/{pid}/tasks", response=dict)
async def get_project_tasks(request, pid: uuid.UUID):
    """The celery tasks of this project's evaluations only. A member sees its
    own project's tasks and nothing else."""
    project = await project_repository.get(pid)
    await role_or_404(request, project)
    evaluations = await evaluation_repository.failed_or_active_for_project(project)
    tasks = []
    for evaluation in evaluations:
        task_id = getattr(evaluation, "task", None)
        state = await celery_service.get_evaluation_task_state(task_id)
        if task_id is not None:
            tasks.append({
                "evaluation_pid": str(evaluation.pid),
                "task_id": str(task_id),
                "state": state,
            })
    return {"tasks": tasks}


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


def _apply_endpoint_payload(component: AIComponent, data: AIComponentInSchema) -> None:
    """Fill an endpoint component from a preset form or the advanced payload
    (ADR 0009). A field left out keeps the component's current value."""
    if data.preset:
        try:
            component_service.apply_preset(component, data.preset, data.fields or {})
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
    if data.component_type is not None:
        component.component_type = data.component_type
    if data.protocol is not None:
        component.protocol = data.protocol
    if data.url is not None:
        component.url = data.url
    if data.adapter is not None:
        component.adapter = data.adapter
    if data.adapter_version is not None:
        component.adapter_version = data.adapter_version
    if data.secret_key is not None:
        component.secret_key = data.secret_key
    if data.adapter_config is not None:
        try:
            component.adapter_config = component_service.validate_config(
                component.adapter, data.adapter_config
            )
        except Exception as exc:
            raise HttpError(400, f"adapter config is invalid: {exc}") from exc
    elif data.preset:
        # A preset's config is validated too (it fills adapter_config).
        try:
            component.adapter_config = component_service.validate_config(
                component.adapter, component.adapter_config
            )
        except Exception as exc:
            raise HttpError(400, f"adapter config is invalid: {exc}") from exc


async def _store_component_secret(component: AIComponent, data: AIComponentInSchema) -> None:
    """Store the component's key and secret headers in the project's secret store.

    The key is kept under ``component:<pid>:key`` and the component records that
    name; a component may instead reference an existing project secret by key.
    """
    if data.secret is not None:
        key_name = component.secret_key or component_secrets.component_key_name(component.pid)
        if data.secret == "":
            await component_secrets.clear_secret(component.project_id, key_name)
        else:
            await component_secrets.set_secret(component.project_id, key_name, data.secret)
            if component.secret_key != key_name:
                component.secret_key = key_name
                await component.asave(update_fields=["secret_key"])
    for header, value in (data.secret_headers or {}).items():
        if value == "":
            await component_secrets.remove_secret_header(component, header)
        else:
            await component_secrets.set_secret_header(component, header, value)


@router.get("/{pid}/aisystem", response=AISystemDetailOutSchema)
async def get_project_aisystem(request, pid: uuid.UUID):
    project = await project_repository.get(pid, True)
    await role_or_404(request, project)
    system = await ai_system_repository.get_with_components(project)
    if system is None:
        await ai_system_repository.get_or_create_for_project(project)
        system = await ai_system_repository.get_with_components(project)
    return system


@router.post("/{pid}/components", response=AIComponentOutSchema)
async def create_project_component(request, pid: uuid.UUID, data: AIComponentInSchema):
    if not data.name or not data.name.strip():
        raise HttpError(400, "Component name is required")
    project = await project_repository.get(pid)
    await role_or_404(request, project)
    system = await ai_system_repository.get_or_create_for_project(project)

    source_dataset = None
    json_value = data.json_value or {}
    if data.source_dataset_pid:
        source_dataset = await ai_component_repository.get(data.source_dataset_pid)
        if source_dataset is None or source_dataset.component_type != AIComponentType.DATASET:
            raise HttpError(400, "source_dataset must be a dataset component")
        if data.component_type == AIComponentType.DATASHAPE and not json_value:
            json_value = await derive_datashape(source_dataset)

    if data.secret_key and not await project_config_repository.secret_exists(project.pid, data.secret_key):
        raise HttpError(400, "Configured API key secret does not exist in this project")

    component = AIComponent(
        name=data.name,
        description=data.description or "",
        component_type=data.component_type or AIComponentType.MODEL,
        source_dataset=source_dataset,
        json_value=json_value,
        system=system,
        project=project,
        storage_container=(
            StorageContainer.Datasets
            if data.component_type == AIComponentType.DATASET
            else StorageContainer.Models
        ),
    )
    _apply_endpoint_payload(component, data)
    saved = await ai_component_repository.save(component)
    await _store_component_secret(saved, data)
    await sync_to_async(log_action)(
        request, action="create", resource_type="component",
        resource_id=str(getattr(saved, "pid", "")),
        metadata={"projectPid": str(pid), "name": getattr(saved, "name", None),
                  "component_type": getattr(saved, "component_type", None)})
    return await component_service.component_row(saved)


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
    await role_or_404(request, project)
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
    project = await project_repository.get(pid)
    await role_or_404(request, project)
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
    project = await project_repository.get(pid, True)
    await role_or_404(request, project)
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
    await role_or_404(request, project)
    queryset = await measurement_repository.filter_queryset(observation__evaluation__project=project)
    results = await measurement_repository.aggregate_measurements(
        queryset, data.group_by, data.filters, data.aggregations
    )
    return {"results": results}


@router.get("/{pid}/measurements/dimension-keys", response=DimensionKeysResponse)
async def get_project_dimension_keys(request, pid: uuid.UUID):
    project = await project_repository.get(pid)
    await role_or_404(request, project)
    queryset = await measurement_repository.filter_queryset(
        observation__evaluation__project=project
    )
    keys = await measurement_repository.get_unique_dimension_keys(queryset)
    return {"keys": keys}


@router.get("/{pid}/measurements/dimension-values/{key}", response=DimensionValuesResponse)
async def get_project_dimension_values(request, pid: uuid.UUID, key: str):
    project = await project_repository.get(pid)
    await role_or_404(request, project)
    queryset = await measurement_repository.filter_queryset(
        observation__evaluation__project=project
    )
    values = await measurement_repository.get_unique_dimension_values(queryset, key)
    return {"key": key, "values": values}
