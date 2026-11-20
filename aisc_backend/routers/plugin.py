import uuid

from asgiref.sync import sync_to_async
from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_plugins.models import Plugin, PluginConfig
from aisc_backend.models import ProjectConfig
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
from packaging.version import parse as parse_version
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


@router.get("/{plugin_pid}/project_config_definitions", response=list[dict])
async def get_plugin_project_config_definitions(request, plugin_pid: uuid.UUID):
    plugin = await plugin_repository.get(plugin_pid)
    plugin_obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)
    return [definition.model_dump(mode="json") for definition in plugin_obj.project_config_definitions]


class CreatePluginsRequest(Schema):
    package_name: str
    version: str
    project_uuid: uuid.UUID


@router.post("", response=list[PluginOutSchema])
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

    if created_plugins and all(not p.enabled for p in created_plugins):
        for p in created_plugins:
            p.enabled = True
            await plugin_repository.save(p)

    await sync_to_async(log_action)(
        request, action="install", resource_type="plugin", resource_id=data.package_name,
        metadata={"projectPid": str(data.project_uuid), "version": data.version,
                  "plugins": [p.name for p in created_plugins]})

    return created_plugins


class RefreshPluginRequest(Schema):
    package_name: str
    version: str
    project_uuid: uuid.UUID


@router.post("/refresh", response=list[PluginOutSchema])
async def refresh_plugins(request, data: RefreshPluginRequest):
    """Re-import a package's plugin code. Only the project's already-installed
    plugins from that package are updated (metadata refreshed) — plugins the
    project did not install are never created or enabled here."""
    plugins_package_dict = plugin_loader.refresh_package(data.package_name, data.version)

    existing = await plugin_repository.list_by_package(
        data.project_uuid, data.package_name, data.version
    )
    existing_by_name = {p.name: p for p in existing}

    result = []
    for plugin_name, plugin_obj in plugins_package_dict.items():
        project_plugin = existing_by_name.get(plugin_name)
        if project_plugin is None:
            continue
        project_plugin.display_name = plugin_obj.display_name
        await plugin_repository.save(project_plugin)
        result.append(project_plugin)

    return result


class DeletePluginsRequest(Schema):
    package_name: str
    version: str
    project_uuid: uuid.UUID


@router.delete("", response={204: None})
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
        metadata={"projectPid": str(data.project_uuid), "version": data.version})
    return 204, None


class UpdatePluginEnabledRequest(Schema):
    enabled: bool


@router.patch("/{plugin_pid}/enabled", response=PluginOutSchema)
async def update_plugin_enabled(
        request, plugin_pid: uuid.UUID, data: UpdatePluginEnabledRequest
):
    # get_with_related prefetches current_config + setting mappings so the
    # schema's `config` serializes without hitting a lazy related query async.
    plugin = await plugin_repository.get_with_related(plugin_pid)
    plugin.enabled = data.enabled
    await plugin_repository.save(plugin)
    await sync_to_async(log_action)(
        request, action="toggle", resource_type="plugin",
        resource_id=str(plugin_pid),
        metadata={"projectPid": str(plugin.project.pid), "enabled": data.enabled})
    return plugin


@router.get(
    "/{plugin_pid}/configs",
    response=list[PluginConfigOutSchema],
)
async def get_plugin_config_history(request, plugin_pid: uuid.UUID):
    plugin = await plugin_repository.get(plugin_pid)
    return await plugin_repository.configs_with_mappings(plugin)


@router.post(
    "/{plugin_pid}/configs/{config_id}/restore",
    response=PluginOutSchema,
)
async def restore_plugin_config(
        request, plugin_pid: uuid.UUID, config_id: int
):
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


# --- version switching + update-from-catalogue --------------------------------


class UpdatePluginVersionRequest(Schema):
    version: str


@router.patch("/{plugin_pid}/version", response=list[PluginOutSchema])
async def update_plugin_version(request, plugin_pid: uuid.UUID, data: UpdatePluginVersionRequest):
    """Switch a project's plugin(s) of this package to another available version.

    Keeps history: the current-version rows are soft-disabled (same convention
    as delete), and the target-version rows (existing or new) are enabled, so
    evaluation/plugin-config history stays attached to its version. Audited.
    """
    return await _switch_version(request, plugin_pid, data.version, action="version_change")


class UpdatePluginFromCatalogueRequest(Schema):
    """Optional pin to a specific version; empty = latest available."""

    version: str = ""


@router.post("/{plugin_pid}/update", response=list[PluginOutSchema])
async def update_plugin_from_catalogue(
    request, plugin_pid: uuid.UUID, data: UpdatePluginFromCatalogueRequest
):
    """Bring a plugin card up to date from the catalogue.

    With no version this picks the latest available version of the package; with
    a version it pins exactly like PATCH version. Same history-preserving
    semantics. Audited.
    """
    plugin = await plugin_repository.get(plugin_pid)
    packages = plugin_loader.list_packages()
    versions = packages.get(plugin.package_name, {})
    if not versions:
        raise HttpError(422, f"{plugin.package_name} is not available in the catalogue")

    target = data.version or _latest_version(versions)
    return await _switch_version(request, plugin_pid, target, action="update", versions=versions)


def _latest_version(versions: dict) -> str:
    """The newest key, by PEP 440: '0.10' beats '0.9' (plain max() on strings would not)."""
    return str(max(versions, key=parse_version))


async def _switch_version(
    request, plugin_pid: uuid.UUID, target: str, action: str, versions: dict | None = None
) -> list[Plugin]:
    """Shared switch logic: validate, disable old version, enable/create new."""
    plugin = await plugin_repository.get_with_related(plugin_pid)
    project = plugin.project   # prefetched by get_with_related (select_related)

    if versions is None:
        packages = plugin_loader.list_packages()
        versions = packages.get(plugin.package_name, {})
    if target not in versions:
        raise HttpError(422, f"version {target} of {plugin.package_name} is not available")

    old_version = plugin.version
    if target != old_version:
        await _set_version_enabled(request, plugin, project, old_version, enable=False)
    result = await _set_version_enabled(request, plugin, project, target, enable=True)
    await sync_to_async(log_action)(
        request, action=action, resource_type="plugin", resource_id=str(plugin.pid),
        metadata={"projectPid": str(project.pid), "package_name": plugin.package_name,
                  "from_version": old_version, "to_version": target,
                  "plugins": [row.name for row in result]})
    return result


async def _set_version_enabled(
    request, plugin: Plugin, project, version: str, enable: bool
) -> list[Plugin]:
    """Enable/disable every row of the package's given version in a project,
    creating the version's rows when enabling (mirroring create_plugins)."""
    if enable:
        loaded = plugin_loader.load_package(plugin.package_name, version)
        result = []
        for name, plugin_obj in loaded.items():
            existing = await plugin_repository.find_by_identity(
                project, plugin.package_name, version, name
            )
            if existing is not None:
                row = existing
                if not row.enabled:
                    row.enabled = True
                    await plugin_repository.save(row)
            else:
                row = await plugin_repository.create(Plugin(
                    name=name,
                    display_name=plugin_obj.display_name,
                    package_name=plugin.package_name,
                    version=version,
                    project=project,
                    enabled=True,
                ))
            result.append(row)
        return result

    rows = await plugin_repository.list_by_package(project.pid, plugin.package_name, version)
    for row in rows:
        if row.enabled:
            row.enabled = False
            await plugin_repository.save(row)
    return rows
