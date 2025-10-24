from django.db import models

from a4s_backend.models.measure import Measurement
from a4s_backend.models.common import Base


class FeatureType(models.TextChoices):
    Date = 'Date', 'Date'
    Categorical = 'Categorical', 'Categorical'
    Integer = 'Integer', 'Integer'
    Float = 'Float', 'Float'


class Feature(Base):
    feature_type = models.CharField(max_length=255, choices=FeatureType.choices)
    min_value = models.FloatField()
    max_value = models.FloatField()

    datashape = models.ForeignKey(
        'DataShape', related_name='features', on_delete=models.PROTECT)

    def get_measurements(self) -> list[Measurement]:
        return self.measurements.all()

    def __str__(self):
        return f'{self.name} ({self.feature_type})'
