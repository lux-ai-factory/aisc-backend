import uuid

from django.core.exceptions import ValidationError
from django.db import models

from config.settings import S3_DATASETS_BUCKET, S3_MODELS_BUCKET, S3_ARTIFACTS_BUCKET


def validate_flat_json(value):
    if not isinstance(value, dict):
        return

    for k, v in value.items():
        if isinstance(v, (dict, list)):
            raise ValidationError(
                f"Dimensions must be flat. Key '{k}' contains a nested {type(v).__name__}."
            )
        if not isinstance(v, (str, int, bool)) or v is None:
            raise ValidationError(
                f"Dimensions must be string, int, or bool. Key '{k}' has invalid type {type(v).__name__}."
            )


class StorageContainer(models.TextChoices):
    Datasets = S3_DATASETS_BUCKET
    Models = S3_MODELS_BUCKET
    Artifacts = S3_ARTIFACTS_BUCKET

class CreatedAtModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class Base(CreatedAtModel):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)

    class Meta:
        abstract = True

class HasData(Base):
    data = models.CharField(max_length=255)
    file_size = models.BigIntegerField(null=True, blank=True)
    storage_container = models.CharField(default=StorageContainer.Datasets, max_length=255, choices=StorageContainer.choices)

    class Meta:
        abstract = True