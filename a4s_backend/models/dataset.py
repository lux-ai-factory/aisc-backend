from django.db import models

from .evaluation import Evaluation
from .common import HasData
from .model import Model
from .datashape import DataShape


class Dataset(HasData):

    project = models.ForeignKey(
        'Project', related_name='datasets', on_delete=models.PROTECT)
    plugin = models.ForeignKey('Plugin', blank=True, null=True,
                                related_name='datasets', on_delete=models.PROTECT)

    def get_models(self) -> list[Model]:
        return list(self.models.all())

    def get_datashape(self) -> DataShape:
        return self.datashape

    def get_evaluations(self) -> list[Evaluation]:
        return list(self.evaluations.all())

    def __str__(self):
        return f'{self.name}'
