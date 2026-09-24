import uuid

from aisc_backend.models import Plugin, PluginConfig, EvaluationPlugin
from aisc_backend.repositories.base_repository import BaseRepository


class PluginRepository(BaseRepository[Plugin]):

    def __init__(self):
        super().__init__(Plugin)


    async def get_with_related(self, pid: uuid.UUID) -> Plugin:
        plugin = await (
            Plugin.objects
            .select_related("project")
            .select_related("current_config")
            .prefetch_related("current_config__setting_mappings__project_config")
            .aget(pid=pid)
        )
        return plugin

    async def find_by_identity(self, project, package_name: str, version: str, name: str) -> Plugin | None:
        return await Plugin.objects.filter(
            project=project,
            package_name=package_name,
            version=version,
            name=name,
        ).afirst()

    async def list_by_package(self, project_pid: uuid.UUID, package_name: str, version: str) -> list[Plugin]:
        return [
            plugin
            async for plugin in Plugin.objects.filter(
                project__pid=project_pid,
                package_name=package_name,
                version=version,
            )
        ]

    async def configs_with_mappings(self, plugin: Plugin) -> list[PluginConfig]:
        return [
            config
            async for config in plugin.configs.prefetch_related("setting_mappings__project_config").all()
        ]

    async def get_config(self, plugin: Plugin, config_id: int) -> PluginConfig:
        return await PluginConfig.objects.aget(id=config_id, plugin=plugin)

    async def get_setting_mappings(self, plugin_config: PluginConfig):
        return [
            mapping
            async for mapping in plugin_config.setting_mappings.select_related("project_config").all()
        ]


class EvaluationPluginRepository(BaseRepository[EvaluationPlugin]):
    def __init__(self):
        super().__init__(EvaluationPlugin)

    async def get_with_related(self, pid: uuid.UUID) -> EvaluationPlugin:
        return await (
            EvaluationPlugin.objects
            .select_related("plugin_config")
            .select_related("plugin_config__plugin")
            .prefetch_related("plugin_config__setting_mappings__project_config")
            .prefetch_related("evaluation_inputs")
            .prefetch_related("artifacts")
            .aget(pid=pid)
        )
