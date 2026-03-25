import uuid
from django.db import models

from .observation import Observation
from .plugin import EvaluationPlugin


class EvaluationStatus(models.TextChoices):
    Done = 'Done', 'Done'
    Archived = 'Archived', 'Archived'
    Pending = 'Pending', 'Pending'
    Processing = 'Processing', 'Processing'
    Custom = 'Custom', 'Custom'


class Evaluation(models.Model):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=255, choices=EvaluationStatus.choices)

    project = models.ForeignKey(
        'Project', related_name='evaluations', on_delete=models.PROTECT)

    task = models.UUIDField(default=None, null=True, blank=True)

    def get_observations(self) -> list[Observation]:
        return list(self.observations.all())

    def get_evaluation_plugins(self) -> list[EvaluationPlugin]:
        return list(self.evaluation_plugins.all())

    def __str__(self):
        return f'{self.pid} ({self.status})'