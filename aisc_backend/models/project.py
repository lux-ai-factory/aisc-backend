from django.db import models

from .ai_system import AIComponent
from .project_config import ProjectConfig
from .evaluation import Evaluation
from .common import Base
from .plugin import Plugin


class ProjectStatus(models.TextChoices):
    Ready = 'Ready', 'Ready'
    Closed = 'Closed', 'Closed'
    Pending = 'Pending', 'Pending'
    Archived = 'Archived', 'Archived'
    Created = 'Created', 'Created'


class Project(Base):
    status = models.CharField(max_length=255, choices=ProjectStatus.choices)

    # The platform project this workspace belongs to (core.project.pid in the
    # one database). Null for projects made before the platform, and for any
    # made outside it: an absent link, not an invalid row. The foreign key is
    # added in the migration, since it crosses into a schema the engine only
    # reads.
    # `project_id` in the database, the same name every other module uses for
    # this link; the attribute keeps saying platform_project_id, because in here
    # `project` would read as the engine's own project rather than the
    # platform's.
    platform_project_id = models.UUIDField(
        null=True, blank=True, db_index=True, db_column="project_id"
    )

    class Meta:
        constraints = [
            # One project here per platform project: this row is the engine's
            # side of the project chosen on the launcher, not a second project.
            # Rows with none are the engine used on its own, and there may be
            # as many of those as someone makes.
            models.UniqueConstraint(
                fields=("platform_project_id",),
                condition=models.Q(platform_project_id__isnull=False),
                name="one_project_per_platform_project",
            )
        ]

    def get_components(self) -> list[AIComponent]:
        return list(self.aisystem.components.all())

    def get_evaluations(self) -> list[Evaluation]:
        return list(self.evaluations.all())

    def get_enabled_plugins(self) -> list[Plugin]:
        # Filter the prefetched list in Python so this works from async
        # contexts (ninja's get-project endpoint). A `.filter()` call would
        # trigger a new sync DB query and raise SynchronousOnlyOperation.
        return sorted(
            [p for p in self.enabled_plugins.all() if p.enabled],
            key=lambda plugin: (
                plugin.package_name.lower(),
                plugin.version.lower(),
                plugin.name.lower(),
            ),
        )

    def get_plugins(self) -> list[Plugin]:
        return sorted(
            list(self.enabled_plugins.all()),
            key=lambda plugin: (
                plugin.package_name.lower(),
                plugin.version.lower(),
                plugin.name.lower(),
            ),
        )

    def get_configs(self) -> list[ProjectConfig]:
        return list(self.configs.all())

    def __str__(self):
        return f'{self.name}, status: {self.status}'
