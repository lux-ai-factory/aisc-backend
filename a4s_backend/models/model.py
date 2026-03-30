from django.db import models

from a4s_backend.models.evaluation import Evaluation
from a4s_backend.models.common import HasData


class Model(HasData):
    project = models.ForeignKey(
        'Project', related_name='models', null=True, blank=True, on_delete=models.PROTECT)
    model_hub = models.CharField(max_length=255)
    public = models.BooleanField(default=True)

    def get_evaluations(self) -> list[Evaluation]:
        return list(self.evaluations.all())

    def __str__(self):
        return f'{self.name}'