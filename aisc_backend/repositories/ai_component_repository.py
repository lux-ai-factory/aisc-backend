import uuid

from aisc_backend.models import AIComponent, AISystem
from aisc_backend.repositories.base_repository import BaseRepository


class AISystemRepository(BaseRepository[AISystem]):

    def __init__(self):
        super().__init__(AISystem)

    async def get_or_create_for_project(self, project) -> AISystem:
        system = await AISystem.objects.filter(project=project).afirst()
        if system is None:
            system = AISystem(
                project=project,
                name=f"{project.name} system",
                description="",
            )
            await system.asave()
        return system

    async def get_with_components(self, project) -> AISystem | None:
        return await (
            AISystem.objects.filter(project=project)
            .prefetch_related("components", "components__source_dataset")
            .afirst()
        )


class AIComponentRepository(BaseRepository[AIComponent]):

    def __init__(self):
        super().__init__(AIComponent)

    async def get(self, component_pid: uuid.UUID) -> AIComponent | None:
        return await AIComponent.objects.filter(pid=component_pid).afirst()

    async def get_with_source_dataset(self, component_pid: uuid.UUID) -> AIComponent | None:
        return await (
            AIComponent.objects.select_related("source_dataset")
            .filter(pid=component_pid)
            .afirst()
        )

    async def get_with_system_project(self, component_pid: uuid.UUID) -> AIComponent | None:
        return await (
            AIComponent.objects.select_related("system__project")
            .filter(pid=component_pid)
            .afirst()
        )
