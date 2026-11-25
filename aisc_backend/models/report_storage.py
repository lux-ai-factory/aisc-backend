from django.db import models
from .common import HasData, StorageContainer


class ReportArtifact(HasData):
    report = models.ForeignKey("Report", related_name="artifacts", on_delete=models.CASCADE)
    storage_container = models.CharField(max_length=255, default=StorageContainer.Artifacts)


class ProjectDashboard(models.Model):
    project = models.OneToOneField("Project", related_name="dashboard", on_delete=models.CASCADE)
    state = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=0)
