from django.db import models

from .common import Base


class Plugin(Base):
    package_name = models.CharField(max_length=255)
    version = models.CharField(max_length=50)
    display_name = models.CharField(max_length=255)

    project = models.ForeignKey(
        "Project", related_name="enabled_plugins", on_delete=models.CASCADE
    )

    current_config = models.ForeignKey(
        "PluginConfig",
        related_name="+",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    # Soft-disable flag. Toggle-off marks this False instead of deleting the
    # row so the evaluation history (EvaluationPlugin + Observations +
    # Measurements + Artifacts) remains accessible. Toggle-on flips it back
    # to True (re-using the same Plugin row).
    enabled = models.BooleanField(default=True)

    def config_set(self) -> bool:
        return self.current_config is not None

    class Meta:
        unique_together = ("name", "project", "version", "package_name")

    def __str__(self):
        return f"{self.package_name}::{self.name} (v{self.version})"


class PluginConfig(Base):
    plugin = models.ForeignKey(
        "Plugin", related_name="configs", on_delete=models.CASCADE
    )
    config = models.JSONField()
    project_configs = models.ManyToManyField(
        "ProjectConfig",
        related_name="plugin_configs",
        blank=True,
        through="PluginConfigProjectConfig",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.plugin.name} config ({self.created_at})"


class PluginConfigProjectConfig(Base):
    """Links a PluginConfig to a ProjectConfig it references during a run."""

    plugin_config = models.ForeignKey(
        PluginConfig, on_delete=models.CASCADE, related_name="setting_mappings"
    )
    project_config = models.ForeignKey(
        "ProjectConfig", on_delete=models.CASCADE, related_name="config_mappings"
    )
    plugin_config_key = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("plugin_config", "plugin_config_key"),
                name="unique_plugin_config_project_config_key",
            )
        ]


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
        "AIComponent", related_name="evaluation_inputs", on_delete=models.PROTECT
    )

    class Meta:
        unique_together = ("evaluation_plugin", "name")


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
        "PluginConfig",
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
