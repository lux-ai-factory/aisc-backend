import logging
import uuid

from asgiref.sync import sync_to_async
from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend import ledger
from aisc_backend.config_secret_fields import (CONFIG_SECRET_PREFIX, RESTORE_REFUSED, SECRET_DESCRIPTION,
                                               SecretFieldRefused, check_split, resolved, secret_hints,
                                               split_secret_fields, value_at, with_set_at)
from aisc_backend.config_secrets import LEDGER_SECRET_NAME, SECRET_SET, masked_for_ledger
from aisc_backend.audit.log import log_action
from aisc_backend.models import (Plugin, PluginConfig, PluginConfigProjectConfig, ProjectConfig,
                                 ProjectConfigCategory)
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.ai_component_repository import AIComponentRepository
from aisc_backend.repositories.base_repository import BaseRepository
from aisc_backend.repositories.evaluation_repository import EvaluationRepository
from aisc_backend.repositories.measurement_repository import MeasurementRepository
from aisc_backend.repositories.plugin_repository import PluginRepository, EvaluationPluginRepository
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.routers.project_config import _masked as masked_secret_value
from aisc_backend.schemas.measure import MeasureOutSchema
from aisc_backend.schemas.plugin import PluginOutSchema, PluginConfigOutSchema
from aisc_backend.schemas.project_config import ProjectConfigOptionSchema, ProjectConfigSelectionSchema
from aisc_backend.services.project_config_keys import normalize_project_config_key
from aisc_backend.utils.encryption import decrypt_value, encrypt_value
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


def _package_event(action: str, package_name: str, version: str, **details) -> None:
    ledger.emit(action, item_type="plugin", item_id=package_name,
                details={"package": package_name, "version": version, **details})


logger = logging.getLogger(__name__)

#: A key holding one of these is taken for a secret even when the plugin's form does not mark it
#: (a plugin that cannot be loaded, or a field saved before the form marked it).
_SECRET_NAME = LEDGER_SECRET_NAME


def _secret(name: str, field: dict, ui: dict) -> bool:
    return (field.get("format") == "password" or field.get("writeOnly") is True
            or (isinstance(ui, dict) and ui.get("ui:widget") == "password") or bool(_SECRET_NAME.search(name)))


def _masked(value, schema: dict, ui, defs: dict):
    """`value` with every secret field's value replaced by SECRET_SET, or None when it is empty."""
    if isinstance(value, list):
        items = resolved(schema.get("items"), defs)
        ui_items = ui.get("items") if isinstance(ui, dict) else None
        return [_masked(item, items, ui_items, defs) for item in value]
    if not isinstance(value, dict):
        return value
    properties = schema.get("properties") or {}
    out = {}
    for name, inner in value.items():
        field = resolved(properties.get(name), defs)
        field_ui = ui.get(name) if isinstance(ui, dict) else None
        if _secret(str(name), field, field_ui):
            out[name] = SECRET_SET if inner not in (None, "", [], {}) else None
        else:
            out[name] = _masked(inner, field, field_ui, defs)
    return out


def _plugin_form(plugin: Plugin, plugin_obj=None) -> tuple[dict, dict]:
    """(schema, ui schema) of the plugin's configuration form, ({}, {}) when it cannot be read: its secret
    fields are then found by name and value alone."""
    try:
        plugin_obj = plugin_obj or plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)
        schema, ui = plugin_obj.get_full_schema()
    except Exception:  # noqa: BLE001 - its secrets are then found by name alone
        logger.warning("plugin %s could not be loaded to read its secret config fields", plugin.name)
        return {}, {}
    return (schema if isinstance(schema, dict) else {}), (ui if isinstance(ui, dict) else {})


def _ledger_config(config, schema: dict, ui: dict, kept_paths=()):
    """The configuration as the ledger may hold it. The secret fields are those the plugin's form marks
    (format password, as SecretStr makes it, writeOnly, the password widget), and any whose name says so;
    a field kept as a project secret is set."""
    masked = _masked(config, schema, ui, schema.get("$defs") or {})
    return with_set_at(masked, kept_paths, SECRET_SET)


def _shown_config(config, schema: dict, ui: dict, kept_paths=()):
    """A stored configuration as the engine answers it: masked as the ledger copy is, and every string with a
    key shape set, so a row saved before keys were kept as project secrets never answers its key in clear."""
    return masked_for_ledger(_ledger_config(config, schema, ui, kept_paths))


def shown_current_config(plugin: Plugin) -> dict | None:
    """A plugin's current configuration as a serialised plugin carries it (`_shown_config`), None when it has
    none. Its kept paths come from the configuration's prefetched mappings: without them a kept field is only
    absent, as it is stored. The current configuration must be loaded already (async code cannot load it)."""
    current = plugin.current_config
    if current is None:
        return None
    schema, ui = _plugin_form(plugin)
    mappings = getattr(current, "_prefetched_objects_cache", {}).get("setting_mappings", [])
    return _shown_config(current.config, schema, ui, _kept_paths(mappings))


def _form_config(config, shown):
    """`config` without every value its shown copy (`_shown_config`) masks: a field is left out, as a kept field
    is, and a list item is emptied. The form posts it back on save, so it holds no key and no placeholder."""
    if isinstance(config, dict) and isinstance(shown, dict):
        return {name: _form_config(value, shown.get(name)) for name, value in config.items()
                if _unmasked(value, shown.get(name))}
    if isinstance(config, list) and isinstance(shown, list) and len(config) == len(shown):
        return [_form_config(item, masked) if _unmasked(item, masked) else "" for item, masked in zip(config, shown)]
    return config


def _unmasked(value, shown) -> bool:
    """`value` is answered as it is stored, or is a container its shown copy masks only inside."""
    if isinstance(value, dict) and isinstance(shown, dict):
        return True
    if isinstance(value, list) and isinstance(shown, list) and len(value) == len(shown):
        return True
    return value == shown


def _configured(plugin_pid, plugin_config: PluginConfig, settings: dict, ledger_config) -> None:
    """A plugin's configuration in the ledger: which settings it uses, by key, never their values, and
    its secret fields only as set or not."""
    ledger.emit("engine.plugin.configured", item_type="plugin", item_id=plugin_pid,
                content={"config_id": plugin_config.id, "config": ledger_config, "settings": settings})


async def _kept_secrets(plugin_config_id) -> dict[str, ProjectConfig]:
    """path -> the project secret a configuration's field is kept as (its `config.<path>` mappings)."""
    if plugin_config_id is None:
        return {}
    return {mapping.plugin_config_key[len(CONFIG_SECRET_PREFIX):]: mapping.project_config
            async for mapping in PluginConfigProjectConfig.objects
            .filter(plugin_config_id=plugin_config_id, plugin_config_key__startswith=CONFIG_SECRET_PREFIX)
            .select_related("project_config").order_by("plugin_config_key")}


def _kept_paths(mappings) -> list[str]:
    """The paths of the fields a configuration keeps as project secrets, from its mappings."""
    return [mapping.plugin_config_key[len(CONFIG_SECRET_PREFIX):] for mapping in mappings
            if mapping.plugin_config_key.startswith(CONFIG_SECRET_PREFIX)]


def _same_value(secret: ProjectConfig, value: str) -> bool:
    try:
        return decrypt_value(secret.encrypted_value) == value
    except Exception:  # noqa: BLE001 - a value that no longer decrypts is replaced
        return False


async def _keep_secret(plugin: Plugin, path: str, value: str, secret: ProjectConfig | None) -> ProjectConfig:
    """The project secret that holds the key of field `path`: the one the configuration already kept it in,
    its value replaced when it changed, or a new one under the first free key `<plugin>_<path>`, `_2`, `_3`, ...
    A secret the person made under that key is never touched."""
    if secret is not None:
        if not _same_value(secret, value):
            secret.encrypted_value = encrypt_value(value)
            secret.masked_value = masked_secret_value(value)
            await project_config_repository.save(secret)
        return secret
    base = normalize_project_config_key(f"{plugin.name}_{path}")[:240]
    taken = {key async for key in ProjectConfig.objects.filter(
        project=plugin.project, category=ProjectConfigCategory.SECRETS, key__startswith=base).values_list("key", flat=True)}
    key, number = base, 1
    while key in taken:
        number += 1
        key = f"{base}_{number}"
    return await project_config_repository.create(ProjectConfig(
        project=plugin.project, category=ProjectConfigCategory.SECRETS, key=key,
        name=f"{plugin.display_name}: {path}"[:255], description=SECRET_DESCRIPTION,
        encrypted_value=encrypt_value(value), masked_value=masked_secret_value(value)))


def _hinted(ui_schema, kept: dict[str, ProjectConfig]) -> dict:
    return secret_hints(ui_schema if isinstance(ui_schema, dict) else {},
                        {path: secret.key for path, secret in kept.items()})


class PluginConfigStateResponse(Schema):
    plugin_config_id: int | None
    config: dict | None
    formSchema: dict
    uiSchema: dict
    project_config_definitions: list[dict]
    project_config_selections: list[ProjectConfigSelectionSchema] = []
    project_configs: list[ProjectConfigOptionSchema] = []
    description: str = ""
    secret_fields: list[str] = []


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
    # Set when the install came from the catalogue (both modes). Optional so a deep link or a
    # direct API call still works; it is then recorded as no origin at all.
    catalogue_slug: str | None = None


@router.post("", response=list[PluginOutSchema])
async def create_plugins(request, data: CreatePluginsRequest):
    project = await project_repository.get(data.project_uuid, True)

    plugins_package_dict = plugin_loader.load_package(data.package_name, data.version)

    async def install():
        # the rows and the install's one ledger event commit together
        created_plugins = []
        reused = True
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
                reused = False
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
        await sync_to_async(_package_event)("plugin.installed", data.package_name, data.version, reused=reused)
        return created_plugins

    created_plugins = await ledger.atomically(install)

    await sync_to_async(log_action)(
        request, action="install", resource_type="plugin", resource_id=data.package_name,
        metadata={"version": data.version, "projectPid": str(data.project_uuid),
                  "catalogueSlug": data.catalogue_slug,
                  "plugins": [p.name for p in created_plugins]})

    # reloaded with their configuration, so a configured plugin answers its configuration masked
    return await plugin_repository.list_with_related([p.pid for p in created_plugins])


class RefreshPluginRequest(Schema):
    package_name: str
    version: str
    project_uuid: uuid.UUID


@router.post("/refresh", response=list[PluginOutSchema])
async def refresh_plugins(request, data: RefreshPluginRequest):
    plugins_package_dict = plugin_loader.refresh_package(data.package_name, data.version)

    project = await project_repository.get(data.project_uuid, True)

    existing = await plugin_repository.list_by_package(
        data.project_uuid, data.package_name, data.version
    )
    existing_by_name = {p.name: p for p in existing}

    async def refresh():
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
        await sync_to_async(_package_event)("plugin.refreshed", data.package_name, data.version)
        return result

    refreshed = await ledger.atomically(refresh)
    # reloaded with their configuration, so a configured plugin answers its configuration masked
    return await plugin_repository.list_with_related([p.pid for p in refreshed])


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

    async def remove():
        for plugin in plugins:
            if plugin.enabled:
                plugin.enabled = False
                await plugin_repository.save(plugin)
        await sync_to_async(_package_event)("plugin.removed", data.package_name, data.version)

    await ledger.atomically(remove)
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
    # with its current configuration, which the answer carries (a lazy load cannot run here)
    plugin = await plugin_repository.get_with_related(plugin_pid)
    plugin.enabled = data.enabled

    async def toggle():
        await plugin_repository.save(plugin)
        await sync_to_async(ledger.emit)("engine.plugin.toggled", item_type="plugin", item_id=plugin_pid,
                                         details={"enabled": data.enabled})

    await ledger.atomically(toggle)
    await sync_to_async(log_action)(
        request, action="toggle", resource_type="plugin",
        resource_id=str(plugin_pid), metadata={"enabled": data.enabled})
    return plugin


@router.get(
    "/{plugin_pid}/configs",
    response=list[PluginConfigOutSchema],
)
async def get_plugin_config_history(request, plugin_pid: uuid.UUID):
    """Every stored configuration of the plugin, each with its keys set (an older row may still hold one)."""
    plugin = await plugin_repository.get(plugin_pid)
    schema, ui = _plugin_form(plugin)
    return [PluginConfigOutSchema.from_orm(row).model_copy(update={"config": _shown_config(
                row.config, schema, ui, _kept_paths(row.setting_mappings.all()))})
            for row in await plugin_repository.configs_with_mappings(plugin)]


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

    # a row saved before keys were kept as project secrets still holds its key: it is not made current again
    schema, ui = _plugin_form(plugin)
    stored = config.config if isinstance(config.config, dict) else {}
    split = split_secret_fields(stored, schema, ui)
    if split.moved or split.not_text:
        raise HttpError(409, RESTORE_REFUSED.format(config_id=config_id,
                                                    fields=", ".join(list(split.moved) + split.not_text)))

    plugin.current_config = config
    mappings = await plugin_repository.get_setting_mappings(config)
    settings = {mapping.plugin_config_key: str(mapping.project_config.pid) for mapping in mappings}
    kept_paths = _kept_paths(mappings)

    async def restore():
        await plugin_repository.save(plugin)
        await sync_to_async(_configured)(plugin_pid, config, settings,
                                         _ledger_config(config.config, schema, ui, kept_paths))

    await ledger.atomically(restore)

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

    # A key field is not stored in the configuration: it is kept as a project secret the configuration
    # points to through a `config.<path>` mapping, and handed back to the plugin at run time.
    form_schema, form_ui = _plugin_form(project_plugin, plugin_obj)
    split = split_secret_fields(data.config, form_schema, form_ui)
    try:
        check_split(split, adopted=getattr(plugin_obj, "target_kinds", None) is not None)
    except SecretFieldRefused as refused:
        raise HttpError(422, str(refused))
    # the backend alone writes the `config.*` mappings
    selections = [selection for selection in data.project_config_selections
                  if not (selection.plugin_config_key or "").startswith(CONFIG_SECRET_PREFIX)]

    plugin_config = PluginConfig(plugin=project_plugin, config=split.kept)

    async def configure():
        previous = await _kept_secrets(project_plugin.current_config_id)
        kept = {path: await _keep_secret(project_plugin, path, value, previous.get(path))
                for path, value in split.moved.items()}
        for path, secret in previous.items():
            if path not in kept and value_at(split.kept, path, "") == "":
                kept[path] = secret
        # A row saved before keys were kept as project secrets holds its key in clear; the form withholds it,
        # so the save leaves the field empty or absent. The key is carried into a project secret rather than
        # lost (a plugin that adopted @assesses takes no key field). The old row is left to the cleanup.
        current = project_plugin.current_config
        if (current is not None and isinstance(current.config, dict)
                and getattr(plugin_obj, "target_kinds", None) is None):
            old = split_secret_fields(current.config, form_schema, form_ui).moved
            for path, value in sorted(old.items()):
                if path not in kept and value_at(split.kept, path, "") == "":
                    kept[path] = await _keep_secret(project_plugin, path, value, None)
        saved = await plugin_config_repository.save(plugin_config)
        try:
            await project_config_repository.save_plugin_config_mappings(
                saved, project_plugin.project.pid, selections
            )
        except ProjectConfig.DoesNotExist:
            raise HttpError(400, "Selected project config does not belong to this project")
        for path, secret in kept.items():
            await PluginConfigProjectConfig.objects.acreate(
                plugin_config=saved, project_config=secret, plugin_config_key=CONFIG_SECRET_PREFIX + path)
        project_plugin.current_config = saved
        await plugin_repository.save(project_plugin)
        settings = {mapping.plugin_config_key: str(mapping.project_config.pid)
                    for mapping in await plugin_repository.get_setting_mappings(saved)}
        await sync_to_async(_configured)(plugin_pid, saved, settings,
                                         _ledger_config(split.kept, form_schema, form_ui, kept))
        return saved, kept

    plugin_config, kept = await ledger.atomically(configure)

    config, schema, ui_schema = plugin_obj.on_config_change(plugin_config.config)

    response = PluginConfigStateResponse(
        plugin_config_id=plugin_config.id,
        config=config,
        formSchema=schema,
        uiSchema=_hinted(ui_schema, kept),
        secret_fields=sorted(kept),
        project_config_definitions=[definition.model_dump(mode="json") for definition in plugin_obj.project_config_definitions],
        project_config_selections=selections,
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
    kept = await _kept_secrets(project_plugin.current_config_id)

    response = PluginConfigStateResponse(
        plugin_config_id=None, config=config, formSchema=schema, uiSchema=_hinted(ui_schema, kept),
        secret_fields=sorted(kept),
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
    # a row saved before keys were kept as project secrets may still hold one: the form gets the field empty
    form_schema, form_ui = _plugin_form(project_plugin, plugin_obj)
    config = _form_config(config, _shown_config(config, form_schema, form_ui))

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

    kept = await _kept_secrets(project_plugin.current_config_id)

    response = PluginConfigStateResponse(
        plugin_config_id=plugin_config_id,
        config=config,
        formSchema=schema,
        uiSchema=_hinted(ui_schema, kept),
        secret_fields=sorted(kept),
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
    kept = await _kept_secrets(plugin.current_config_id)

    response = PluginConfigStateResponse(
        plugin_config_id=None, config=config, formSchema=schema, uiSchema=_hinted(ui_schema, kept),
        secret_fields=sorted(kept),
        project_config_definitions=[definition.model_dump(mode="json") for definition in plugin_obj.project_config_definitions],
        description=plugin_obj.help_text,
    )

    return response
