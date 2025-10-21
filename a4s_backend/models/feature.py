import uuid

from django.db import models


class FeatureType(models.TextChoices):
    Date = 'Date', 'Date'
    Categorical = 'Categorical', 'Categorical'
    Integer = 'Integer', 'Integer'
    Float = 'Float', 'Float'


class Feature(models.Model):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)
    feature_type = models.CharField(max_length=255, choices=FeatureType.choices)
    min_value = models.FloatField()
    max_value = models.FloatField()

    datashape = models.ForeignKey(
        'DataShape', related_name='features', on_delete=models.PROTECT)
