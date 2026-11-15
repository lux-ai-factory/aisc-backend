from django.db import models

from .common import Base


class ProjectConfigCategory(models.TextChoices):
    SECRETS = "secrets", "Secrets / Credentials"
    VARIABLES = "variables", "Variables (primitive / JSON)"


class KeptSettings(models.Manager):
    """The settings that are not deleted. A deleted setting's row is kept (`deleted_at`), so the plugin
    configurations of past runs still name it; `_base_manager` (and a mapping's `project_config`) reads it."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class ProjectConfig(Base):
    """A named value a project exposes for plugins to reference.

    A project-level config holds either:
      - a secret/credential (encrypted API key), or
      - a typed value (string / number / boolean / JSON) that is passed to a
        plugin during evaluation.

    Deleting one marks it deleted (`deleted_at`) instead of removing it: it is no longer listed or offered to a
    new run, and the plugin configurations that used it keep their link to it.
    """

    key = models.CharField(max_length=255)
    project = models.ForeignKey("Project", related_name="configs", on_delete=models.CASCADE)
    category = models.CharField(max_length=50, choices=ProjectConfigCategory.choices)
    updated_at = models.DateTimeField(auto_now=True)
    encrypted_value = models.TextField(blank=True, default="")
    masked_value = models.CharField(max_length=255, blank=True, default="")
    json_value = models.JSONField(blank=True, default=dict)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = KeptSettings()

    class Meta:
        constraints = [
            # a deleted setting's key may be given again
            models.UniqueConstraint(fields=("project", "category", "key"), name="unique_project_config_key",
                                    condition=models.Q(deleted_at__isnull=True))
        ]

    def __str__(self):
        return f"{self.project.name}:{self.category}:{self.key}"
