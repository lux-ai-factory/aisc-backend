from django.db import models

from aisc_backend.models.common import Base


class Plugin(Base):
    package_name = models.CharField(max_length=255)
    version = models.CharField(max_length=50)
    display_name = models.CharField(max_length=255)

    project = models.ForeignKey(
        "aisc_backend.Project", related_name="enabled_plugins", on_delete=models.CASCADE
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
        db_table = "aisc_backend_plugin"
        unique_together = ("name", "project", "version", "package_name")

    def __str__(self):
        return f"{self.package_name}::{self.name} (v{self.version})"


class PluginConfig(Base):
    plugin = models.ForeignKey(
        "Plugin", related_name="configs", on_delete=models.CASCADE
    )
    config = models.JSONField()
    project_configs = models.ManyToManyField(
        "aisc_backend.ProjectConfig",
        related_name="plugin_configs",
        blank=True,
        through="PluginConfigProjectConfig",
    )

    class Meta:
        db_table = "aisc_backend_pluginconfig"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.plugin.name} config ({self.created_at})"


class PluginConfigProjectConfig(Base):
    """Links a PluginConfig to a ProjectConfig it references during a run."""

    plugin_config = models.ForeignKey(
        PluginConfig, on_delete=models.CASCADE, related_name="setting_mappings"
    )
    project_config = models.ForeignKey(
        "aisc_backend.ProjectConfig",
        on_delete=models.CASCADE,
        related_name="config_mappings",
    )
    plugin_config_key = models.CharField(max_length=255)

    class Meta:
        db_table = "aisc_backend_pluginconfigprojectconfig"
        constraints = [
            models.UniqueConstraint(
                fields=("plugin_config", "plugin_config_key"),
                name="unique_plugin_config_project_config_key",
            )
        ]
