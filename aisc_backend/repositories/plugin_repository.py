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

    async def list_with_related(self, pids: list[uuid.UUID]) -> list[Plugin]:
        """The plugins with these pids, in the given order, each loaded as get_with_related loads one, in a
        fixed number of queries."""
        found = {
            plugin.pid: plugin
            async for plugin in Plugin.objects
            .select_related("project")
            .select_related("current_config")
            .prefetch_related("current_config__setting_mappings__project_config")
            .filter(pid__in=pids)
        }
        return [found[pid] for pid in pids if pid in found]

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

    @staticmethod
    def _with_related():
        return (
            EvaluationPlugin.objects
            .select_related("plugin_config")
            .select_related("plugin_config__plugin")
            .prefetch_related("plugin_config__setting_mappings__project_config")
            .prefetch_related("evaluation_inputs")
            .prefetch_related("artifacts")
        )

    async def get_with_related(self, pid: uuid.UUID) -> EvaluationPlugin:
        return await self._with_related().aget(pid=pid)

    async def get_of_evaluation(
        self, evaluation_plugin_pid: uuid.UUID, evaluation_pid: uuid.UUID, with_related: bool = False
    ) -> EvaluationPlugin:
        """The plugin run with this pid, only if it belongs to the evaluation with that pid.

        Raises EvaluationPlugin.DoesNotExist for an unknown pid and for a run of another evaluation alike.
        """
        queryset = self._with_related() if with_related else EvaluationPlugin.objects
        return await queryset.aget(pid=evaluation_plugin_pid, evaluation__pid=evaluation_pid)
