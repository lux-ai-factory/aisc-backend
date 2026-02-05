import uuid

from ninja import Router, Schema
from ninja.errors import HttpError

from a4s_plugin_manager import Loader
from a4s_plugin_interface import MetricVisualization

from a4s_backend.repositories import file_repository
from a4s_backend.repositories.dataset_repository import DatasetRepository
from a4s_backend.repositories.evaluation_repository import EvaluationRepository
from a4s_backend.repositories.measurement_repository import MeasurementRepository
from a4s_backend.repositories.base_repository import BaseRepository
from a4s_backend.repositories.project_repository import ProjectRepository

from a4s_backend.models import Plugin, EvaluationPlugin
from a4s_backend.schemas.measure import MeasureOutSchema
from a4s_backend.schemas.plugin import PluginOutSchema
from config.settings import PLUGIN_PATH, S3_DATASETS_BUCKET

router = Router(tags=["plugin"])

plugin_loader: Loader = Loader(PLUGIN_PATH)

plugin_repository = BaseRepository(model=Plugin)
project_repository = ProjectRepository()
evaluation_repository = EvaluationRepository()
measurement_repository = MeasurementRepository()
dataset_repository = DatasetRepository()


class ProjectPluginConfigStateResponse(Schema):
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


class CreatePluginRequest(Schema):
    name: str
    config: dict | None
    project_uuid: uuid.UUID


@router.post("", response=PluginOutSchema)
async def create_plugin(request, data: CreatePluginRequest):
    project = await project_repository.get(data.project_uuid, True)
    plugin = plugin_loader.load(data.name)

    if data.config:
        # raises a validation error based on customizable validation rules
        plugin.validate_config_form_data(data.config)

    project_plugin = next(
        (p for p in project.get_enabled_plugins() if p.name == data.name), None
    )
    if not project_plugin:
        project_plugin = Plugin(name=data.name, config=data.config, project=project)
        project_plugin = await plugin_repository.create(project_plugin)
    else:
        project_plugin.config = data.config
        project_plugin = await plugin_repository.save(project_plugin)

    return project_plugin


class UpdatePluginConfigRequest(Schema):
    config: dict


@router.post("/{plugin_name}/config/update", response=ProjectPluginConfigStateResponse)
async def update_plugin_config_state(
    request, plugin_name: str, data: UpdatePluginConfigRequest
):
    plugin = plugin_loader.load(plugin_name)

    config, schema, ui_schema = plugin.on_config_change(data.config)

    response = ProjectPluginConfigStateResponse(
        config=config, formSchema=schema, uiSchema=ui_schema
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
    observation = await evaluation.observations.order_by("-whenObserved").afirst()

    measurements = await measurement_repository.filter_with_related(
        name__in=metrics, observation=observation
    )

    eval_plugin = await EvaluationPlugin.objects.select_related("plugin").aget(
        evaluation=evaluation, plugin__name=plugin_name
    )
    metric_visualizations = plugin.get_metric_visualizations(
        eval_plugin.evaluation_config
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
    config, schema, ui_schema = plugin.on_config_change(project_plugin.config)

    response = ProjectPluginConfigStateResponse(
        config=config, formSchema=schema, uiSchema=ui_schema
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
        bucket_name=S3_DATASETS_BUCKET, object_name=dataset.data
    )
    file_content = response["Body"].read()

    plugin = plugin_loader.load(plugin_name)
    plugin.set_dataset_input_provider(file_content)

    config = plugin.parse_config_from_dataset()

    config, schema, ui_schema = plugin.on_config_change(config)

    response = ProjectPluginConfigStateResponse(
        config=config, formSchema=schema, uiSchema=ui_schema
    )

    return response
