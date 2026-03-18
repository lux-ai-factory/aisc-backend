from django.db import models

from .common import Base


class Plugin(Base):
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
        unique_together = ("name", "project")

    def __str__(self):
        return f"{self.name}"


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


class EvaluationPlugin(Base):
    evaluation = models.ForeignKey(
        "Evaluation",
        related_name="evaluation_plugins",
        on_delete=models.CASCADE,
    )
    plugin = models.ForeignKey(
        "Plugin",
        related_name="evaluation_plugins",
        on_delete=models.CASCADE,
    )
    dataset = models.ForeignKey(
        "Dataset",
        related_name="evaluation_plugins",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    model = models.ForeignKey(
        "Model",
        related_name="evaluation_plugins",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    plugin_config = models.ForeignKey(
        "PluginConfig",
        related_name="evaluation_plugins",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    def save(self, *args, **kwargs):
        """
        On first save (creation), if plugin_config isn't set, use the current plugin config.
        """
        if self._state.adding and self.plugin_config is None:
            self.plugin_config = self.plugin.current_config
        super().save(*args, **kwargs)
