import uuid
from django.db import models

from a4s_backend.models.dataset import Dataset
from a4s_backend.models.feature import Feature
from a4s_backend.models.model import Model


class DataShapeStatus(models.TextChoices):
    Auto = 'Auto', 'Auto'
    Manual = 'Manual', 'Manual'
    Requested = 'Requested', 'Requested'
    Failed = 'Failed', 'Failed'
    Done = 'Done', 'Done'
    Awaiting_data = 'Awaiting_data', 'Awaiting_data'


class DataShape(models.Model):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=255, choices=DataShapeStatus.choices)
    models.CharField(max_length=255)

    date_feature = models.ForeignKey(
        "Feature", related_name="date_feature", null=True, blank=True, on_delete=models.SET_NULL)
    target_feature = models.ForeignKey(
        "Feature", related_name="target_feature", null=True, blank=True, on_delete=models.SET_NULL)

    def get_features(self) -> list[Feature]:
        return self.features.all()

    def get_datasets(self) -> list[Dataset]:
        return self.datasets.all()

    def get_models(self) -> list[Model]:
        datasets = self.get_datasets()
        dataset_models: list[Model] = []
        for dataset in datasets:
            for model in dataset.get_models():
                dataset_models.append(model)

        return dataset_models
