import uuid
from django.db import models


class Model(models.Model):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)
    model_hub = models.CharField(max_length=255)
    public = models.BooleanField(default=True)
    data = models.CharField(max_length=255)

    dataset = models.ForeignKey(
        'Dataset', related_name='models', on_delete=models.PROTECT)
