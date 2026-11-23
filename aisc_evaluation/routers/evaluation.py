import base64
import uuid

from pathlib import Path
from asgiref.sync import sync_to_async
from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend.auth.access import role_or_404
from aisc_evaluation.models import EvaluationPlugin, EvaluationInput
from aisc_backend.models import AIComponent
from aisc_backend.models.ai_system import AIComponentType
from aisc_backend.services import components as component_service
from aisc_plugins.models import PluginConfig
from aisc_backend.models.common import StorageContainer
from aisc_evaluation.models import Observation
from aisc_evaluation.models import Metric

from aisc_evaluation.models import Evaluation, EvaluationStatus
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.base_repository import BaseRepository
from aisc_evaluation.repositories.evaluation_repository import EvaluationRepository
from aisc_evaluation.repositories.measurement_repository import MeasurementRepository
from aisc_plugins.repositories.plugin_repository import EvaluationPluginRepository, PluginRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_evaluation.schemas.artifact import ArtifactOutSchema, ArtifactPreviewSchema
from aisc_evaluation.schemas.evaluation import (
    EvaluationDetailOutSchema,
    EvaluationByStatusResponseSchema,
    EvaluationOutSchema,
)
from aisc_evaluation.schemas.measure import MeasurementAggregationResponse, \
    MeasurementAggregationRequest, DimensionKeysResponse, DimensionValuesResponse, \
    DimensionKeysRequest, DimensionValuesRequest, MetricNamesResponse, MetricNamesRequest
from aisc_backend.services import celery_service
from aisc_backend.utils.file_utils import csv_bytes_to_rows, zip_bytes_to_file_list
from aisc_backend.audit.log import log_action
from aisc_plugins.routers.plugin import plugin_loader
from aisc_backend.services.project_config_matching import validate_plugin_settings

router = Router(tags=["evaluation"])

evaluation_repository = EvaluationRepository()
project_repository = ProjectRepository()
plugin_repository = PluginRepository()
ai_component_repository = BaseRepository(model=AIComponent)
observation_repository = BaseRepository(model=Observation)
measurement_repository = MeasurementRepository()
metric_repository = BaseRepository(model=Metric)
evaluation_plugin_repository = EvaluationPluginRepository()


async def _authorized_evaluation(request, evaluation_pid: uuid.UUID) -> Evaluation:
    """The evaluation reached by its own id, gated on the project that owns it:
    a caller who is not a member of that project is told nothing (404)."""
    evaluation = (
        await Evaluation.objects.filter(pid=evaluation_pid)
        .select_related("project")
        .afirst()
    )
    if evaluation is None:
        raise HttpError(404, f"No evaluation found {evaluation_pid}")
    await role_or_404(request, evaluation.project)
    return evaluation


class EvaluationPluginInputInSchema(Schema):
    pid: uuid.UUID
    name: str
    value: dict | None = None
    #: The chosen adapter (a registry id) and its config; defaulted per slot when
    #: omitted (ADR 0009).
    adapter: str | None = None
    adapter_config: dict | None = None


class EvaluationPluginInSchema(Schema):
    name: str
    inputs: list[EvaluationPluginInputInSchema] | None = None
    # Optional config version to run; defaults to the plugin's current (latest) config
    config_id: int | None = None


class CreateEvaluationRequest(Schema):
    project_pid: uuid.UUID
    plugins_to_run: list[EvaluationPluginInSchema]


@router.post("/task", response=EvaluationOutSchema)
async def create_evaluation_task(request, data: CreateEvaluationRequest):
    project = await project_repository.get(data.project_pid, True)
    await role_or_404(request, project)

    # Resolve and validate plugins before writing anything to DB
    resolved: list[tuple[EvaluationPluginInSchema, object, object]] = []
    validation_errors = {"missing": [], "invalid": [], "ambiguous": []}
    for plugin_to_run in data.plugins_to_run:
        plugin = next(
            (p for p in project.get_enabled_plugins() if p.name == plugin_to_run.name),
            None,
        )
        if plugin:
            if plugin.current_config is None and plugin_to_run.config_id is None:
                raise HttpError(400, f"Plugin {plugin.name} has no current config")
            if plugin_to_run.config_id is not None:
                try:
                    plugin_config = await plugin_repository.get_config(plugin, plugin_to_run.config_id)
                except PluginConfig.DoesNotExist:
                    raise HttpError(400, f"Plugin {plugin.name} config {plugin_to_run.config_id} not found")
            else:
                plugin_config = plugin.current_config
            plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)
            selected_settings = [
                mapping.project_config
                async for mapping in plugin_config.setting_mappings.select_related("project_config").all()
            ]
            setting_errors = await validate_plugin_settings(
                project, plugin.name, plugin_obj.project_config_definitions,
                selected_settings,
            )
            for key in validation_errors:
                validation_errors[key].extend(setting_errors[key])
            resolved.append((plugin_to_run, plugin_config, plugin_obj))
        else:
            raise HttpError(400, f"Plugin {plugin_to_run.name} is not enabled for this project")

    if any(validation_errors.values()):
        raise HttpError(400, format_validation_error({"message": "Required project settings are missing or invalid", **validation_errors}))

    evaluation = Evaluation(status=EvaluationStatus.Pending, project=project)
    evaluation = await evaluation_repository.create(evaluation)

    adapters = component_service.installed_adapters()
    evaluation_plugins = []
    for plugin_to_run, plugin_config, plugin_obj in resolved:
        run_plugin = EvaluationPlugin(
            plugin_config=plugin_config, evaluation=evaluation
        )
        run_plugin = await evaluation_plugin_repository.create(run_plugin)

        # Link the input components
        if plugin_to_run.inputs:
            slots = {d.name: d for d in (getattr(plugin_obj, "input_definitions", None) or [])}
            for input_data in plugin_to_run.inputs:
                component = await ai_component_repository.get(input_data.pid)
                if not component:
                    raise HttpError(400, f"Component {input_data.pid} not found")
                # Deny-by-default: a component from another project is not found.
                if component.project_id != project.pk:
                    raise HttpError(400, f"Component {input_data.pid} not found")

                slot = slots.get(input_data.name)
                try:
                    adapter_id, adapter_version, adapter_config = component_service.resolve_input_adapter(
                        slot, component, input_data.adapter, input_data.adapter_config, adapters,
                    )
                except ValueError as exc:
                    raise HttpError(400, str(exc)) from exc

                provenance = {}
                if component.component_type == AIComponentType.ENDPOINT:
                    provenance = component_service.provenance_of(
                        component, adapter_id, adapter_version, adapter_config
                    )

                evaluation_input = EvaluationInput(
                    evaluation_plugin=run_plugin,
                    name=input_data.name,
                    component=component,
                    value=input_data.value or {},
                    adapter=adapter_id,
                    adapter_version=adapter_version,
                    adapter_config=adapter_config,
                    provenance=provenance,
                )
                await evaluation_input.asave()

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
    await _authorized_evaluation(request, evaluation_pid)
    return await evaluation_repository.get_including(evaluation_pid, include)


@router.get("", response=list[EvaluationByStatusResponseSchema])
async def get_evaluations_by_status(request, status: EvaluationStatus):
    return await evaluation_repository.filter(status=status)


@router.get("/{evaluation_pid}/plugins/status", response=dict)
async def check_evaluation_plugins_status(request, evaluation_pid: uuid.UUID):
    """Check if any plugins in the evaluation have failed."""
    await _authorized_evaluation(request, evaluation_pid)
    evaluation = await evaluation_repository.get(evaluation_pid, True)

    has_failed_plugins = await evaluation.evaluation_plugins.filter(status="Failed").aexists()
    total_plugins = await evaluation.evaluation_plugins.acount()

    return {
        "has_failed_plugins": has_failed_plugins,
        "total_plugins": total_plugins,
    }


@router.get("/{evaluation_pid}/artifacts", response=list[ArtifactOutSchema])
async def get_evaluation_artifacts(
    request, evaluation_pid: uuid.UUID, evaluation_plugin_uuid: uuid.UUID
):
    await _authorized_evaluation(request, evaluation_pid)
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
    await _authorized_evaluation(request, evaluation_pid)
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
    await _authorized_evaluation(request, evaluation_pid)
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
    await _authorized_evaluation(request, evaluation_pid)
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
    await _authorized_evaluation(request, evaluation_pid)
    evaluation = await evaluation_repository.get(evaluation_pid)
    filter_params = {"observation__evaluation": evaluation}

    if data.evaluation_plugin_pid:
        evaluation_plugin = await evaluation_plugin_repository.get_with_related(data.evaluation_plugin_pid)
        plugin = evaluation_plugin.plugin_config.plugin
        filter_params["observation__tool"] = str(plugin)
    
    queryset = await measurement_repository.filter_queryset(**filter_params)
    names = await measurement_repository.get_unique_metric_names(queryset)
    return {"names": names}


def format_validation_error(data: dict) -> str:
    parts = [data.get("message", "A validation error occurred")]

    for category in ["missing", "invalid", "ambiguous"]:
        items = data.get(category, [])
        if items:
            details = [f"'{item.get('setting', item.get('key'))}' ({item.get('reason')})" for item in items]
            parts.append(f"{category.title()}: {', '.join(details)}")

    return " - ".join(parts)
