from django.db import models

from .common import Base


class Plugin(Base):
    config = models.JSONField(default=None, null=True)

    project = models.ForeignKey(
        "Project", related_name="enabled_plugins", on_delete=models.CASCADE
    )

    def config_set(self) -> bool:
        return self.config is not None

    class Meta:
        unique_together = ("name", "project")

    def __str__(self):
        return f"{self.name}"


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
    evaluation_config = models.JSONField(
        null=True,
        blank=True,
    )

    def save(self, *args, **kwargs):
        """
        On first save (creation), if evaluation_config isn't set, copy the current plugin config.
        If you have a 'resolve' step, call it here to store the effective config.
        """
        if self._state.adding and self.evaluation_config is None:
            self.evaluation_config = self.plugin.config
        super().save(*args, **kwargs)
