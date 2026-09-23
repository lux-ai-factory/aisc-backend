import uuid

from aisc_backend.models import ProjectConfig
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
