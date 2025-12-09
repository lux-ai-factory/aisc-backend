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
    frequency = models.CharField(max_length=255)
    window_size = models.CharField(max_length=255)

    expected_datashape = models.OneToOneField(
        'DataShape', related_name='project', on_delete=models.PROTECT, null=True, blank=True,)

    def get_datasets(self) -> list[Dataset]:
        return list(self.datasets.all())

    def get_models(self) -> list[Model]:
        dataset_models: list[Model] = []
        for dataset in self.get_datasets():
            for model in dataset.get_models():
                dataset_models.append(model)

        return dataset_models

    def get_evaluations(self) -> list[Evaluation]:
        return list(self.evaluations.all())

    def get_plugins(self) -> list[Plugin]:
        return list(self.plugins.all())

    def __str__(self):
        return f'{self.name}, status: {self.status}'
