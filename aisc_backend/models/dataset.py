from django.db import models

from .evaluation import Evaluation
from .common import HasData


class Dataset(HasData):
    project = models.ForeignKey(
        'Project', related_name='datasets', on_delete=models.PROTECT)
    label_mappings = models.JSONField(default=dict, blank=True)

    def get_evaluations(self) -> list[Evaluation]:
        return list(self.evaluations.all())

    def __str__(self):
        return f'{self.name}'
