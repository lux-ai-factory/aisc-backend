import uuid

from a4s_backend.repositories.evaluation_repository import EvaluationRepository
from a4s_backend.repositories.measurement_repository import MeasurementRepository
from a4s_backend.schemas.measure import MeasureOutSchema
from a4s_backend.schemas.plugin import PluginOutSchema
from a4s_plugin_manager import Loader
from ninja import Router, Schema

from a4s_backend.models import Plugin
from a4s_backend.repositories.base_repository import BaseRepository
from a4s_backend.repositories.project_repository import ProjectRepository
from config.settings import PLUGIN_PATH

router = Router(tags=["plugin"])

plugin_loader: Loader = Loader(PLUGIN_PATH)

plugin_repository = BaseRepository(model=Plugin)
project_repository = ProjectRepository()
evaluation_repository = EvaluationRepository()
measurement_repository = MeasurementRepository()


@router.get("", response=list[str])
async def get_plugins(request):
    plugins = plugin_loader.list_plugins()
    plugin_names = [p for p in plugins.keys()]
    return plugin_names


@router.get("{plugin_name}/config_schema", response=dict)
async def get_plugin_config_schema(request, plugin_name: str):
    plugin = plugin_loader.load(plugin_name)
    return plugin.get_config_form_schema()


@router.get("{plugin_name}/config_ui_schema", response=dict)
async def get_plugin_config_ui_schema(request, plugin_name: str):
    plugin = plugin_loader.load(plugin_name)
    return plugin.get_config_form_ui_schema()


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

    project_plugin = next((p for p in project.get_enabled_plugins() if p.name == data.name), None)
    if not project_plugin:
        project_plugin = Plugin(name=data.name, config=data.config, project=project)
        project_plugin = await plugin_repository.create(project_plugin)
    else:
        project_plugin.config = data.config
        project_plugin = await plugin_repository.save(project_plugin)

    return project_plugin


@router.delete("/{plugin_pid}", response={204: None})
async def delete_plugin(request, plugin_pid):
    plugin = await plugin_repository.get(pid=plugin_pid)
    await plugin_repository.delete(plugin)
    return 204, None


@router.get("/{plugin_name}/evaluations/{evaluation_uuid}/result", response=list[MeasureOutSchema])
async def get_plugin_evaluation_results(request, plugin_name: str, evaluation_uuid: uuid.UUID):
    plugin = plugin_loader.load(plugin_name)
    metrics = plugin.get_metrics()

    evaluation = await evaluation_repository.get(evaluation_uuid)
    observation = await evaluation.observations.order_by("-whenObserved").afirst()

    measurements = await measurement_repository.filter_with_related(name__in=metrics, observation=observation)

    return measurements
