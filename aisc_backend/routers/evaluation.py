import base64
import uuid

from pathlib import Path
from asgiref.sync import sync_to_async
from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend.models import EvaluationPlugin, AIComponent, EvaluationInput
from aisc_backend.models.common import StorageContainer
from aisc_backend.models.observation import Observation
from aisc_backend.models.metric import Metric

from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.base_repository import BaseRepository
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
from aisc_backend.schemas.measure import MeasurementAggregationResponse, \
    MeasurementAggregationRequest, DimensionKeysResponse, DimensionValuesResponse, \
    DimensionKeysRequest, DimensionValuesRequest, MetricNamesResponse, MetricNamesRequest
from aisc_backend.services import celery_service
from aisc_backend.utils.file_utils import csv_bytes_to_rows, zip_bytes_to_file_list
from aisc_backend.audit.log import log_action
from aisc_backend.routers.plugin import plugin_loader
from aisc_backend.routers.target_input import CARD, LOCAL_MODEL, TARGET, _configurator
from aisc_backend.services.project_config_matching import validate_plugin_settings
from aisc_plugin_interface import card_needs_connection, target_kinds_of

router = Router(tags=["evaluation"])

evaluation_repository = EvaluationRepository()
project_repository = ProjectRepository()
ai_component_repository = BaseRepository(model=AIComponent)
observation_repository = BaseRepository(model=Observation)
measurement_repository = MeasurementRepository()
metric_repository = BaseRepository(model=Metric)
evaluation_plugin_repository = EvaluationPluginRepository()


class EvaluationPluginInputInSchema(Schema):
    pid: uuid.UUID
    name: str
    value: dict | None = None


class EvaluationPluginInSchema(Schema):
    name: str
    inputs: list[EvaluationPluginInputInSchema] | None = None


class CreateEvaluationRequest(Schema):
    project_pid: uuid.UUID
    plugins_to_run: list[EvaluationPluginInSchema]


def card_target_connected(platform_pid, key: str) -> bool | None:
    """Whether the card target `key` of the platform project has a connection; None when the engine
    cannot tell. The platform knows (its internal target resolve route answers 404 "no_endpoint"),
    but that route is guarded by the connections token, which the engine does not hold, and it
    answers with the connection's key: so the engine does not ask, and the worker's run finds out."""
    return None


def _reference(component) -> str:
    value = component.json_value
    return str(value.get("value") or "") if isinstance(value, dict) else ""


def check_assessed(plugin, inputs: list[tuple[str, AIComponent]], project) -> str | None:
    """Why the run's inputs assess something the plugin did not declare (@assesses), or None.

    `inputs` are the run's (input name, component) pairs. A card target is the `target` input, a
    mirror of one of this project's card targets (`target:<platform pid>/<key>`), connected when
    the plugin's card access calls it; a local model file is the plugin's local model input; a plugin
    declaring both is given one of them. An older declaration alone (@system_under_test,
    @dataset_through_target, @assesses_inputs) is a card target; a plugin declaring nothing is not checked."""
    kinds = target_kinds_of(plugin)
    if not kinds:
        return None
    for _, component in inputs:
        if _reference(component).startswith("connection:"):
            return f"{component.name} is a connection: what is assessed is a target of the AI card"
    card = next((component for name, component in inputs if name == TARGET), None)
    local_input = getattr(plugin, "local_model_input", None)
    local = next((component for name, component in inputs if local_input and name == local_input), None)
    if card is not None:
        if CARD not in kinds:
            return "it assesses a local model file, not a target of the AI card"
        if card.component_type == "model":
            return "it assesses targets of the AI card, not a local model file"
        own = f"target:{project.platform_project_id}/"
        reference = _reference(card)
        if project.platform_project_id is None or not reference.startswith(own):
            return f"{card.name} is not a target of this project's AI card"
        if card_needs_connection(plugin) and card_target_connected(project.platform_project_id,
                                                                   reference[len(own):]) is False:
            return f"{card.name} has no connection: set one under Manage, Components and connections"
    if kinds == (CARD, LOCAL_MODEL):
        if card is not None and local is not None:
            return "assess either a target of the AI card or a local model file, not both"
        if card is None and local is None:
            return "pick what is assessed: a target of the AI card or a local model file"
    return None


async def _refuse_what_was_not_declared(plugin_row, plugin, inputs, project) -> None:
    pairs = []
    for input_data in inputs or []:
        component = await AIComponent.objects.filter(pid=input_data.pid).afirst()
        if component is not None:  # an unknown component is refused below, as before
            pairs.append((input_data.name, component))
    reason = check_assessed(plugin, pairs, project)
    if reason:
        raise HttpError(422, f"{plugin_row.display_name}: {reason}")


@router.post("/task", response=EvaluationOutSchema)
async def create_evaluation_task(request, data: CreateEvaluationRequest):
    project = await project_repository.get(data.project_pid, True)

    # Resolve and validate plugins before writing anything to DB
    resolved: list[tuple[EvaluationPluginInSchema, object]] = []
    validation_errors = {"missing": [], "invalid": [], "ambiguous": []}
    for plugin_to_run in data.plugins_to_run:
        plugin = next(
            (p for p in project.get_enabled_plugins() if p.name == plugin_to_run.name),
            None,
        )
        if plugin:
            if plugin.current_config is None:
                raise HttpError(400, f"Plugin {plugin.name} has no current config")
            plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)
            if _configurator():
                await _refuse_what_was_not_declared(plugin, plugin_obj, plugin_to_run.inputs, project)
            selected_settings = [
                mapping.project_config
                async for mapping in plugin.current_config.setting_mappings.select_related("project_config").all()
            ]
            setting_errors = await validate_plugin_settings(
                project, plugin.name, plugin_obj.project_config_definitions,
                selected_settings,
            )
            for key in validation_errors:
                validation_errors[key].extend(setting_errors[key])
            resolved.append((plugin_to_run, plugin.current_config))
        else:
            raise HttpError(400, f"Plugin {plugin_to_run.name} is not enabled for this project")

    if any(validation_errors.values()):
        raise HttpError(400, format_validation_error({"message": "Required project settings are missing or invalid", **validation_errors}))

    evaluation = Evaluation(status=EvaluationStatus.Pending, project=project)
    evaluation = await evaluation_repository.create(evaluation)

    evaluation_plugins = []
    for plugin_to_run, plugin_config in resolved:
        run_plugin = EvaluationPlugin(
            plugin_config=plugin_config, evaluation=evaluation
        )
        run_plugin = await evaluation_plugin_repository.create(run_plugin)

        # Link the input components
        if plugin_to_run.inputs:
            for input_data in plugin_to_run.inputs:
                component = await ai_component_repository.get(input_data.pid)
                if not component:
                    raise HttpError(400, f"Component {input_data.pid} not found")

                evaluation_input = EvaluationInput(
                    evaluation_plugin=run_plugin,
                    name=input_data.name,
                    component=component,
                    value=input_data.value or {},
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
    return await evaluation_repository.get_including(evaluation_pid, include)


@router.get("", response=list[EvaluationByStatusResponseSchema])
async def get_evaluations_by_status(request, status: EvaluationStatus):
    return await evaluation_repository.filter(status=status)


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


def format_validation_error(data: dict) -> str:
    parts = [data.get("message", "A validation error occurred")]

    for category in ["missing", "invalid", "ambiguous"]:
        items = data.get(category, [])
        if items:
            details = [f"'{item.get('setting', item.get('key'))}' ({item.get('reason')})" for item in items]
            parts.append(f"{category.title()}: {', '.join(details)}")

    return " - ".join(parts)
