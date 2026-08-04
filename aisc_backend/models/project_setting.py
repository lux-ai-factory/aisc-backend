from django.db import models

from .common import Base


class ProjectSettingCategory(models.TextChoices):
    API_KEY = "api_key", "API Key"
    DATASHAPE = "datashape", "DataShape / Feature Definition"
    GENERAL = "general", "General Setting"


class ProjectSetting(Base):
    description = models.CharField(max_length=255, blank=True, default="")
    project = models.ForeignKey("Project", related_name="settings", on_delete=models.CASCADE)
    category = models.CharField(max_length=50, choices=ProjectSettingCategory.choices)
    key = models.CharField(max_length=255)
    service_type = models.CharField(max_length=100, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)
    encrypted_value = models.TextField(blank=True, default="")
    masked_value = models.CharField(max_length=255, blank=True, default="")
    json_value = models.JSONField(blank=True, default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("project", "category", "key"), name="unique_project_setting_key")
        ]

    def __str__(self):
        return f"{self.project_id}:{self.category}:{self.key}"
