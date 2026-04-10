import uuid

from vera_backend.models import Project
from vera_backend.models.project import ProjectStatus
from vera_backend.repositories.base_repository import BaseRepository


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
            .select_related(
                "expected_datashape",
                "expected_datashape__dataset"
            )
            .prefetch_related(
                "datasets",
                "models",
                "enabled_plugins",

                "enabled_plugins__current_config",

                "expected_datashape__features",
                "expected_datashape__date_feature",
                "expected_datashape__target_feature"
            )
            .aget(pid=pid)
        )
        return project
