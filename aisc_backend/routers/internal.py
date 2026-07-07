import datetime
import uuid

from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist
from django.http import StreamingHttpResponse
from ninja import Router, Schema, Form, UploadedFile, File, Path, Body
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.internal import InternalSharedKeyAuth
from aisc_backend.models import EvaluationStatus, Observation, Metric, Measurement
from aisc_backend.models.artifact import Artifact
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.base_repository import BaseRepository
from aisc_backend.repositories.evaluation_repository import EvaluationRepository
from aisc_backend.repositories.plugin_repository import EvaluationPluginRepository
from aisc_backend.schemas.evaluation import EvaluationDetailOutSchema
from aisc_backend.schemas.measure import MeasureInSchema

router = Router(tags=["internal"], auth=InternalSharedKeyAuth())

evaluation_repository = EvaluationRepository()
evaluation_plugin_repository = EvaluationPluginRepository()
observation_repository = BaseRepository(model=Observation)
metric_repository = BaseRepository(model=Metric)


@router.get("/evaluations/{evaluation_pid}", response=EvaluationDetailOutSchema)
async def get_evaluation_details(request, evaluation_pid: uuid.UUID, include: str = ""):
    return await evaluation_repository.get_including(evaluation_pid, include)

@router.get("/evaluations/{evaluation_pid}/plugins/status", response=dict)
async def check_evaluation_plugins_status(request, evaluation_pid: uuid.UUID):
    """Check if any plugins in the evaluation have failed."""
    evaluation = await evaluation_repository.get(evaluation_pid, True)

    has_failed_plugins = await evaluation.evaluation_plugins.filter(status="Failed").aexists()
    total_plugins = await evaluation.evaluation_plugins.acount()

    return {
        "has_failed_plugins": has_failed_plugins,
        "total_plugins": total_plugins,
    }

@router.put("/evaluations/{evaluation_pid}", response=str)
async def update_evaluation_status(
    request, evaluation_pid: uuid.UUID, status: EvaluationStatus
):
    evaluation = await evaluation_repository.get(evaluation_pid, True)

    # If trying to mark as Done, check if any plugins failed
    if status == EvaluationStatus.Done:
        has_failed_plugins = await evaluation.evaluation_plugins.filter(status="Failed").aexists()
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
    evaluation = await evaluation_repository.get(evaluation_pid)
    if evaluation is None:
        raise HttpError(404, f"No evaluation found")

    eval_plugin = await evaluation_plugin_repository.get(evaluation_plugin_pid)
    if eval_plugin is None:
        raise HttpError(404, f"No evaluation plugin found")

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
    await eval_plugin.asave()

    return "ok"


class PluginFailureSchema(Schema):
    error_message: str = ""

@router.patch("/evaluations/{evaluation_pid}/plugins/{evaluation_plugin_pid}/fail", response=str)
async def mark_plugin_failed(
    request, evaluation_pid: uuid.UUID, evaluation_plugin_pid: uuid.UUID, data: PluginFailureSchema
):
    evaluation = await evaluation_repository.get(evaluation_pid)
    if evaluation is None:
        raise HttpError(404, f"No evaluation found")

    eval_plugin = await evaluation_plugin_repository.get(evaluation_plugin_pid)
    if eval_plugin is None:
        raise HttpError(404, f"No evaluation plugin found")

    eval_plugin.status = "Failed"
    eval_plugin.error_message = data.error_message
    eval_plugin.finished_at = datetime.datetime.now(tz=datetime.timezone.utc)
    await eval_plugin.asave()

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
        await observation_repository.save(observation)

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
            await Measurement.objects.abulk_create(measurement_objs)

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
    evaluation = await evaluation_repository.get(evaluation_pid)
    if evaluation is None:
        raise HttpError(404, f"No evaluation found")

    evaluation_plugin = await evaluation_plugin_repository.get_with_related(evaluation_plugin_uuid)
    if evaluation_plugin is None:
        raise HttpError(404, f"No evaluation plugin found")

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
    await artifact.asave()

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