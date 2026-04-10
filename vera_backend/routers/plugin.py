import uuid

from ninja import Router, Schema
from ninja.errors import HttpError

from vera_backend.models.common import StorageContainer
from vera_plugin_interface.models.evaluation_input import InputDefinition
from vera_plugin_manager import Loader
from vera_plugin_interface import MetricVisualization

from vera_backend.repositories import file_repository
from vera_backend.repositories.dataset_repository import DatasetRepository
from vera_backend.repositories.evaluation_repository import EvaluationRepository
from vera_backend.repositories.measurement_repository import MeasurementRepository
from vera_backend.repositories.base_repository import BaseRepository
from vera_backend.repositories.project_repository import ProjectRepository

from vera_backend.models import Plugin, EvaluationPlugin, PluginConfig
from vera_backend.schemas.measure import MeasureOutSchema
from vera_backend.schemas.plugin import (
    PluginOutSchema,
    PluginConfigOutSchema,
)
from config.settings import PLUGIN_PATH

router = Router(tags=["plugin"])

plugin_loader: Loader = Loader(PLUGIN_PATH)

plugin_repository = BaseRepository(model=Plugin)
project_repository = ProjectRepository()
evaluation_repository = EvaluationRepository()
measurement_repository = MeasurementRepository()
dataset_repository = DatasetRepository()


class ProjectPluginConfigStateResponse(Schema):
    plugin_config_id: int | None
    config: dict | None
    formSchema: dict
    uiSchema: dict


@router.get("", response=list[str])
async def get_plugins(request):
    plugins = plugin_loader.list_plugins()
    plugin_names = [p for p in plugins.keys()]
    return plugin_names


@router.get("/{plugin_name}/feature_flags", response=dict)
async def get_plugin_feature_flags(request, plugin_name: str):
    plugin = plugin_loader.load(plugin_name)
    feature_flags = plugin.feature_flags

    return feature_flags.model_dump()


@router.get("/{plugin_name}/display_icon", response=str)
async def get_plugin_display_icon(request, plugin_name: str):
    plugin = plugin_loader.load(plugin_name)
    display_icon = plugin.display_icon

    return display_icon


@router.get("/{plugin_name}/input_definitions", response=list[InputDefinition])
async def get_plugin_input_definitions(request, plugin_name: str):
    plugin = plugin_loader.load(plugin_name)
    input_definitions: list[InputDefinition] = plugin.input_definitions

    return input_definitions


class CreatePluginRequest(Schema):
    name: str
    config: dict | None
    project_uuid: uuid.UUID


@router.post("", response=PluginOutSchema)
async def create_plugin(request, data: CreatePluginRequest):
    project = await project_repository.get(data.project_uuid, True)
    plugin_obj = plugin_loader.load(data.name)

    if data.config:
        # raises a validation error based on customizable validation rules
        plugin_obj.validate_config_form_data(data.config)

    project_plugin = next(
        (p for p in project.get_enabled_plugins() if p.name == data.name), None
    )
    if not project_plugin:
        project_plugin = Plugin(name=data.name, project=project)
        project_plugin = await plugin_repository.create(project_plugin)

    if data.config:
        plugin_config = PluginConfig(plugin=project_plugin, config=data.config)
        await plugin_config.asave()
        project_plugin.current_config = plugin_config
        project_plugin = await plugin_repository.save(project_plugin)

    return project_plugin


@router.get(
    "/{plugin_name}/project/{project_uuid}/configs",
    response=list[PluginConfigOutSchema],
)
async def get_plugin_config_history(request, plugin_name: str, project_uuid: uuid.UUID):
    project = await project_repository.get(project_uuid)
    plugin = await plugin_repository.get_one(name=plugin_name, project=project)
    return [c async for c in plugin.configs.all()]


@router.post(
    "/{plugin_name}/project/{project_uuid}/configs/{config_id}/restore",
    response=PluginOutSchema,
)
async def restore_plugin_config(
    request, plugin_name: str, project_uuid: uuid.UUID, config_id: int
):
    project = await project_repository.get(project_uuid)
    plugin = await plugin_repository.get_one(name=plugin_name, project=project)
    config = await PluginConfig.objects.aget(id=config_id, plugin=plugin)

    plugin.current_config = config
    await plugin.asave()

    return plugin


class UpdatePluginConfigRequest(Schema):
    config: dict


@router.post("/{plugin_name}/config/update", response=ProjectPluginConfigStateResponse)
async def update_plugin_config_state(
    request, plugin_name: str, data: UpdatePluginConfigRequest
):
    plugin = plugin_loader.load(plugin_name)

    config, schema, ui_schema = plugin.on_config_change(data.config)

    response = ProjectPluginConfigStateResponse(
        plugin_config_id=None, config=config, formSchema=schema, uiSchema=ui_schema
    )

    return response


@router.delete("/{plugin_pid}", response={204: None})
async def delete_plugin(request, plugin_pid):
    plugin = await plugin_repository.get(pid=plugin_pid)
    await plugin_repository.delete(plugin)
    return 204, None


class EvaluationResultOutSchema(Schema):
    measurements: list[MeasureOutSchema]
    metric_visualizations: list[MetricVisualization]


@router.get(
    "/{plugin_name}/evaluations/{evaluation_uuid}/result",
    response=EvaluationResultOutSchema,
)
async def get_plugin_evaluation_results(
    request, plugin_name: str, evaluation_uuid: uuid.UUID
):
    plugin = plugin_loader.load(plugin_name)
    metrics = plugin.get_metrics()

    evaluation = await evaluation_repository.get(evaluation_uuid)
    observation = (
        await evaluation.observations.filter(tool=plugin_name)
        .order_by("-whenObserved")
        .afirst()
    )

    measurements = await measurement_repository.filter_with_related(
        name__in=metrics, observation=observation
    )

    eval_plugin = await EvaluationPlugin.objects.select_related("plugin_config").aget(
        evaluation=evaluation, plugin_config__plugin__name=plugin_name
    )
    metric_visualizations = plugin.get_metric_visualizations(
        eval_plugin.plugin_config.config
    )

    return EvaluationResultOutSchema(
        measurements=measurements, metric_visualizations=metric_visualizations
    )


@router.get(
    "/{plugin_name}/project/{project_uuid}/config/state",
    response=ProjectPluginConfigStateResponse,
)
async def get_project_plugin_config_state(
    request, plugin_name: str, project_uuid: uuid.UUID
):
    project = await project_repository.get(project_uuid, True)

    project_plugin = next(
        (p for p in project.get_enabled_plugins() if p.name == plugin_name), None
    )
    if not project_plugin:
        raise HttpError(404, f"Project {project_uuid} has no plugin {plugin_name}")

    plugin = plugin_loader.load(plugin_name)
    plugin_config = None
    plugin_config_id = None
    if project_plugin.config_set():
        plugin_config = project_plugin.current_config.config
        plugin_config_id = project_plugin.current_config.id
    config, schema, ui_schema = plugin.on_config_change(plugin_config)

    response = ProjectPluginConfigStateResponse(
        plugin_config_id=plugin_config_id,
        config=config,
        formSchema=schema,
        uiSchema=ui_schema,
    )

    return response


@router.get(
    "/{plugin_name}/parse_dataset/{dataset_uuid}/config/state",
    response=ProjectPluginConfigStateResponse,
)
async def parse_plugin_config_state_from_dataset(
    request, plugin_name: str, dataset_uuid: uuid.UUID
):
    dataset = await dataset_repository.get(dataset_uuid)

    response = file_repository.get_object(
        bucket_name=StorageContainer.Datasets, object_name=dataset.data
    )
    file_content = response["Body"].read()

    plugin = plugin_loader.load(plugin_name)

    config = plugin.parse_config_from_dataset(file_content)

    config, schema, ui_schema = plugin.on_config_change(config)

    response = ProjectPluginConfigStateResponse(
        plugin_config_id=None, config=config, formSchema=schema, uiSchema=ui_schema
    )

    return response
