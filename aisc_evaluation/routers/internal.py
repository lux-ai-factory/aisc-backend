import datetime
import uuid
from pathlib import Path
from typing import Any

from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist
from ninja import Router, Schema, Form, UploadedFile, File, Body
from ninja.errors import HttpError
from pydantic import Field

from aisc_backend.audit.log import log_action
from aisc_backend.auth.internal import InternalSharedKeyAuth
from aisc_evaluation.models import Evaluation, EvaluationStatus, Observation, Metric, Measurement
from aisc_evaluation.models import Artifact
from aisc_backend.models.common import StorageContainer
from aisc_backend.models.ai_system import AIComponentType
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.base_repository import BaseRepository
from aisc_evaluation.repositories.evaluation_repository import EvaluationRepository
from aisc_evaluation.repositories.measurement_repository import MeasurementRepository
from aisc_plugins.repositories.plugin_repository import EvaluationPluginRepository
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_evaluation.schemas.evaluation import EvaluationDetailOutSchema
from aisc_evaluation.schemas.measure import MeasureInSchema
from aisc_backend.schemas.project_config import ProjectConfigSelectionSchema
from aisc_backend.services import component_secrets
from aisc_backend.services import components as component_service

router = Router(tags=["internal"], auth=InternalSharedKeyAuth())


class ProjectConfigsByPidRequest(Schema):
    project_config_selections: list[ProjectConfigSelectionSchema]


class InternalProjectConfigSchema(Schema):
    pid: uuid.UUID
    category: str
    key: str
    encrypted_value: str
    json_value: dict[str, Any]


class SelectionProjectConfigSchema(Schema):
    pid: uuid.UUID
    key: str
    plugin_config_key: str | None
    category: str
    encrypted_value: str
    json_value: dict[str, Any]


class AdapterInstallTargetSchema(Schema):
    package: str
    version: str
    source: str = ""


class EvaluationPluginInputSchema(Schema):
    name: str
    component_type: str
    data: str
    json_value: dict[str, Any] = Field(default_factory=dict)
    value: dict[str, Any] = Field(default_factory=dict)
    # Endpoint inputs (ADR 0009): the target and the adapter to reach it, with the
    # key still **encrypted**. The worker decrypts it in the run's door; it never
    # travels as plaintext and never reaches the plugin.
    url: str | None = None
    protocol: str | None = None
    adapter: str | None = None
    adapter_version: str | None = None
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    secret_encrypted_value: str | None = None
    secret_headers: dict[str, str] = Field(default_factory=dict)
    install_target: AdapterInstallTargetSchema | None = None


class PluginsStatusResponse(Schema):
    has_failed_plugins: bool
    total_plugins: int


evaluation_repository = EvaluationRepository()
evaluation_plugin_repository = EvaluationPluginRepository()
observation_repository = BaseRepository(model=Observation)
metric_repository = BaseRepository(model=Metric)
measurement_repository = MeasurementRepository()
project_config_repository = ProjectConfigRepository()
artifact_repository = BaseRepository(model=Artifact)


@router.get("/projects/settings/{project_pid}", response=list[InternalProjectConfigSchema])
async def get_project_configs(request, project_pid: uuid.UUID):
    settings = await project_config_repository.get_by_project(project_pid)
    return [
        InternalProjectConfigSchema(
            pid=setting.pid,
            category=setting.category,
            key=setting.key,
            encrypted_value=setting.encrypted_value,
            json_value=setting.json_value,
        )
        for setting in settings
    ]


@router.post("/projects/settings/{project_pid}/by-pid", response=list[SelectionProjectConfigSchema])
async def get_project_configs_by_pid(
    request, project_pid: uuid.UUID, data: ProjectConfigsByPidRequest
):
    settings = await project_config_repository.get_by_pids(
        project_pid,
        [selection.project_config_pid for selection in data.project_config_selections],
    )
    by_pid = {str(setting.pid): setting for setting in settings}
    return [
        SelectionProjectConfigSchema(
            pid=setting.pid,
            key=setting.key,
            plugin_config_key=selection.plugin_config_key,
            category=setting.category,
            encrypted_value=setting.encrypted_value,
            json_value=setting.json_value,
        )
        for selection in data.project_config_selections
        if (setting := by_pid.get(str(selection.project_config_pid))) is not None
    ]


@router.get("/evaluations/{evaluation_pid}", response=EvaluationDetailOutSchema)
async def get_evaluation_details(request, evaluation_pid: uuid.UUID, include: str = ""):
    return await evaluation_repository.get_including(evaluation_pid, include)


@router.get("/evaluations/{evaluation_pid}/inputs", response=dict[str, list[EvaluationPluginInputSchema]])
async def get_evaluation_inputs(request, evaluation_pid: uuid.UUID):
    """Fetch the component input data (including encrypted secrets) for a
    worker to bind each evaluation plugin's inputs to the derived components."""
    try:
        evaluation = await evaluation_repository.get_with_plugin_inputs(evaluation_pid)
    except Evaluation.DoesNotExist:
        raise HttpError(404, "Evaluation not found")

    result: dict[str, list[EvaluationPluginInputSchema]] = {}
    for evaluation_plugin in list(evaluation.evaluation_plugins.all()):
        entries = []
        for inp in list(evaluation_plugin.evaluation_inputs.all()):
            component = inp.component
            entry = EvaluationPluginInputSchema(
                name=inp.name,
                component_type=component.component_type,
                data=component.data,
                json_value=component.json_value,
                value=inp.value,
            )
            if component.component_type == AIComponentType.ENDPOINT:
                entry.url = component.url or ""
                entry.protocol = component.protocol or ""
                entry.adapter = component.adapter or ""
                entry.adapter_version = component.adapter_version or ""
                entry.adapter_config = component.adapter_config or {}
                entry.secret_encrypted_value = await _encrypted_secret(component)
                entry.secret_headers = await _encrypted_secret_headers(component)
                entry.install_target = component_service.adapter_install_target(
                    component.adapter
                )
            entries.append(entry)
        result[str(evaluation_plugin.pid)] = entries
    return result


async def _encrypted_secret(component) -> str | None:
    key_name = component.secret_key or component_secrets.component_key_name(component.pid)
    row = await project_config_repository.get_secret_by_key(component.project.pid, key_name)
    return row.encrypted_value if row is not None else None


async def _encrypted_secret_headers(component) -> dict[str, str]:
    headers: dict[str, str] = {}
    for header in await component_secrets.secret_header_names(component):
        row = await project_config_repository.get_secret_by_key(
            component.project.pid,
            component_secrets.component_header_name(component.pid, header),
        )
        if row is not None and row.encrypted_value:
            headers[header] = row.encrypted_value
    return headers


@router.get("/evaluations/{evaluation_pid}/plugins/status", response=PluginsStatusResponse)
async def check_evaluation_plugins_status(request, evaluation_pid: uuid.UUID):
    """Check if any plugins in the evaluation have failed."""
    evaluation = await evaluation_repository.get(evaluation_pid, True)

    has_failed_plugins, total_plugins = await evaluation_repository.plugin_status(evaluation)

    return PluginsStatusResponse(
        has_failed_plugins=has_failed_plugins,
        total_plugins=total_plugins,
    )


@router.put("/evaluations/{evaluation_pid}", response=str)
async def update_evaluation_status(
    request, evaluation_pid: uuid.UUID, status: EvaluationStatus
):
    evaluation = await evaluation_repository.get(evaluation_pid, True)

    # If trying to mark as Done, check if any plugins failed
    if status == EvaluationStatus.Done:
        has_failed_plugins, _ = await evaluation_repository.plugin_status(evaluation)
        if has_failed_plugins:
            evaluation.status = EvaluationStatus.Failed
            await evaluation_repository.save(evaluation)
            await sync_to_async(log_action)(
                request, action="status_change", resource_type="evaluation",
                resource_id=str(evaluation_pid), source_app="eval",
                metadata={
                    "projectPid": str(evaluation.project.pid),
                    "status": str(evaluation.status),
                })
            return evaluation.status

    evaluation.status = status
    await evaluation_repository.save(evaluation)

    await sync_to_async(log_action)(
        request, action="status_change", resource_type="evaluation",
        resource_id=str(evaluation_pid),
        metadata={
            "projectPid": str(evaluation.project.pid),
            "status": str(evaluation.status),
        })
    return evaluation.status


class PluginTimestampSchema(Schema):
    field: str  # "started_at" or "finished_at"


@router.patch("/evaluations/{evaluation_pid}/plugins/{evaluation_plugin_pid}/timestamp", response=str)
async def update_plugin_timestamp(
    request, evaluation_pid: uuid.UUID, evaluation_plugin_pid: uuid.UUID, data: PluginTimestampSchema
):
    evaluation = await evaluation_repository.get(evaluation_pid)
    if evaluation is None:
        raise HttpError(404, "No evaluation found")

    eval_plugin = await evaluation_plugin_repository.get(evaluation_plugin_pid)
    if eval_plugin is None:
        raise HttpError(404, "No evaluation plugin found")

    if data.field not in ("started_at", "finished_at"):
        raise HttpError(
            400, f"Invalid field: {data.field}. Must be 'started_at' or 'finished_at'"
        )

    setattr(eval_plugin, data.field, datetime.datetime.now(tz=datetime.timezone.utc))
    if data.field == "started_at":
        eval_plugin.status = "Running"
    elif data.field == "finished_at":
        if eval_plugin.status != "Failed":
            eval_plugin.status = "Done"
    await evaluation_plugin_repository.save(eval_plugin)

    return "ok"


class PluginFailureSchema(Schema):
    error_message: str = ""


@router.patch("/evaluations/{evaluation_pid}/plugins/{evaluation_plugin_pid}/fail", response=str)
async def mark_plugin_failed(
    request, evaluation_pid: uuid.UUID, evaluation_plugin_pid: uuid.UUID, data: PluginFailureSchema
):
    evaluation = await evaluation_repository.get(evaluation_pid, True)
    if evaluation is None:
        raise HttpError(404, "No evaluation found")

    eval_plugin = await evaluation_plugin_repository.get(evaluation_plugin_pid)
    if eval_plugin is None:
        raise HttpError(404, "No evaluation plugin found")

    eval_plugin.status = "Failed"
    eval_plugin.error_message = data.error_message
    eval_plugin.finished_at = datetime.datetime.now(tz=datetime.timezone.utc)
    await evaluation_plugin_repository.save(eval_plugin)

    evaluation.status = EvaluationStatus.Failed
    await evaluation_repository.save(evaluation)

    await sync_to_async(log_action)(
        request, action="plugin_failed", resource_type="evaluation", resource_id=str(evaluation_pid),
        outcome="failed", source_app="eval",
        metadata={
            "projectPid": str(evaluation.project.pid),
            "pluginPid": str(evaluation_plugin_pid),
        })
    return "ok"


@router.post("/evaluations/{evaluation_pid}/measures", response={201: Schema})
async def create_evaluation_measures(
    request,
    evaluation_pid: uuid.UUID,
    data: dict[uuid.UUID, list[MeasureInSchema]] = Body(...),
):
    evaluation = await evaluation_repository.get(evaluation_pid, True)

    for evaluation_plugin_uuid, measures in data.items():
        # Create a new Observation for each plugin

        evaluation_plugin = await evaluation_plugin_repository.get_with_related(evaluation_plugin_uuid)
        plugin = evaluation_plugin.plugin_config.plugin

        observation = Observation(
            observer="AISC System",
            tool=str(plugin),
            evaluation=evaluation,
        )
        observation = await observation_repository.save(observation)

        # Resolve every metric name once instead of one DB query per measure
        # (a fairness run can send hundreds of thousands of measurements).
        metric_names = {measure_in_schema.name for measure_in_schema in measures}
        metric_by_name: dict[str, Metric] = {}
        for name in metric_names:
            try:
                metric_by_name[name] = await metric_repository.get_one(name=name)
            except ObjectDoesNotExist:
                metric_by_name[name] = await metric_repository.save(Metric(name=name))

        measurement_objs = []
        for measure_in_schema in measures:
            measure_in_schema.description = (
                ""
                if measure_in_schema.description is None
                else measure_in_schema.description
            )

            measure_in_schema.observation = observation
            measure_in_schema.metric = metric_by_name[measure_in_schema.name]
            measurement_objs.append(Measurement(**measure_in_schema.model_dump()))

        if measurement_objs:
            await measurement_repository.bulk_create(measurement_objs)

    await sync_to_async(log_action)(
        request, action="record_measures", resource_type="evaluation",
        resource_id=str(evaluation_pid), source_app="eval",
        metadata={"projectPid": str(evaluation.project.pid), "plugins": len(data)})
    return Schema()


class UploadArtifactResponse(Schema):
    file_name: str


@router.post("/evaluations/{evaluation_pid}/artifacts", response=UploadArtifactResponse)
async def upload_evaluation_artifact(
    request,
    evaluation_pid: uuid.UUID,
    evaluation_plugin_uuid: uuid.UUID = Form(...),
    file: UploadedFile = File(...),
):
    evaluation = await evaluation_repository.get(evaluation_pid, True)
    if evaluation is None:
        raise HttpError(404, "No evaluation found")

    evaluation_plugin = await evaluation_plugin_repository.get_with_related(evaluation_plugin_uuid)
    if evaluation_plugin is None:
        raise HttpError(404, "No evaluation plugin found")

    plugin = evaluation_plugin.plugin_config.plugin

    original_filename = file.name
    suffix = Path(file.name).suffix.lower()
    file.name = f"{str(uuid.uuid4())}{suffix}"

    result = file_repository.upload_file(file, StorageContainer.Artifacts)
    if not result:
        raise HttpError(500, "Failed to upload artifact to storage")

    artifact = Artifact(
        name=original_filename,
        description=f"Artifact generated by {str(plugin)}",
        data=file.name,
        evaluation_plugin=evaluation_plugin
    )
    artifact = await artifact_repository.save(artifact)

    await sync_to_async(log_action)(
        request, action="upload_artifact", resource_type="evaluation",
        resource_id=str(evaluation_pid), source_app="eval",
        metadata={"projectPid": str(evaluation.project.pid), "artifact": original_filename})
    return UploadArtifactResponse(file_name=file.name)


@router.get("/files/dataset/{file_name}")
async def get_dataset_file(request, file_name: str):
    return await file_repository.get_s3_file_stream(StorageContainer.Datasets, file_name)


@router.get("/files/model/{file_name}")
async def get_model_file(request, file_name: str):
    return await file_repository.get_s3_file_stream(StorageContainer.Models, file_name)
