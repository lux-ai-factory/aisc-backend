import uuid

from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend.models.common import StorageContainer
from aisc_plugin_interface.models.evaluation_input import InputDefinition

from aisc_backend.repositories.plugin_repository import PluginRepository, EvaluationPluginRepository
from aisc_backend.audit.log import log_action
from asgiref.sync import sync_to_async
from aisc_plugin_manager import Loader
from aisc_plugin_interface import MetricVisualization

from aisc_backend.repositories import file_repository
from aisc_backend.repositories.dataset_repository import DatasetRepository
from aisc_backend.repositories.evaluation_repository import EvaluationRepository
from aisc_backend.repositories.measurement_repository import MeasurementRepository
from aisc_backend.repositories.base_repository import BaseRepository
from aisc_backend.repositories.project_repository import ProjectRepository

from aisc_backend.models import Plugin, EvaluationPlugin, PluginConfig
from aisc_backend.schemas.measure import MeasureOutSchema
from aisc_backend.schemas.plugin import (
    PluginOutSchema,
    PluginConfigOutSchema,
)
from config.settings import PLUGIN_PATH, PACKAGE_REGISTRY_URL, PACKAGE_REGISTRY_INDEX, PACKAGE_REGISTRY_USER, \
    PACKAGE_REGISTRY_PASSWORD

router = Router(tags=["plugin"])

plugin_loader: Loader = Loader(PLUGIN_PATH, PACKAGE_REGISTRY_URL, PACKAGE_REGISTRY_INDEX, PACKAGE_REGISTRY_USER,
                               PACKAGE_REGISTRY_PASSWORD)

plugin_repository = PluginRepository()
project_repository = ProjectRepository()
evaluation_repository = EvaluationRepository()
measurement_repository = MeasurementRepository()
dataset_repository = DatasetRepository()
evaluation_plugin_repository = EvaluationPluginRepository()


class PluginConfigStateResponse(Schema):
    plugin_config_id: int | None
    config: dict | None
    formSchema: dict
    uiSchema: dict


class PackageAvailableSchema(Schema):
    package_name: str
    version: str
    source: str


@router.get("", response=list[PackageAvailableSchema])
async def get_plugins(request):
    packages_dict = plugin_loader.list_packages(refresh=True)

    available_packages = []
    for pkg_name, versions_dict in packages_dict.items():
        for version, meta in versions_dict.items():
            available_packages.append(PackageAvailableSchema(
                package_name=pkg_name,
                version=version,
                source=meta.get("source", "unknown")
            ))

    return available_packages


@router.get("/{plugin_pid}/feature_flags", response=dict)
async def get_plugin_feature_flags(request, plugin_pid: uuid.UUID):
    plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)

    return plugin_obj.feature_flags.model_dump()


@router.get("/{plugin_pid}/display_icon", response=str)
async def get_plugin_display_icon(request, plugin_pid: uuid.UUID):
    plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)

    return plugin_obj.display_icon


@router.get("/{plugin_pid}/input_definitions", response=list[InputDefinition])
async def get_plugin_input_definitions(request, plugin_pid: uuid.UUID):
    plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)
    input_definitions: list[InputDefinition] = plugin_obj.input_definitions

    return input_definitions


class CreatePluginsRequest(Schema):
    package_name: str
    version: str
    project_uuid: uuid.UUID

@router.post("", response=list[PluginOutSchema])
async def create_plugins(request, data: CreatePluginsRequest):
    from asgiref.sync import sync_to_async

    project = await project_repository.get(data.project_uuid, True)

    plugins_package_dict = plugin_loader.load_package(data.package_name, data.version)
    created_plugins = []

    for plugin_name, plugin_obj in plugins_package_dict.items():
        # Look up ANY Plugin row for this (package, version, name, project) —
        # including soft-disabled ones — so the toggle re-uses the existing
        # row and the historical eval data stays attached.
        existing = await sync_to_async(
            lambda: Plugin.objects.filter(
                project=project,
                package_name=data.package_name,
                version=data.version,
                name=plugin_name,
            ).first()
        )()

        if existing is not None:
            if not existing.enabled:
                existing.enabled = True
                await sync_to_async(existing.save)()
            project_plugin = existing
        else:
            project_plugin = Plugin(
                name=plugin_name,
                display_name=plugin_obj.display_name,
                package_name=data.package_name,
                version=data.version,
                project=project,
                enabled=True,
            )
            project_plugin = await plugin_repository.create(project_plugin)

        created_plugins.append(project_plugin)

    await sync_to_async(log_action)(
        request, action="install", resource_type="plugin", resource_id=data.package_name,
        metadata={"version": data.version, "projectPid": str(data.project_uuid),
                  "plugins": [p.name for p in created_plugins]})
    return created_plugins

class DeletePluginsRequest(Schema):
    package_name: str
    version: str
    project_uuid: uuid.UUID

@router.delete("", response={204: None})
async def delete_plugin(request, data: DeletePluginsRequest):
    from asgiref.sync import sync_to_async

    # Soft-disable instead of deleting. Keeps the Plugin row + every
    # downstream link (PluginConfig, EvaluationPlugin, Artifact) intact so
    # the existing evaluation history remains visible. Re-toggling the
    # plugin on flips `enabled` back to True without losing any data.
    plugins = await plugin_repository.filter(
        package_name=data.package_name,
        version=data.version,
        project__pid=data.project_uuid,
    )
    for plugin in plugins:
        if plugin.enabled:
            plugin.enabled = False
            await sync_to_async(plugin.save)()
    await sync_to_async(log_action)(
        request, action="disable", resource_type="plugin", resource_id=data.package_name,
        metadata={"version": data.version, "projectPid": str(data.project_uuid)})
    return 204, None


class UpdatePluginEnabledRequest(Schema):
    enabled: bool

@router.patch("/{plugin_pid}/enabled", response=PluginOutSchema)
async def update_plugin_enabled(
        request, plugin_pid: uuid.UUID, data: UpdatePluginEnabledRequest
):
    plugin = await plugin_repository.get(plugin_pid)
    plugin.enabled = data.enabled
    await plugin_repository.save(plugin)
    await sync_to_async(log_action)(
        request, action="toggle", resource_type="plugin",
        resource_id=str(plugin_pid), metadata={"enabled": data.enabled})
    return plugin


@router.get(
    "/{plugin_pid}/configs",
    response=list[PluginConfigOutSchema],
)
async def get_plugin_config_history(request, plugin_pid: uuid.UUID):
    plugin = await plugin_repository.get(plugin_pid)
    return [c async for c in plugin.configs.all()]


@router.post(
    "/{plugin_pid}/configs/{config_id}/restore",
    response=PluginOutSchema,
)
async def restore_plugin_config(
        request, plugin_pid: uuid.UUID, config_id: int
):
    plugin = await plugin_repository.get(plugin_pid)
    config = await PluginConfig.objects.aget(id=config_id, plugin=plugin)

    plugin.current_config = config
    await plugin.asave()

    return plugin


class UpdatePluginConfigRequest(Schema):
    config: dict

@router.post("/{plugin_pid}/config", response=PluginConfigStateResponse)
async def update_plugin_config_state(
        request, plugin_pid: uuid.UUID, data: UpdatePluginConfigRequest
):
    project_plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(project_plugin.package_name, project_plugin.name, project_plugin.version)

    if not data.config:
        raise HttpError(400, "Config is required")

    plugin_config = PluginConfig(plugin=project_plugin, config=data.config)
    await plugin_config.asave()
    project_plugin.current_config = plugin_config
    await plugin_repository.save(project_plugin)

    config, schema, ui_schema = plugin_obj.on_config_change(plugin_config.config)

    response = PluginConfigStateResponse(
        plugin_config_id=plugin_config.id,
        config=config,
        formSchema=schema,
        uiSchema=ui_schema,
    )

    await sync_to_async(log_action)(
        request, action="configure", resource_type="plugin",
        resource_id=str(plugin_pid), metadata={"configId": plugin_config.id})
    return response


@router.post("/{plugin_pid}/config/state", response=PluginConfigStateResponse)
async def update_plugin_config_state(
        request, plugin_pid: uuid.UUID, data: UpdatePluginConfigRequest
):
    project_plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(project_plugin.package_name, project_plugin.name, project_plugin.version)

    config, schema, ui_schema = plugin_obj.on_config_change(data.config)

    response = PluginConfigStateResponse(
        plugin_config_id=None, config=config, formSchema=schema, uiSchema=ui_schema
    )

    return response


class EvaluationResultOutSchema(Schema):
    measurements: list[MeasureOutSchema]
    metric_visualizations: list[MetricVisualization]


@router.get(
    "/{evaluation_plugin_pid}/evaluations/{evaluation_uuid}/result",
    response=EvaluationResultOutSchema,
)
async def get_plugin_evaluation_results(
        request, evaluation_plugin_pid: uuid.UUID, evaluation_uuid: uuid.UUID
):
    evaluation_plugin = await evaluation_plugin_repository.get_with_related(evaluation_plugin_pid)
    plugin = evaluation_plugin.plugin_config.plugin
    plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)
    metrics = plugin_obj.get_metrics()

    evaluation = await evaluation_repository.get(evaluation_uuid)
    observation = (
        await evaluation.observations.filter(tool=str(plugin))
        .order_by("-created_at")
        .afirst()
    )

    measurements = await measurement_repository.filter(name__in=metrics, observation=observation)

    metric_visualizations = plugin_obj.get_metric_visualizations(
        evaluation_plugin.plugin_config.config
    )

    return EvaluationResultOutSchema(
        measurements=measurements, metric_visualizations=metric_visualizations
    )


@router.get(
    "/{plugin_pid}/config/state",
    response=PluginConfigStateResponse,
)
async def get_project_plugin_config_state(
        request, plugin_pid: uuid.UUID
):
    project_plugin = await plugin_repository.get_with_related(plugin_pid)
    if not project_plugin:
        raise HttpError(
            404, f"Plugin {plugin_pid} not found"
        )

    plugin_obj = plugin_loader.load_plugin(project_plugin.package_name, project_plugin.name, project_plugin.version)

    plugin_config = None
    plugin_config_id = None
    if project_plugin.config_set():
        plugin_config = project_plugin.current_config.config
        plugin_config_id = project_plugin.current_config.id

    config, schema, ui_schema = plugin_obj.on_config_change(plugin_config)

    response = PluginConfigStateResponse(
        plugin_config_id=plugin_config_id,
        config=config,
        formSchema=schema,
        uiSchema=ui_schema,
    )

    return response


@router.get(
    "/{plugin_pid}/parse_dataset/{dataset_uuid}/config/state",
    response=PluginConfigStateResponse,
)
async def parse_plugin_config_state_from_dataset(
        request, plugin_pid: uuid.UUID, dataset_uuid: uuid.UUID
):
    dataset = await dataset_repository.get(dataset_uuid)

    response = file_repository.get_object(
        bucket_name=StorageContainer.Datasets, object_name=dataset.data
    )
    file_content = response["Body"].read()

    plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)

    config = plugin_obj.parse_config_from_dataset(file_content)

    config, schema, ui_schema = plugin_obj.on_config_change(config)

    response = PluginConfigStateResponse(
        plugin_config_id=None, config=config, formSchema=schema, uiSchema=ui_schema
    )

    return response