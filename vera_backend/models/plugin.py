from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
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

    def config_set(self) -> bool:
        return self.current_config is not None

    class Meta:
        unique_together = ("name", "project", "version", "package_name")

    def __str__(self):
        return f"{self.package_name}::{self.name} (v{self.version})"


class PluginConfig(models.Model):
    plugin = models.ForeignKey(
        "Plugin", related_name="configs", on_delete=models.CASCADE
    )
    config = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.plugin.name} config ({self.created_at})"


class EvaluationPluginInputFile(models.Model):
    evaluation_plugin = models.ForeignKey(
        "EvaluationPlugin",
        related_name="input_files",
        on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    class Meta:
        unique_together = ("evaluation_plugin", "name")


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

    def get_input_files(self):
        return self.input_files.all()

    def get_artifacts(self):
        return self.artifacts.all()