import uuid

from django.db import models

from config.settings import S3_DATASETS_BUCKET, S3_MODELS_BUCKET, S3_ARTIFACTS_BUCKET

class StorageContainer(models.TextChoices):
    Datasets = S3_DATASETS_BUCKET
    Models = S3_MODELS_BUCKET
    Artifacts = S3_ARTIFACTS_BUCKET

class Base(models.Model):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

class HasData(Base):
    data = models.CharField(max_length=255)
    storage_container = models.CharField(default=StorageContainer.Datasets, max_length=255, choices=StorageContainer.choices)

    class Meta:
        abstract = True