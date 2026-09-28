import uuid

from aisc_backend.models import PluginConfigProjectConfig, ProjectConfig, ProjectConfigCategory
from aisc_backend.repositories.base_repository import BaseRepository


class ProjectConfigRepository(BaseRepository[ProjectConfig]):
    def __init__(self):
        super().__init__(ProjectConfig)

    async def get(
        self,
        pid: uuid.UUID,
        get_related: bool = False,
        project_pid: uuid.UUID | None = None,
    ) -> ProjectConfig:
        if get_related:
            return await self.get_with_related(pid, project_pid)

        queryset = ProjectConfig.objects
        if project_pid is not None:
            queryset = queryset.filter(project__pid=project_pid)
        return await queryset.aget(pid=pid)

    async def get_with_related(
        self,
        pid: uuid.UUID,
        project_pid: uuid.UUID | None = None,
    ) -> ProjectConfig:
        queryset = ProjectConfig.objects.select_related("project")
        if project_pid is not None:
            queryset = queryset.filter(project__pid=project_pid)
        return await queryset.aget(pid=pid)

    async def get_by_project(self, project_pid: uuid.UUID) -> list[ProjectConfig]:
        return [project_config async for project_config in ProjectConfig.objects.filter(project__pid=project_pid)]

    async def get_by_category(self, project_pid: uuid.UUID, category: str) -> list[ProjectConfig]:
        return [project_config async for project_config in ProjectConfig.objects.filter(project__pid=project_pid, category=category)]

    async def get_secret_by_key(self, project_pid: uuid.UUID, key: str) -> ProjectConfig | None:
        return await ProjectConfig.objects.filter(
            project__pid=project_pid,
            key=key,
            category=ProjectConfigCategory.SECRETS,
        ).afirst()

    async def secret_exists(self, project_pid: uuid.UUID, key: str) -> bool:
        return await ProjectConfig.objects.filter(
            project__pid=project_pid,
            key=key,
            category=ProjectConfigCategory.SECRETS,
        ).aexists()

    async def get_by_pids(self, project_pid: uuid.UUID, pids: list[uuid.UUID]) -> list[ProjectConfig]:
        return [
            project_config
            async for project_config in ProjectConfig.objects.filter(
                project__pid=project_pid,
                pid__in=pids,
            )
        ]

    async def save_plugin_config_mappings(self, plugin_config, project_pid: uuid.UUID, selections) -> None:
        await PluginConfigProjectConfig.objects.filter(plugin_config=plugin_config).adelete()
        for selection in selections:
            plugin_config_key = selection.plugin_config_key
            project_config_pid = selection.project_config_pid
            if not plugin_config_key or not project_config_pid:
                continue
            project_config = await self.get(project_config_pid, project_pid=project_pid)
            await PluginConfigProjectConfig.objects.acreate(
                plugin_config=plugin_config,
                project_config=project_config,
                plugin_config_key=plugin_config_key,
            )
