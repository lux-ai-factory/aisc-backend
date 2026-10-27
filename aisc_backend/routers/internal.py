import datetime
import logging
import uuid
from pathlib import Path
from typing import Any

from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist
from ninja import Router, Schema, Form, UploadedFile, File, Body
from ninja.errors import HttpError
from pydantic import BaseModel, ConfigDict, Field

from aisc_backend import ledger
from aisc_backend.audit.log import log_action
from aisc_backend.auth.internal import InternalSharedKeyAuth
from aisc_backend.config_secrets import scrub_text
from aisc_backend.models import (
    Evaluation, EvaluationInput, EvaluationPlugin, EvaluationStatus, Observation, Metric, Measurement,
)
from aisc_backend.models.artifact import Artifact
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.base_repository import BaseRepository
from aisc_backend.repositories.evaluation_repository import EvaluationRepository
from aisc_backend.repositories.measurement_repository import MeasurementRepository
from aisc_backend.repositories.plugin_repository import EvaluationPluginRepository
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_backend.routers import plugin as plugin_routes
from aisc_backend.schemas.evaluation import EvaluationDetailOutSchema
from aisc_backend.schemas.measure import MeasureInSchema
from aisc_backend.schemas.project_config import ProjectConfigSelectionSchema
from aisc_plugin_interface import LLMConfig

logger = logging.getLogger(__name__)

router = Router(tags=["internal"], auth=InternalSharedKeyAuth())

NO_EVALUATION = "No evaluation found"
NO_EVALUATION_PLUGIN = "No evaluation plugin of this evaluation found"


async def _evaluation_or_404(evaluation_pid: uuid.UUID, with_related: bool = False) -> Evaluation:
    try:
        return await evaluation_repository.get(evaluation_pid, with_related)
    except Evaluation.DoesNotExist:
        raise HttpError(404, NO_EVALUATION)


async def _run_of_evaluation_or_404(
    evaluation_plugin_pid: uuid.UUID, evaluation_pid: uuid.UUID, with_related: bool = False
) -> EvaluationPlugin:
    try:
        return await evaluation_plugin_repository.get_of_evaluation(
            evaluation_plugin_pid, evaluation_pid, with_related=with_related)
    except EvaluationPlugin.DoesNotExist:
        raise HttpError(404, NO_EVALUATION_PLUGIN)


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


class EvaluationPluginInputSchema(Schema):
    pid: uuid.UUID | None = None
    name: str
    component_type: str
    component_name: str = ""
    data: str | None = None
    json_value: dict[str, Any] = Field(default_factory=dict)
    value: dict[str, Any] = Field(default_factory=dict)
    endpoint_url: str | None = None
    secret_key: str | None = None
    secret_encrypted_value: str | None = None
    #: the plugin's local model file (@assesses(local_model=...)): a run on it is not an AI card run;
    #: None, with local_model_unknown, for a model file of a plugin the backend could not load
    local_model: bool | None = False
    local_model_unknown: bool = False
    #: a helper model of the platform (`helper:<platform pid>/<name>`), with component_type "helper"
    #: and no component: the worker resolves its endpoint and key with the platform
    helper_ref: str | None = None


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
    try:
        return await evaluation_repository.get_including(evaluation_pid, include)
    except Evaluation.DoesNotExist:
        raise HttpError(404, "Evaluation not found")


@router.get("/evaluations/{evaluation_pid}/inputs", response=dict[str, list[EvaluationPluginInputSchema]])
async def get_evaluation_inputs(request, evaluation_pid: uuid.UUID):
    """Fetch the component input data (including decrypted secrets) for a
    worker to bind each evaluation plugin's inputs to the derived components.
    A helper model of the platform is handed by reference, with no component."""
    try:
        evaluation = await evaluation_repository.get_with_plugin_inputs(evaluation_pid)
    except Evaluation.DoesNotExist:
        raise HttpError(404, "Evaluation not found")

    result: dict[str, list[EvaluationPluginInputSchema]] = {}
    for evaluation_plugin in list(evaluation.evaluation_plugins.all()):
        local_model_input = await _local_model_input(evaluation_plugin.pid)
        entries = []
        for inp in list(evaluation_plugin.evaluation_inputs.all()):
            if inp.helper_ref is not None:
                entries.append(EvaluationPluginInputSchema(
                    pid=inp.pid, name=inp.name, component_type="helper", helper_ref=inp.helper_ref,
                    value=inp.value))
                continue
            component = inp.component
            config = (
                LLMConfig.model_validate(component.json_value or {})
                if component.component_type == "llm"
                else None
            )
            endpoint_url = config.endpoint_url if config is not None else None
            secret_key = config.secret_key if config is not None else None
            secret_encrypted_value = None
            if secret_key:
                secret_row = await project_config_repository.get_secret_by_key(
                    component.system.project.pid, secret_key
                )
                if secret_row is not None:
                    secret_encrypted_value = secret_row.encrypted_value
            entries.append(EvaluationPluginInputSchema(
                pid=inp.pid,
                name=inp.name,
                component_type=component.component_type,
                component_name=component.name,
                data=component.data,
                json_value=component.json_value,
                value=inp.value,
                endpoint_url=endpoint_url,
                secret_key=secret_key,
                secret_encrypted_value=secret_encrypted_value,
                **_local_model_flags(inp.name, component.component_type, local_model_input),
            ))
        result[str(evaluation_plugin.pid)] = entries
    return result


#: the plugin could not be loaded: which input is its local model file is not known
UNKNOWN = object()


def _local_model_flags(input_name: str, component_type: str, local_model_input) -> dict:
    if component_type != "model":
        return {"local_model": False, "local_model_unknown": False}
    if local_model_input is UNKNOWN:
        return {"local_model": None, "local_model_unknown": True}
    return {"local_model": input_name == local_model_input, "local_model_unknown": False}


async def _local_model_input(evaluation_plugin_pid: uuid.UUID):
    """The name of the plugin's local model input, read from its class (the worker may not load
    plugin classes); None when it declares none, UNKNOWN when it cannot be loaded here."""
    evaluation_plugin = await evaluation_plugin_repository.get_with_related(evaluation_plugin_pid)
    plugin = evaluation_plugin.plugin_config.plugin
    try:
        plugin_class = plugin_routes.plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)
    except Exception:
        logger.warning("plugin %s could not be loaded to find its local model input", plugin, exc_info=True)
        return UNKNOWN
    return getattr(plugin_class, "local_model_input", None)


class LocalModelFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    file: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LocalModelMarker(BaseModel):
    """The only value the worker stores on an input: the local model file a run was on."""
    model_config = ConfigDict(extra="forbid", strict=True)

    local_model: LocalModelFile


class EvaluationInputValueSchema(Schema):
    value: LocalModelMarker


@router.patch("/evaluations/{evaluation_pid}/inputs/{input_pid}/value", response=str)
async def store_evaluation_input_value(
    request, evaluation_pid: uuid.UUID, input_pid: uuid.UUID, data: EvaluationInputValueSchema
):
    """Store what a run was on, on one of its inputs: for a local model file, the file's name and
    hash, which keep the run apart from the AI card's."""
    try:
        evaluation_input = await EvaluationInput.objects.aget(
            pid=input_pid, evaluation_plugin__evaluation__pid=evaluation_pid
        )
    except EvaluationInput.DoesNotExist:
        raise HttpError(404, "No input of this evaluation found")
    evaluation_input.value = data.value.model_dump()
    local_model = data.value.local_model

    async def record():
        # the marker and its ledger event commit together
        await evaluation_input.asave(update_fields=["value"])
        await sync_to_async(ledger.emit_for_run)(
            "engine.evaluation.input_marked_local", evaluation_pid, item_type="evaluation", item_id=evaluation_pid,
            details={"input": evaluation_input.name, "file": local_model.file, "sha256": local_model.sha256})

    await ledger.atomically(record)

    await sync_to_async(log_action)(
        request, action="store_input_value", resource_type="evaluation",
        resource_id=str(evaluation_pid), metadata={"input": evaluation_input.name})
    return "ok"


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
                resource_id=str(evaluation_pid), metadata={"status": str(evaluation.status)})
            return evaluation.status

    evaluation.status = status
    await evaluation_repository.save(evaluation)

    await sync_to_async(log_action)(
        request, action="status_change", resource_type="evaluation",
        resource_id=str(evaluation_pid), metadata={"status": str(evaluation.status)})
    return evaluation.status


class PluginTimestampSchema(Schema):
    field: str  # "started_at" or "finished_at"


@router.patch("/evaluations/{evaluation_pid}/plugins/{evaluation_plugin_pid}/timestamp", response=str)
async def update_plugin_timestamp(
    request, evaluation_pid: uuid.UUID, evaluation_plugin_pid: uuid.UUID, data: PluginTimestampSchema
):
    evaluation = await _evaluation_or_404(evaluation_pid)
    eval_plugin = await _run_of_evaluation_or_404(evaluation_plugin_pid, evaluation_pid)

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
    evaluation = await _evaluation_or_404(evaluation_pid)
    eval_plugin = await _run_of_evaluation_or_404(evaluation_plugin_pid, evaluation_pid)

    eval_plugin.status = "Failed"
    eval_plugin.error_message = scrub_text(data.error_message)
    eval_plugin.finished_at = datetime.datetime.now(tz=datetime.timezone.utc)
    await evaluation_plugin_repository.save(eval_plugin)

    evaluation.status = EvaluationStatus.Failed
    await evaluation_repository.save(evaluation)

    await sync_to_async(log_action)(
        request, action="plugin_failed", resource_type="evaluation", resource_id=str(evaluation_pid),
        outcome="failed", metadata={"pluginPid": str(evaluation_plugin_pid)})
    return "ok"


@router.post("/evaluations/{evaluation_pid}/measures", response={201: Schema})
async def create_evaluation_measures(
    request,
    evaluation_pid: uuid.UUID,
    data: dict[uuid.UUID, list[MeasureInSchema]] = Body(...),
):
    evaluation = await _evaluation_or_404(evaluation_pid, True)
    # every named run must be of this evaluation before anything is written: all or nothing
    runs = {
        evaluation_plugin_uuid: await _run_of_evaluation_or_404(evaluation_plugin_uuid, evaluation_pid, True)
        for evaluation_plugin_uuid in data
    }

    async def record():
        # the measures and their ledger event commit together
        count = 0
        for evaluation_plugin_uuid, measures in data.items():
            # Create a new Observation for each plugin

            evaluation_plugin = runs[evaluation_plugin_uuid]
            plugin = evaluation_plugin.plugin_config.plugin

            observation = Observation(
                observer="AISC System",
                tool=str(plugin),
                evaluation=evaluation,
            )
            observation = await observation_repository.save(observation)

            measurement_objs = []
            for measure_in_schema in measures:
                measure_in_schema.description = (
                    ""
                    if measure_in_schema.description is None
                    else measure_in_schema.description
                )

                # Get or create Metric
                try:
                    metric = await metric_repository.get_one(name=measure_in_schema.name)
                except ObjectDoesNotExist:
                    metric = await metric_repository.save(
                        Metric(name=measure_in_schema.name)
                    )

                # Save Measure associated with the Observation
                measure_in_schema.observation = observation
                measure_in_schema.metric = metric

                measurement_objs.append(Measurement(**measure_in_schema.model_dump()))

            if measurement_objs:
                await measurement_repository.bulk_create(measurement_objs)
            count += len(measurement_objs)
        await sync_to_async(ledger.emit_for_run)(
            "engine.measures.recorded", evaluation.pid, item_type="evaluation", item_id=evaluation.pid,
            details={"count": count})

    await ledger.atomically(record)

    await sync_to_async(log_action)(
        request, action="record_measures", resource_type="evaluation",
        resource_id=str(evaluation_pid), metadata={"plugins": len(data)})
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
    evaluation = await _evaluation_or_404(evaluation_pid)
    evaluation_plugin = await _run_of_evaluation_or_404(evaluation_plugin_uuid, evaluation_pid, True)

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

    async def record():
        saved = await artifact_repository.save(artifact)
        await sync_to_async(ledger.emit_for_run)(
            "engine.artifact.uploaded", evaluation.pid, item_type="artifact", item_id=saved.pid,
            details={"size": file.size})
        return saved

    artifact = await ledger.atomically(record)

    await sync_to_async(log_action)(
        request, action="upload_artifact", resource_type="evaluation",
        resource_id=str(evaluation_pid), metadata={"artifact": original_filename})
    return UploadArtifactResponse(file_name=file.name)


@router.get("/files/dataset/{file_name}")
async def get_dataset_file(request, file_name: str):
    return await file_repository.get_s3_file_stream(StorageContainer.Datasets, file_name)


@router.get("/files/model/{file_name}")
async def get_model_file(request, file_name: str):
    return await file_repository.get_s3_file_stream(StorageContainer.Models, file_name)
