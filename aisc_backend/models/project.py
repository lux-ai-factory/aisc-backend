from django.db import models

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

    def get_components(self) -> list:
        # Prefetched (via ProjectRepository) as self._aisystem -> AISystem.
        system = getattr(self, "_aisystem", None)
        if system is not None:
            return list(system.components.all())
        if self.aisystem_id is not None:
            return list(self.aisystem.components.all())
        from .ai_system import AIComponent
        return list(AIComponent.objects.filter(system__project=self))

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
