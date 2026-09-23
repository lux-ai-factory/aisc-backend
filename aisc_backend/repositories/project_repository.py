import uuid

from django.db.models import Prefetch

from aisc_backend.models import Project
from aisc_backend.models.ai_system import AISystem
from aisc_backend.models.project import ProjectStatus
from aisc_backend.repositories.base_repository import BaseRepository


class ProjectRepository(BaseRepository[Project]):

    def __init__(self):
        super().__init__(Project)

    async def create(self, name: str) -> Project:
        project = Project(
            name=name,
            status=ProjectStatus.Created
        )
        await project.asave()
        return project


    async def get_with_related(self, pid: uuid.UUID) -> Project:
        project = await (
            Project.objects
            .prefetch_related(
                Prefetch(
                    "aisystem",
                    queryset=AISystem.objects.prefetch_related(
                        "components",
                        "components__source_dataset",
                    ),
                    to_attr="_aisystem",
                ),
                "enabled_plugins",
                "configs",

                "enabled_plugins__current_config"
            )
            .aget(pid=pid)
        )
        return project
