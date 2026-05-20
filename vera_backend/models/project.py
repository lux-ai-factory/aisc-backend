from django.db import models

from .evaluation import Evaluation
from .common import Base
from .dataset import Dataset
from .model import Model
from .plugin import Plugin


class ProjectStatus(models.TextChoices):
    Ready = 'Ready', 'Ready'
    Closed = 'Closed', 'Closed'
    Pending = 'Pending', 'Pending'
    Archived = 'Archived', 'Archived'
    Created = 'Created', 'Created'


class Project(Base):
    status = models.CharField(max_length=255, choices=ProjectStatus.choices)

    expected_datashape = models.OneToOneField(
        'DataShape', related_name='project', on_delete=models.PROTECT, null=True, blank=True,)

    def get_datasets(self) -> list[Dataset]:
        return list(self.datasets.all())

    def get_models(self) -> list[Model]:
        return list(self.models.all())

    def get_evaluations(self) -> list[Evaluation]:
        return list(self.evaluations.all())

    def get_enabled_plugins(self) -> list[Plugin]:
        # Filter the prefetched list in Python so this works from async
        # contexts (ninja's get-project endpoint). A `.filter()` call would
        # trigger a new sync DB query and raise SynchronousOnlyOperation.
        return [p for p in self.enabled_plugins.all() if p.enabled]

    def __str__(self):
        return f'{self.name}, status: {self.status}'
