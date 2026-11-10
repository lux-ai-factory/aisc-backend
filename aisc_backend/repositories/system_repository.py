"""
Repository for systems and their card versions (the system-versions contract).
"""
from aisc_backend.models.qualification import System, SystemVersion
from aisc_backend.models.project import Project


class SystemRepository:
    def _versions(self, system: System):
        return SystemVersion.objects.filter(system=system)

    async def get_or_create(self, project: Project) -> System:
        system = await System.objects.filter(project=project).afirst()
        if system is None:
            system = await System.objects.acreate(
                project=project,
                name=project.name,
                description="",
            )
        return system

    async def next_number(self, system: System) -> int:
        last = await self._versions(system).order_by("-number").afirst()
        return (last.number + 1) if last is not None else 1

    async def create_version(
        self, system: System, name: str, version: str = "", provider: str = "",
        description: str = "", created_by: str = "",
    ) -> SystemVersion:
        number = await self.next_number(system)
        row = await SystemVersion.objects.acreate(
            system=system,
            project_id=system.project_id,   # FK column (int), no relation fetch
            number=number,
            name=name,
            version=version,
            provider=provider,
            description=description,
            created_by=created_by,
        )
        return row

    async def latest(self, system: System) -> SystemVersion | None:
        return await self._versions(system).order_by("-number").afirst()

    async def list_versions(self, system: System) -> list[SystemVersion]:
        return [row async for row in self._versions(system).order_by("-number")]

    def iterate_versions(self, system: System):
        """An async iterable of a system's versions, newest first."""
        return self._versions(system).order_by("-number")
