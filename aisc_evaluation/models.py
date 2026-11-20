from __future__ import annotations

import uuid

from django.contrib.postgres.indexes import GinIndex
from django.db import models

from aisc_backend.models.common import (
    Base,
    CreatedAtModel,
    HasData,
    StorageContainer,
    validate_flat_json,
)


class Metric(Base):
    type_spec = models.CharField(max_length=255)

    def get_derived_by(self) -> list[Derived]:
        return list(self.derived_by.all())

    def get_categories(self) -> list[MetricCategory]:
        return list(self.categories.all())

    def get_measurements(self) -> list[Measurement]:
        return list(self.measurements.all())

    def __str__(self):
        return f"{self.name}"

    class Meta:
        db_table = "aisc_backend_metric"


class MetricCategory(Base):
    metrics = models.ManyToManyField("Metric", related_name="categories")

    class Meta:
        db_table = "aisc_backend_metriccategory"


class Direct(Metric):
    class Meta:
        db_table = "aisc_backend_direct"


class Derived(Metric):
    expression = models.CharField(max_length=255)
    base_metric = models.ForeignKey(
        "Metric", related_name="derived_by", on_delete=models.PROTECT
    )

    class Meta:
        db_table = "aisc_backend_derived"


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
        "aisc_backend.Project", related_name="evaluations", on_delete=models.PROTECT
    )

    task = models.UUIDField(default=None, null=True, blank=True)

    def get_observations(self) -> list[Observation]:
        return list(self.observations.all())

    def get_evaluation_plugins(self) -> list[EvaluationPlugin]:
        return list(self.evaluation_plugins.all())

    def __str__(self):
        return f"{self.pid} ({self.status})"

    class Meta:
        db_table = "aisc_backend_evaluation"


class Observation(Base):
    observer = models.CharField(max_length=255)
    tool = models.CharField(max_length=255)

    evaluation = models.ForeignKey(
        "Evaluation", related_name="observations", on_delete=models.CASCADE
    )

    def get_measurements(self) -> list[Measurement]:
        return self.measurements.all()

    def __str__(self):
        return f"{self.observer} ({self.created_at})"

    class Meta:
        db_table = "aisc_backend_observation"


class Measurement(Base):
    unit = models.CharField(default="N/A", blank=True, null=True, max_length=255)
    time = models.DateTimeField()
    direction = models.CharField(default="", blank=True, null=True, max_length=50)
    score = models.FloatField()
    error = models.CharField(default="N/A", blank=True, null=True, max_length=255)
    uncertainty = models.FloatField(default=0)

    observation = models.ForeignKey(
        "Observation", related_name="measurements", on_delete=models.CASCADE
    )
    metric = models.ForeignKey(
        "Metric", related_name="measurements", on_delete=models.CASCADE
    )

    dimensions = models.JSONField(
        default=dict, blank=True, null=True, validators=[validate_flat_json]
    )

    class Meta:
        db_table = "aisc_backend_measurement"
        indexes = [
            GinIndex(fields=["dimensions"], name="metric_dimensions_gin"),
        ]

    def __str__(self):
        return f"{self.name}"


class EvaluationPluginStatus(models.TextChoices):
    Pending = "Pending", "Pending"
    Running = "Running", "Running"
    Done = "Done", "Done"
    Failed = "Failed", "Failed"


class EvaluationPlugin(Base):
    evaluation = models.ForeignKey(
        "Evaluation",
        related_name="evaluation_plugins",
        on_delete=models.CASCADE,
    )
    plugin_config = models.ForeignKey(
        "aisc_plugins.PluginConfig",
        related_name="evaluation_plugins",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=50,
        choices=EvaluationPluginStatus.choices,
        default=EvaluationPluginStatus.Pending,
    )
    error_message = models.TextField(blank=True, default="")

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    def get_inputs(self):
        return self.evaluation_inputs.all()

    def get_artifacts(self):
        return self.artifacts.all()

    class Meta:
        db_table = "aisc_backend_evaluationplugin"


class EvaluationInput(Base):
    """An input bound to an evaluation plugin run.

    Points at an AIComponent that supplies the data. Only dataset/model/file
    components carry a stored artifact; other component types expose their
    input data through this same link.
    """

    evaluation_plugin = models.ForeignKey(
        "EvaluationPlugin", related_name="evaluation_inputs", on_delete=models.CASCADE
    )
    component = models.ForeignKey(
        "aisc_backend.AIComponent",
        related_name="evaluation_inputs",
        on_delete=models.PROTECT,
    )
    value = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "aisc_backend_evaluationinput"
        unique_together = ("evaluation_plugin", "name")


class Artifact(HasData):
    evaluation_plugin = models.ForeignKey(
        "EvaluationPlugin", related_name="artifacts", on_delete=models.PROTECT
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.storage_container = StorageContainer.Artifacts

    def __str__(self):
        return f"{self.name}"

    class Meta:
        db_table = "aisc_backend_artifact"
