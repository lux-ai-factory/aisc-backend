import uuid

from django.db import models

class StorageContainer(models.TextChoices):
    Datasets = 'datasets'
    Models = 'models'
    Artifacts = 'artifacts'

class Base(models.Model):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)

    class Meta:
        abstract = True

class HasData(Base):
    data = models.CharField(max_length=255)
    storage_container = models.CharField(max_length=255, choices=StorageContainer.choices)

    class Meta:
        abstract = True