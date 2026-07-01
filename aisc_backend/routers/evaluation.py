import base64
import datetime
import uuid

from pathlib import Path
from asgiref.sync import sync_to_async
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from ninja import Router, Schema, Body, Form, File, UploadedFile
from ninja.errors import HttpError

from aisc_backend.models import EvaluationPlugin, Dataset, EvaluationPluginInputFile, Measurement
from aisc_backend.models.artifact import Artifact
from aisc_backend.models.common import StorageContainer
from aisc_backend.models.observation import Observation
from aisc_backend.models.metric import Metric

from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.models.model import Model
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.base_repository import BaseRepository
from aisc_backend.repositories.dataset_repository import DatasetRepository
from aisc_backend.repositories.evaluation_repository import EvaluationRepository
from aisc_backend.repositories.measurement_repository import MeasurementRepository
from aisc_backend.repositories.plugin_repository import EvaluationPluginRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.artifact import ArtifactOutSchema, ArtifactPreviewSchema
from aisc_backend.schemas.evaluation import (
    EvaluationDetailOutSchema,
    EvaluationByStatusResponseSchema,
    EvaluationOutSchema,
)
from aisc_backend.schemas.measure import MeasureInSchema, MeasurementAggregationResponse, \
    MeasurementAggregationRequest, DimensionKeysResponse, DimensionValuesResponse, MetricNamesResponse, \
    DimensionKeysRequest, DimensionValuesRequest, MetricNamesRequest
from aisc_backend.services import celery_service
from aisc_backend.utils.file_utils import csv_bytes_to_rows, zip_bytes_to_file_list
from aisc_backend.audit.log import log_action
from aisc_plugin_interface import InputType

router = Router(tags=["evaluation"])

evaluation_repository = EvaluationRepository()
project_repository = ProjectRepository()
dataset_repository = DatasetRepository()
model_repository = BaseRepository(model=Model)
observation_repository = BaseRepository(model=Observation)
measurement_repository = MeasurementRepository()
metric_repository = BaseRepository(model=Metric)
evaluation_plugin_repository = EvaluationPluginRepository()


class EvaluationPluginInputInSchema(Schema):
    pid: uuid.UUID
    name: str
    input_type: InputType


class EvaluationPluginInSchema(Schema):
    name: str
    inputs: list[EvaluationPluginInputInSchema] | None = None


class CreateEvaluationRequest(Schema):
    project_pid: uuid.UUID
    plugins_to_run: list[EvaluationPluginInSchema]


@router.post("/task", response=EvaluationOutSchema)
async def create_evaluation_task(request, data: CreateEvaluationRequest):
    project = await project_repository.get(data.project_pid, True)

    # Resolve and validate plugins before writing anything to DB
    resolved: list[tuple[EvaluationPluginInSchema, object]] = []
    for plugin_to_run in data.plugins_to_run:
        plugin = next(
            (p for p in project.get_enabled_plugins() if p.name == plugin_to_run.name),
            None,
        )
        if plugin:
            if plugin.current_config is None:
                raise HttpError(400, f"Plugin {plugin.name} has no current config")
            resolved.append((plugin_to_run, plugin.current_config))

    evaluation = Evaluation(status=EvaluationStatus.Pending, project=project)
    evaluation = await evaluation_repository.create(evaluation)

    evaluation_plugins = []
    for plugin_to_run, plugin_config in resolved:
        run_plugin = EvaluationPlugin(
            plugin_config=plugin_config, evaluation=evaluation
        )
        run_plugin = await evaluation_plugin_repository.create(run_plugin)

        # Link the input files
        if plugin_to_run.inputs:
            for input_data in plugin_to_run.inputs:
                # Determine content type and model
                if input_data.input_type == InputType.DATASET:
                    content_model = Dataset
                elif input_data.input_type == InputType.MODEL:
                    content_model = Model
                else:
                    continue

                # Get the actual object (Dataset or Model) from DB
                # NOTE: Ensure you have access to a repository or use the model's objects
                content_obj = await content_model.objects.aget(pid=input_data.pid)
                content_type = await sync_to_async(
                    ContentType.objects.get_for_model
                )(content_model)
                # Create the link
                input_file = EvaluationPluginInputFile(
                    evaluation_plugin=run_plugin,
                    name=input_data.name,  # This matches the key in the @input decorator
                    content_type=content_type,
                    object_id=content_obj.id,
                    content_object=content_obj,
                )
                await input_file.asave()

        evaluation_plugins.append(run_plugin)

    evaluation_plugins_task = await celery_service.run_evaluation(evaluation.pid)

    evaluation.task = evaluation_plugins_task.task_id
    await evaluation_repository.save(evaluation)

    # AUDIT: who ran which evaluation, on which project, with which plugins (the core webapp action)
    await sync_to_async(log_action)(
        request, action="run", resource_type="evaluation", resource_id=str(evaluation.pid),
        metadata={"projectPid": str(data.project_pid),
                  "plugins": [p.name for p in data.plugins_to_run]})
    return evaluation


@router.get("/{evaluation_pid}", response=EvaluationDetailOutSchema)
async def get_evaluation_details(request, evaluation_pid: uuid.UUID, include: str = ""):
    return await evaluation_repository.get_including(evaluation_pid, include)


@router.get("", response=list[EvaluationByStatusResponseSchema])
async def get_evaluations_by_status(request, status: EvaluationStatus):
    return await evaluation_repository.filter(status=status)


@router.put("/{evaluation_pid}", response=str)
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


@router.get("/{evaluation_pid}/plugins/status", response=dict)
async def check_evaluation_plugins_status(request, evaluation_pid: uuid.UUID):
    """Check if any plugins in the evaluation have failed."""
    evaluation = await evaluation_repository.get(evaluation_pid, True)

    has_failed_plugins = await evaluation.evaluation_plugins.filter(status="Failed").aexists()
    total_plugins = await evaluation.evaluation_plugins.acount()

    return {
        "has_failed_plugins": has_failed_plugins,
        "total_plugins": total_plugins,
    }


class PluginTimestampSchema(Schema):
    field: str  # "started_at" or "finished_at"


@router.patch("/{evaluation_pid}/plugins/{evaluation_plugin_pid}/timestamp", response=str)
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


@router.patch("/{evaluation_pid}/plugins/{evaluation_plugin_pid}/fail", response=str)
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


@router.post("/{evaluation_pid}/measures", response={201: Schema})
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


@router.post("/{evaluation_pid}/artifacts", response=UploadArtifactResponse)
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


@router.get("/{evaluation_pid}/artifacts", response=list[ArtifactOutSchema])
async def get_evaluation_artifacts(
    request, evaluation_pid: uuid.UUID, evaluation_plugin_uuid: uuid.UUID
):
    evaluation = await evaluation_repository.get_with_related(evaluation_pid)

    if evaluation is None:
        raise HttpError(404, f"No evaluation found")

    evaluation_plugin = await evaluation_plugin_repository.get_with_related(evaluation_plugin_uuid)
    if evaluation_plugin is None:
        raise HttpError(404, f"No evaluation plugin found")

    response: list[ArtifactOutSchema] = []

    artifacts = evaluation_plugin.get_artifacts()
    for artifact in artifacts:
        path = Path(artifact.data)
        suffix = path.suffix.lower()

        preview_data = None
        object_response = file_repository.get_object(
            StorageContainer.Artifacts, artifact.data
        )
        file_content = object_response["Body"].read()

        if suffix == ".csv":
            preview_data = csv_bytes_to_rows(file_content)

        if suffix == ".png":
            base64_image = base64.b64encode(file_content).decode("utf-8")
            preview_data = f"data:image/png;base64,{base64_image}"

        if suffix == ".pdf":
            base64_pdf = base64.b64encode(file_content).decode("utf-8")
            preview_data = f"data:application/pdf;base64,{base64_pdf}"

        if suffix == ".zip":
            preview_data = zip_bytes_to_file_list(file_content)

        if suffix == ".log" or suffix == ".txt":
            preview_data = file_content.decode('utf-8')

        artifact_out_schema = ArtifactOutSchema.model_validate(artifact)
        artifact_preview: ArtifactPreviewSchema = ArtifactPreviewSchema(
            data=preview_data, type=suffix
        )

        artifact_out_schema.preview = artifact_preview
        response.append(artifact_out_schema)

    return response

@router.post("/{evaluation_pid}/measurements/aggregate", response=MeasurementAggregationResponse)
async def aggregate_evaluation_measurements(
    request,
    evaluation_pid: uuid.UUID,
    data: MeasurementAggregationRequest
):
    evaluation = await evaluation_repository.get(evaluation_pid)
    filter_params = {"observation__evaluation": evaluation}

    if data.evaluation_plugin_pid:
        evaluation_plugin = await evaluation_plugin_repository.get_with_related(data.evaluation_plugin_pid)
        plugin = evaluation_plugin.plugin_config.plugin
        filter_params["observation__tool"] = str(plugin)
    
    if data.metric_name:
        filter_params["metric__name"] = data.metric_name

    queryset = await measurement_repository.filter_queryset(**filter_params)
    results = await measurement_repository.aggregate_measurements(
        queryset, data.group_by, data.filters, data.aggregations
    )
    return {"results": results}

@router.post("/{evaluation_pid}/measurements/dimension-keys", response=DimensionKeysResponse)
async def get_evaluation_dimension_keys(
    request, 
    evaluation_pid: uuid.UUID, 
    data: DimensionKeysRequest
):
    evaluation = await evaluation_repository.get(evaluation_pid)
    filter_params = {"observation__evaluation": evaluation}

    if data.evaluation_plugin_pid:
        evaluation_plugin = await evaluation_plugin_repository.get_with_related(data.evaluation_plugin_pid)
        plugin = evaluation_plugin.plugin_config.plugin
        filter_params["observation__tool"] = str(plugin)
    
    if data.metric_name:
        filter_params["metric__name"] = data.metric_name

    queryset = await measurement_repository.filter_queryset(**filter_params)
    keys = await measurement_repository.get_unique_dimension_keys(queryset)
    return {"keys": keys}

@router.post("/{evaluation_pid}/measurements/dimension-values/{key}", response=DimensionValuesResponse)
async def get_evaluation_dimension_values(
    request, 
    evaluation_pid: uuid.UUID, 
    key: str,
    data: DimensionValuesRequest
):
    evaluation = await evaluation_repository.get(evaluation_pid)
    filter_params = {"observation__evaluation": evaluation}

    if data.evaluation_plugin_pid:
        evaluation_plugin = await evaluation_plugin_repository.get_with_related(data.evaluation_plugin_pid)
        plugin = evaluation_plugin.plugin_config.plugin
        filter_params["observation__tool"] = str(plugin)
    
    if data.metric_name:
        filter_params["metric__name"] = data.metric_name

    queryset = await measurement_repository.filter_queryset(**filter_params)
    values = await measurement_repository.get_unique_dimension_values(queryset, key)
    return {"key": key, "values": values}

@router.post("/{evaluation_pid}/measurements/metric-names", response=MetricNamesResponse)
async def get_evaluation_metric_names(
    request, 
    evaluation_pid: uuid.UUID, 
    data: MetricNamesRequest
):
    evaluation = await evaluation_repository.get(evaluation_pid)
    filter_params = {"observation__evaluation": evaluation}

    if data.evaluation_plugin_pid:
        evaluation_plugin = await evaluation_plugin_repository.get_with_related(data.evaluation_plugin_pid)
        plugin = evaluation_plugin.plugin_config.plugin
        filter_params["observation__tool"] = str(plugin)
    
    queryset = await measurement_repository.filter_queryset(**filter_params)
    names = await measurement_repository.get_unique_metric_names(queryset)
    return {"names": names}