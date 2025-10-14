import uuid
from django.db import models
from a4s_backend.models.model import Model


class Dataset(models.Model):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)
    data = models.CharField(max_length=255)

    datashape = models.ForeignKey(
        'DataShape', related_name='datasets', on_delete=models.PROTECT)

    def get_models(self) -> list[Model]:
        return self.models.all()
