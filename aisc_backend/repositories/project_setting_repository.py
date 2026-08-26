import uuid

from aisc_backend.models import ProjectSetting
from aisc_backend.repositories.base_repository import BaseRepository


class ProjectSettingRepository(BaseRepository[ProjectSetting]):
    def __init__(self):
        super().__init__(ProjectSetting)

    async def get(
        self,
        pid: uuid.UUID,
        get_related: bool = False,
        project_pid: uuid.UUID | None = None,
    ) -> ProjectSetting:
        if get_related:
            return await self.get_with_related(pid, project_pid)

        queryset = ProjectSetting.objects
        if project_pid is not None:
            queryset = queryset.filter(project__pid=project_pid)
        return await queryset.aget(pid=pid)

    async def get_with_related(
        self,
        pid: uuid.UUID,
        project_pid: uuid.UUID | None = None,
    ) -> ProjectSetting:
        queryset = ProjectSetting.objects.select_related("project")
        if project_pid is not None:
            queryset = queryset.filter(project__pid=project_pid)
        return await queryset.aget(pid=pid)

    async def get_by_project(self, project_pid: uuid.UUID) -> list[ProjectSetting]:
        return [setting async for setting in ProjectSetting.objects.filter(project__pid=project_pid)]

    async def get_by_category(self, project_pid: uuid.UUID, category: str) -> list[ProjectSetting]:
        return [setting async for setting in ProjectSetting.objects.filter(project__pid=project_pid, category=category)]
