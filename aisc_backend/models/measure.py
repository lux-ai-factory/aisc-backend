from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.postgres.indexes import GinIndex

from .common import Base


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


class Measurement(Base):
    unit = models.CharField(default="N/A", blank=True, null=True, max_length=255)
    time = models.DateTimeField()
    direction = models.CharField(default="", blank=True, null=True, max_length=50)
    score = models.FloatField()
    error = models.CharField(default="N/A", blank=True, null=True, max_length=255)
    uncertainty = models.FloatField(default=0)

    observation = models.ForeignKey(
        'Observation', related_name='measurements', on_delete=models.CASCADE)
    metric = models.ForeignKey(
        'Metric', related_name='measurements', on_delete=models.CASCADE)

    dimensions = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        validators=[validate_flat_json]
    )

    class Meta:
        indexes = [
            GinIndex(fields=['dimensions'], name='metric_dimensions_gin'),
        ]

    def __str__(self):
        return f'{self.name}'