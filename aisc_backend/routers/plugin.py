import uuid

from asgiref.sync import sync_to_async
from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend import deployment
from aisc_backend.audit.log import log_action
# In the Configurator every route that reaches one plugin asks whether the caller is in its
# project, and the three that install, reinstall or remove code take admin. Standalone never asks.
from aisc_backend.auth import membership
from aisc_backend.auth.keycloak import admin_in_configurator
from aisc_backend.models import Plugin, PluginConfig, ProjectConfig
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.ai_component_repository import AIComponentRepository
from aisc_backend.repositories.base_repository import BaseRepository
from aisc_backend.repositories.evaluation_repository import EvaluationRepository
from aisc_backend.repositories.measurement_repository import MeasurementRepository
from aisc_backend.repositories.plugin_repository import PluginRepository, EvaluationPluginRepository
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.measure import MeasureOutSchema
from aisc_backend.schemas.plugin import PluginOutSchema, PluginConfigOutSchema
from aisc_backend.schemas.project_config import ProjectConfigOptionSchema, ProjectConfigSelectionSchema
from aisc_plugin_manager import Loader
from aisc_plugin_interface import MetricVisualization
from aisc_plugin_interface.models.evaluation_input import InputDefinition
from config.settings import PLUGIN_PATH, PACKAGE_REGISTRY_URL, PACKAGE_REGISTRY_INDEX, PACKAGE_REGISTRY_USER, \
    PACKAGE_REGISTRY_PASSWORD

router = Router(tags=["plugin"])

plugin_loader: Loader = Loader(PLUGIN_PATH, PACKAGE_REGISTRY_URL, PACKAGE_REGISTRY_INDEX, PACKAGE_REGISTRY_USER,
                               PACKAGE_REGISTRY_PASSWORD)

plugin_repository = PluginRepository()
project_repository = ProjectRepository()
evaluation_repository = EvaluationRepository()
measurement_repository = MeasurementRepository()
evaluation_plugin_repository = EvaluationPluginRepository()
ai_component_repository = AIComponentRepository()
project_config_repository = ProjectConfigRepository()
plugin_config_repository = BaseRepository(model=PluginConfig)


class PluginConfigStateResponse(Schema):
    plugin_config_id: int | None
    config: dict | None
    formSchema: dict
    uiSchema: dict
    project_config_definitions: list[dict]
    project_config_selections: list[ProjectConfigSelectionSchema] = []
    project_configs: list[ProjectConfigOptionSchema] = []
    description: str = ""


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
    if deployment.is_configurator():
        await sync_to_async(membership.for_plugin)(request, plugin_pid)
    plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)

    return plugin_obj.feature_flags.model_dump()


@router.get("/{plugin_pid}/display_icon", response=str)
async def get_plugin_display_icon(request, plugin_pid: uuid.UUID):
    if deployment.is_configurator():
        await sync_to_async(membership.for_plugin)(request, plugin_pid)
    plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)

    return plugin_obj.display_icon


@router.get("/{plugin_pid}/input_definitions", response=list[InputDefinition])
async def get_plugin_input_definitions(request, plugin_pid: uuid.UUID):
    if deployment.is_configurator():
        await sync_to_async(membership.for_plugin)(request, plugin_pid)
    plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)
    input_definitions: list[InputDefinition] = plugin_obj.input_definitions

    return input_definitions


@router.get("/{plugin_pid}/project_config_definitions", response=list[dict])
async def get_plugin_project_config_definitions(request, plugin_pid: uuid.UUID):
    if deployment.is_configurator():
        await sync_to_async(membership.for_plugin)(request, plugin_pid)
    plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)
    return [definition.model_dump(mode="json") for definition in plugin_obj.project_config_definitions]


class CreatePluginsRequest(Schema):
    package_name: str
    version: str
    project_uuid: uuid.UUID
    # Set when the install came from the catalogue (both modes). Optional so a deep link or a
    # direct API call still works; it is then recorded as no origin at all.
    catalogue_slug: str | None = None


@router.post("", response=list[PluginOutSchema], auth=admin_in_configurator())
async def create_plugins(request, data: CreatePluginsRequest):
    project = await project_repository.get(data.project_uuid, True)

    plugins_package_dict = plugin_loader.load_package(data.package_name, data.version)
    created_plugins = []

    for plugin_name, plugin_obj in plugins_package_dict.items():
        # Look up ANY Plugin row for this (package, version, name, project) —
        # including soft-disabled ones — so the toggle re-uses the existing
        # row and the historical eval data stays attached.
        existing = await plugin_repository.find_by_identity(
            project, data.package_name, data.version, plugin_name
        )

        if existing is not None:
            project_plugin = existing
            # An older row may not know where it came from. Learn it, but never
            # unlearn it: an install without an origin says nothing about the
            # origin already recorded.
            if data.catalogue_slug and not project_plugin.catalogue_slug:
                project_plugin.catalogue_slug = data.catalogue_slug
                await plugin_repository.save(project_plugin)
        else:
            project_plugin = Plugin(
                name=plugin_name,
                display_name=plugin_obj.display_name,
                package_name=data.package_name,
                version=data.version,
                catalogue_slug=data.catalogue_slug,
                project=project,
                enabled=True,
            )
            project_plugin = await plugin_repository.create(project_plugin)

        created_plugins.append(project_plugin)

    if created_plugins and all(not p.enabled for p in created_plugins):
        for p in created_plugins:
            p.enabled = True
            await plugin_repository.save(p)

    await sync_to_async(log_action)(
        request, action="install", resource_type="plugin", resource_id=data.package_name,
        metadata={"version": data.version, "projectPid": str(data.project_uuid),
                  "catalogueSlug": data.catalogue_slug,
                  "plugins": [p.name for p in created_plugins]})

    return created_plugins


class RefreshPluginRequest(Schema):
    package_name: str
    version: str
    project_uuid: uuid.UUID


@router.post("/refresh", response=list[PluginOutSchema], auth=admin_in_configurator())
async def refresh_plugins(request, data: RefreshPluginRequest):
    plugins_package_dict = plugin_loader.refresh_package(data.package_name, data.version)

    project = await project_repository.get(data.project_uuid, True)

    existing = await plugin_repository.list_by_package(
        data.project_uuid, data.package_name, data.version
    )
    existing_by_name = {p.name: p for p in existing}

    result = []
    for plugin_name, plugin_obj in plugins_package_dict.items():
        if plugin_name in existing_by_name:
            project_plugin = existing_by_name[plugin_name]
            project_plugin.display_name = plugin_obj.display_name
            await plugin_repository.save(project_plugin)
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

        result.append(project_plugin)

    return result


class DeletePluginsRequest(Schema):
    package_name: str
    version: str
    project_uuid: uuid.UUID


@router.delete("", response={204: None}, auth=admin_in_configurator())
async def delete_plugin(request, data: DeletePluginsRequest):
    # Soft-disable instead of deleting. Keeps the Plugin row + every
    # downstream link (PluginConfig, EvaluationPlugin, Artifact) intact so
    # the existing evaluation history remains visible. Re-toggling the
    # plugin on flips `enabled` back to True without losing any data.
    plugins = await plugin_repository.list_by_package(
        data.project_uuid, data.package_name, data.version
    )
    for plugin in plugins:
        if plugin.enabled:
            plugin.enabled = False
            await plugin_repository.save(plugin)
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
    if deployment.is_configurator():
        await sync_to_async(membership.for_plugin)(request, plugin_pid, "editor")
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
    if deployment.is_configurator():
        await sync_to_async(membership.for_plugin)(request, plugin_pid)
    plugin = await plugin_repository.get(plugin_pid)
    return await plugin_repository.configs_with_mappings(plugin)


@router.post(
    "/{plugin_pid}/configs/{config_id}/restore",
    response=PluginOutSchema,
)
async def restore_plugin_config(
        request, plugin_pid: uuid.UUID, config_id: int
):
    if deployment.is_configurator():
        await sync_to_async(membership.for_plugin)(request, plugin_pid, "editor")
    plugin = await plugin_repository.get(plugin_pid)
    try:
        config = await plugin_repository.get_config(plugin, config_id)
    except PluginConfig.DoesNotExist:
        raise HttpError(404, f"Plugin config {config_id} not found")

    plugin.current_config = config
    await plugin_repository.save(plugin)

    return plugin


class UpdatePluginConfigRequest(Schema):
    config: dict
    project_config_selections: list[ProjectConfigSelectionSchema] = []


@router.post("/{plugin_pid}/config", response=PluginConfigStateResponse)
async def update_plugin_config_state(
        request, plugin_pid: uuid.UUID, data: UpdatePluginConfigRequest
):
    if deployment.is_configurator():
        await sync_to_async(membership.for_plugin)(request, plugin_pid, "editor")
    project_plugin = await plugin_repository.get_with_related(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(project_plugin.package_name, project_plugin.name, project_plugin.version)

    if not data.config:
        raise HttpError(400, "Config is required")

    plugin_config = PluginConfig(plugin=project_plugin, config=data.config)
    plugin_config = await plugin_config_repository.save(plugin_config)

    try:
        await project_config_repository.save_plugin_config_mappings(
            plugin_config, project_plugin.project.pid, data.project_config_selections
        )
    except ProjectConfig.DoesNotExist:
        raise HttpError(400, "Selected project config does not belong to this project")
    project_plugin.current_config = plugin_config
    await plugin_repository.save(project_plugin)

    config, schema, ui_schema = plugin_obj.on_config_change(plugin_config.config)

    response = PluginConfigStateResponse(
        plugin_config_id=plugin_config.id,
        config=config,
        formSchema=schema,
        uiSchema=ui_schema,
        project_config_definitions=[definition.model_dump(mode="json") for definition in plugin_obj.project_config_definitions],
        project_config_selections=data.project_config_selections,
        project_configs=[
            ProjectConfigOptionSchema.from_row(project_config)
            for project_config in await project_config_repository.get_by_project(project_plugin.project.pid)
        ],
        description=plugin_obj.help_text,
    )

    await sync_to_async(log_action)(
        request, action="configure", resource_type="plugin",
        resource_id=str(plugin_pid), metadata={"configId": plugin_config.id})
    return response


@router.post("/{plugin_pid}/config/state", response=PluginConfigStateResponse)
async def preview_plugin_config_state(
        request, plugin_pid: uuid.UUID, data: UpdatePluginConfigRequest
):
    if deployment.is_configurator():
        await sync_to_async(membership.for_plugin)(request, plugin_pid)
    project_plugin = await plugin_repository.get_with_related(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(project_plugin.package_name, project_plugin.name, project_plugin.version)

    config, schema, ui_schema = plugin_obj.on_config_change(data.config)

    response = PluginConfigStateResponse(
        plugin_config_id=None, config=config, formSchema=schema, uiSchema=ui_schema,
        project_config_definitions=[definition.model_dump(mode="json") for definition in plugin_obj.project_config_definitions],
        project_config_selections=data.project_config_selections,
        project_configs=[
            ProjectConfigOptionSchema.from_row(project_config)
            for project_config in await project_config_repository.get_by_project(project_plugin.project.pid)
        ],
        description=plugin_obj.help_text,
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
    observation = await evaluation_repository.get_latest_observation(evaluation, str(plugin))

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
    if deployment.is_configurator():
        await sync_to_async(membership.for_plugin)(request, plugin_pid)
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

    current_setting_selections = []
    if project_plugin.current_config:
        mappings = await plugin_repository.get_setting_mappings(project_plugin.current_config)
        current_setting_selections = [
            ProjectConfigSelectionSchema(
                plugin_config_key=mapping.plugin_config_key,
                project_config_pid=mapping.project_config.pid,
            )
            for mapping in mappings
        ]

    response = PluginConfigStateResponse(
        plugin_config_id=plugin_config_id,
        config=config,
        formSchema=schema,
        uiSchema=ui_schema,
        project_config_definitions=[definition.model_dump(mode="json") for definition in plugin_obj.project_config_definitions],
        project_config_selections=current_setting_selections,
        project_configs=[
            ProjectConfigOptionSchema.from_row(project_config)
            for project_config in await project_config_repository.get_by_project(project_plugin.project.pid)
        ],
        description=plugin_obj.help_text,
    )

    return response


@router.get(
    "/{plugin_pid}/parse_dataset/{dataset_uuid}/config/state",
    response=PluginConfigStateResponse,
)
async def parse_plugin_config_state_from_dataset(
        request, plugin_pid: uuid.UUID, dataset_uuid: uuid.UUID
):
    if deployment.is_configurator():
        await sync_to_async(membership.for_plugin)(request, plugin_pid)
    dataset = await ai_component_repository.get(dataset_uuid)
    if not dataset:
        raise HttpError(404, f"Component {dataset_uuid} not found")

    response = file_repository.get_object(
        bucket_name=StorageContainer.Datasets, object_name=dataset.data
    )
    file_content = response["Body"].read()

    plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)

    config = plugin_obj.parse_config_from_dataset(file_content)

    config, schema, ui_schema = plugin_obj.on_config_change(config)

    response = PluginConfigStateResponse(
        plugin_config_id=None, config=config, formSchema=schema, uiSchema=ui_schema,
        project_config_definitions=[definition.model_dump(mode="json") for definition in plugin_obj.project_config_definitions],
        description=plugin_obj.help_text,
    )

    return response
