import uuid

from vera_backend.models import Plugin, EvaluationPlugin
from vera_backend.repositories.base_repository import BaseRepository


class PluginRepository(BaseRepository[Plugin]):

    def __init__(self):
        super().__init__(Plugin)


    async def get_with_related(self, pid: uuid.UUID) -> Plugin:
        plugin = await (
            Plugin.objects
            .select_related("current_config")
            .aget(pid=pid)
        )
        return plugin


class EvaluationPluginRepository(BaseRepository[EvaluationPlugin]):
    def __init__(self):
        super().__init__(EvaluationPlugin)

    async def get_with_related(self, pid: uuid.UUID) -> EvaluationPlugin:
        return await (
            EvaluationPlugin.objects
            .select_related("plugin_config")
            .select_related("plugin_config__plugin")
            .prefetch_related("input_files")
            .prefetch_related("artifacts")
            .aget(pid=pid)
        )