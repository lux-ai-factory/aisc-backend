import base64
import json
import logging
import os
import uuid

from pathlib import Path
from urllib import error, request as urlrequest
from urllib.parse import quote
from asgiref.sync import sync_to_async
from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend import platform_helpers
from aisc_backend.models import EvaluationPlugin, AIComponent, EvaluationInput
from aisc_backend.models.common import StorageContainer
from aisc_backend.models.observation import Observation
from aisc_backend.models.metric import Metric

from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.ai_component_repository import AIComponentRepository
from aisc_backend.repositories.base_repository import BaseRepository
from aisc_backend.repositories.evaluation_repository import EvaluationRepository
from aisc_backend.repositories.measurement_repository import MeasurementRepository
from aisc_backend.repositories.plugin_repository import EvaluationPluginRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.artifact import ArtifactOutSchema, ArtifactPreviewSchema
from aisc_backend.schemas.evaluation import (
    EvaluationByStatusResponseSchema,
    EvaluationOutSchema,
    ShownEvaluationDetailOutSchema,
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
from aisc_plugin_interface.helpers import HELPER_INPUT_PREFIX

logger = logging.getLogger(__name__)

router = Router(tags=["evaluation"])

evaluation_repository = EvaluationRepository()
project_repository = ProjectRepository()
ai_component_repository = AIComponentRepository()
observation_repository = BaseRepository(model=Observation)
measurement_repository = MeasurementRepository()
metric_repository = BaseRepository(model=Metric)
evaluation_plugin_repository = EvaluationPluginRepository()


class EvaluationPluginInputInSchema(Schema):
    """One input of a run: a component of the AI system (`pid`), or, in the Configurator, for a
    helper model's input (`helper:<role>`), one of the platform's helper models (`helper_ref`)."""
    pid: uuid.UUID | None = None
    name: str
    value: dict | None = None
    helper_ref: str | None = None


class EvaluationPluginInSchema(Schema):
    name: str
    #: the plugin's package: two packages may ship a plugin of the same name (a copy of a test)
    package_name: str | None = None
    inputs: list[EvaluationPluginInputInSchema] | None = None


class CreateEvaluationRequest(Schema):
    project_pid: uuid.UUID
    plugins_to_run: list[EvaluationPluginInSchema]


REACHABLE_TIMEOUT = 3  # seconds


def _get(url: str, headers: dict) -> tuple[int, object]:
    req = urlrequest.Request(url, method="GET", headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=REACHABLE_TIMEOUT) as res:
            return res.status, json.loads(res.read() or b"null")
    except error.HTTPError as exc:
        return exc.code, None


def card_target_connected(platform_pid, key: str) -> bool | None:
    """Whether the card target `key` of the platform project has a connection, as the platform says
    (GET /internal/projects/{pid}/targets/{key}/reachable, with PLATFORM_ENGINE_TOKEN: the answer
    carries no key). None when the engine cannot tell: the evaluation is then accepted and its run
    fails in the worker with the reason, as before."""
    base = (os.environ.get("PLATFORM_URL") or "").rstrip("/")
    token = os.environ.get("PLATFORM_ENGINE_TOKEN") or ""
    if not base or not token:
        logger.warning("PLATFORM_URL or PLATFORM_ENGINE_TOKEN is not set: a card target's connection is not checked")
        return None
    try:
        status, body = _get(f"{base}/internal/projects/{platform_pid}/targets/{quote(key, safe='')}/reachable",
                            {"X-AISC-Service-Token": token})
    except Exception as exc:  # noqa: BLE001 - the platform is down, or the URL is wrong
        logger.warning("the platform can't be reached (%s): a card target's connection is not checked",
                       type(exc).__name__)
        return None
    connected = body.get("connected") if status == 200 and isinstance(body, dict) else None
    if not isinstance(connected, bool):
        logger.warning("the platform answered %s on whether target %s is reachable: its connection is not checked",
                       status, key)
        return None
    return connected


def _reference(component) -> str:
    value = component.json_value
    return str(value.get("value") or "") if isinstance(value, dict) else ""


def check_assessed(plugin, inputs: list[tuple[str, AIComponent]], project) -> str | None:
    """Why the run's inputs assess something the plugin did not declare (@assesses), or None.

    `inputs` are the run's (input name, component) pairs. A card target is the `target` input, a
    mirror of one of this project's card targets (`target:<platform pid>/<key>`), connected when
    the plugin's card access calls it; a local model file is the plugin's local model input; a plugin
    declaring one kind or both is given one of them. An older declaration alone (@system_under_test,
    @dataset_through_target, @assesses_inputs) is a card target, and may still be given a connection
    bound directly (`connection:<pid>/<name>`); a plugin declaring nothing is not checked."""
    kinds = target_kinds_of(plugin)
    if not kinds:
        return None
    adopted = getattr(plugin, "target_kinds", None) is not None  # what @assesses sets
    for _, component in inputs:
        if adopted and _reference(component).startswith("connection:"):
            return f"{component.name} is a connection: what is assessed is a target of the AI card"
    card = next((component for name, component in inputs if name == TARGET), None)
    if card is not None and _reference(card).startswith("connection:"):
        return None  # a connection bound directly, as an older declaration may take it
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
    if adopted and kinds == (CARD,) and card is None:
        return "pick what is assessed: a target of the AI card"
    if adopted and kinds == (LOCAL_MODEL,) and local is None:
        return "pick what is assessed: a local model file"
    return None


FROM_MANAGE = "a helper model is picked from Manage > Helper models, not from the AI system's components"


async def _bound_helpers(plugin_row, inputs, project) -> dict[str, tuple[str, dict]]:
    """The run's helper models, by input name: (reference, value to store), checked before anything is
    stored. In the Configurator a helper model's input (`helper:<role>`) names one of the project's
    helper models on the platform, allowed for that role, and the model picked for the run; it never
    names a component, and no other input names a helper model. Standalone has no platform helper
    models: every input names a component, as before."""
    configurator = _configurator()
    bound = {}
    for input_data in inputs or []:
        name = input_data.name
        if not (configurator and name.startswith(HELPER_INPUT_PREFIX)):
            if input_data.helper_ref is not None:
                reason = ("only a helper model's input names a helper model" if configurator
                          else "this engine has no helper models of the platform: pick a component")
                raise HttpError(400, f"{plugin_row.display_name}: {name}: {reason}")
            if input_data.pid is None:
                raise HttpError(400, f"{plugin_row.display_name}: {name}: pick a component")
            continue
        if input_data.pid is not None:
            raise HttpError(400, f"{plugin_row.display_name}: {name}: {FROM_MANAGE}")
        role, ref = name[len(HELPER_INPUT_PREFIX):], input_data.helper_ref
        if not ref:
            raise HttpError(400, f"{plugin_row.display_name}: {name}: pick one of the project's helper models")
        if project.platform_project_id is None or not ref.startswith(f"helper:{project.platform_project_id}/"):
            raise HttpError(400, f"{plugin_row.display_name}: {ref} is not a helper model of this project")
        model = (input_data.value or {}).get("model")
        if not isinstance(model, str) or not model.strip():
            raise HttpError(400, f"{plugin_row.display_name}: {name}: name the model the helper model runs")
        try:
            helpers = await sync_to_async(platform_helpers.list_helpers, thread_sensitive=False)(
                str(project.platform_project_id), role)
        except platform_helpers.HelpersUnavailable as exc:
            raise HttpError(503, f"{plugin_row.display_name}: {exc.message}")
        helper = next((h for h in helpers if h.get("ref") == ref and role in (h.get("roles") or ())), None)
        if helper is None:
            raise HttpError(400, f"{plugin_row.display_name}: {ref} is not one of this project's helper models"
                                 f" for the role {role}")
        bound[name] = (ref, {"model": model.strip(), "label": helper.get("label") or ""})
    return bound


async def _refuse_what_was_not_declared(plugin_row, plugin, inputs, project) -> None:
    pairs = []
    for input_data in inputs or []:
        component = await AIComponent.objects.filter(pid=input_data.pid).afirst()
        if component is not None:  # an unknown component is refused below, as before
            pairs.append((input_data.name, component))
    # the platform may be asked whether a card target has a connection: not on the event loop
    reason = await sync_to_async(check_assessed, thread_sensitive=False)(plugin, pairs, project)
    if reason:
        raise HttpError(422, f"{plugin_row.display_name}: {reason}")


def _refuse_a_sent_local_marker(plugin_row, inputs) -> None:
    """Only the engine marks a run local (_local_marker here, then the worker): an input's value
    sent with the run never carries the marker, whatever its shape, so a card run is never taken
    for a local one and kept out of the AI card's results."""
    for input_data in inputs or []:
        if isinstance(input_data.value, dict) and "local_model" in input_data.value:
            raise HttpError(422, f"{plugin_row.display_name}: an input's value cannot say it is a local"
                                 " model file; the engine marks local runs")


def _local_marker(plugin, input_name: str, component) -> dict | None:
    """A run on the plugin's local model file is local from its creation: its model input says so
    ({"local_model": {"file"}}) before the worker replaces it with the full marker, with the file's
    hash, so a pending, running or failed local run is never taken for an AI card run. Only a model
    file is marked, as the worker does."""
    if input_name != getattr(plugin, "local_model_input", None) or component.component_type != "model":
        return None
    return {"local_model": {"file": component.name}}


def _plugin_to_run(project, wanted: EvaluationPluginInSchema):
    """The enabled plugin a run names: by package and name, or by name alone when only one plugin has it.
    A name several packages ship, sent without its package, is refused rather than guessed."""
    same_name = [p for p in project.get_enabled_plugins() if p.name == wanted.name]
    if wanted.package_name is not None:
        found = next((p for p in same_name if p.package_name == wanted.package_name), None)
        if found is None:
            raise HttpError(400, f"Plugin {wanted.name} of {wanted.package_name} is not enabled for this project")
        return found
    if len(same_name) > 1:
        packages = ", ".join(sorted({p.package_name for p in same_name}))
        raise HttpError(400, f"Several plugins are named {wanted.name} ({packages}): the run must name the package")
    if not same_name:
        raise HttpError(400, f"Plugin {wanted.name} is not enabled for this project")
    return same_name[0]


@router.post("/task", response=EvaluationOutSchema)
async def create_evaluation_task(request, data: CreateEvaluationRequest):
    try:
        return await _create_evaluation_task(request, data)
    except HttpError as exc:
        # the page shows the reason once; the log keeps it, so a refused launch can be explained afterwards
        logger.warning("launch refused (%s): %s", exc.status_code, exc.message)
        raise


async def _create_evaluation_task(request, data: CreateEvaluationRequest):
    project = await project_repository.get(data.project_pid, True)

    # Resolve and validate plugins before writing anything to DB
    resolved: list[tuple[object, object, dict, list]] = []
    validation_errors = {"missing": [], "invalid": [], "ambiguous": []}
    for plugin_to_run in data.plugins_to_run:
        plugin = _plugin_to_run(project, plugin_to_run)
        if plugin:
            if plugin.current_config is None:
                raise HttpError(400, f"Plugin {plugin.name} has no current config")
            plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)
            _refuse_a_sent_local_marker(plugin, plugin_to_run.inputs)
            helpers = await _bound_helpers(plugin, plugin_to_run.inputs, project)
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
            # every input's component is found before anything is stored, so a refused launch leaves nothing
            inputs = []
            for input_data in plugin_to_run.inputs or []:
                component = None
                if input_data.name not in helpers:
                    component = await ai_component_repository.get(input_data.pid)
                    if not component:
                        raise HttpError(400, f"Component {input_data.pid} not found")
                inputs.append((input_data, component))
            resolved.append((plugin.current_config, plugin_obj, helpers, inputs))

    if any(validation_errors.values()):
        raise HttpError(400, format_validation_error({"message": "Required project settings are missing or invalid", **validation_errors}))

    evaluation = Evaluation(status=EvaluationStatus.Pending, project=project)
    evaluation = await evaluation_repository.create(evaluation)

    evaluation_plugins = []
    for plugin_config, plugin_obj, helpers, inputs in resolved:
        run_plugin = EvaluationPlugin(
            plugin_config=plugin_config, evaluation=evaluation
        )
        run_plugin = await evaluation_plugin_repository.create(run_plugin)

        # Link the input components
        for input_data, component in inputs:
            if input_data.name in helpers:
                helper_ref, value = helpers[input_data.name]
                await EvaluationInput(evaluation_plugin=run_plugin, name=input_data.name, component=None,
                                      helper_ref=helper_ref, value=value).asave()
                continue

            evaluation_input = EvaluationInput(
                evaluation_plugin=run_plugin,
                name=input_data.name,
                component=component,
                value=_local_marker(plugin_obj, input_data.name, component) or input_data.value or {},
            )
            await evaluation_input.asave()

        evaluation_plugins.append(run_plugin)

    evaluation_plugins_task = await celery_service.run_evaluation(evaluation.pid)

    # only the task id: the worker may already have moved the evaluation on from the Pending this copy holds
    evaluation.task = evaluation_plugins_task.task_id
    await evaluation.asave(update_fields=["task"])

    # AUDIT: who ran which evaluation, on which project, with which plugins (the core webapp action)
    await sync_to_async(log_action)(
        request, action="run", resource_type="evaluation", resource_id=str(evaluation.pid),
        metadata={"projectPid": str(data.project_pid),
                  "plugins": [p.name for p in data.plugins_to_run]})
    return evaluation


@router.get("/{evaluation_pid}", response=ShownEvaluationDetailOutSchema)
async def get_evaluation_details(request, evaluation_pid: uuid.UUID, include: str = ""):
    try:
        return await evaluation_repository.get_including(evaluation_pid, include)
    except Evaluation.DoesNotExist:
        raise HttpError(404, "Evaluation not found")


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
