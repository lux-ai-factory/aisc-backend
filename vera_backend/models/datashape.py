import uuid
from django.db import models

from .feature import Feature


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

    dataset = models.OneToOneField(
        'Dataset', related_name='datashape', on_delete=models.PROTECT)

    date_feature = models.ForeignKey(
        'Feature', related_name='date_feature', null=True, blank=True, on_delete=models.SET_NULL)
    target_feature = models.ForeignKey(
        'Feature', related_name='target_feature', null=True, blank=True, on_delete=models.SET_NULL)

    def get_features(self) -> list[Feature]:
        exclude_ids = {f.id for f in [self.date_feature, self.target_feature] if f}
        return [f for f in self.features.all() if f.id not in exclude_ids]

    def __str__(self):
        return f'{self.pid}, Dataset name: {self.dataset.name}'