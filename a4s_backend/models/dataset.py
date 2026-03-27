from django.db import models

from .evaluation import Evaluation
from .common import HasData
from .datashape import DataShape


class Dataset(HasData):
    project = models.ForeignKey(
        'Project', related_name='datasets', on_delete=models.PROTECT)

    def get_datashape(self) -> DataShape:
        return self.datashape

    def get_evaluations(self) -> list[Evaluation]:
        return list(self.evaluations.all())

    def __str__(self):
        return f'{self.name}'
