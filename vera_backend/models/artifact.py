from django.db import models

from .common import HasData, StorageContainer


class Artifact(HasData):
    evaluation_plugin = models.ForeignKey(
        "EvaluationPlugin",
        related_name="artifacts",
        on_delete=models.PROTECT
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.storage_container = StorageContainer.Artifacts

    def __str__(self):
        return f'{self.name}'
