import uuid
from django.db import models

from .model import Model
from .datashape import DataShape


class Dataset(models.Model):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)
    data = models.CharField(max_length=255)

    project = models.ForeignKey(
        'Project', related_name='datasets', on_delete=models.PROTECT)

    def get_models(self) -> list[Model]:
        return list(self.models.all())

    def get_datashape(self) -> DataShape:
        return self.datashape
