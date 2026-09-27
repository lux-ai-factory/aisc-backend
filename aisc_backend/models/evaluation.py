import uuid
from django.db import models

from .common import CreatedAtModel
from .observation import Observation
from .plugin import EvaluationPlugin


class EvaluationStatus(models.TextChoices):
    Done = "Done", "Done"
    Failed = "Failed", "Failed"
    Archived = "Archived", "Archived"
    Pending = "Pending", "Pending"
    Processing = "Processing", "Processing"
    Custom = "Custom", "Custom"


class Evaluation(CreatedAtModel):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=255, choices=EvaluationStatus.choices)

    project = models.ForeignKey(
        "Project", related_name="evaluations", on_delete=models.PROTECT
    )

    task = models.UUIDField(default=None, null=True, blank=True)

    # The system this evaluation ran against (core.system.pid). It belongs here
    # rather than on the model, because a measurement never references a model:
    # what is run, and what is reported on, is an evaluation. Null when the
    # system was never named on the platform.
    system_id = models.UUIDField(null=True, blank=True, db_index=True)

    def get_observations(self) -> list[Observation]:
        return list(self.observations.all())

    def get_evaluation_plugins(self) -> list[EvaluationPlugin]:
        return list(self.evaluation_plugins.all())

    def __str__(self):
        return f"{self.pid} ({self.status})"

    class Meta:
        db_table = "evaluation"
