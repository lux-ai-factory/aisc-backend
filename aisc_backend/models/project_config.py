from django.db import models

from .common import Base


class ProjectConfigCategory(models.TextChoices):
    SECRETS = "secrets", "Secrets / Credentials"
    VARIABLES = "variables", "Variables (primitive / JSON)"


class ProjectConfig(Base):
    """A named value a project exposes for plugins to reference.

    A project-level config holds either:
      - a secret/credential (encrypted API key), or
      - a typed value (string / number / boolean / JSON) that is passed to a
        plugin during evaluation.
    """

    key = models.CharField(max_length=255)
    project = models.ForeignKey("Project", related_name="configs", on_delete=models.CASCADE)
    category = models.CharField(max_length=50, choices=ProjectConfigCategory.choices)
    updated_at = models.DateTimeField(auto_now=True)
    encrypted_value = models.TextField(blank=True, default="")
    masked_value = models.CharField(max_length=255, blank=True, default="")
    json_value = models.JSONField(blank=True, default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("project", "category", "key"), name="unique_project_config_key")
        ]

    def __str__(self):
        return f"{self.project.name}:{self.category}:{self.key}"
