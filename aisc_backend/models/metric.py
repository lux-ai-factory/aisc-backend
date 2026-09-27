from django.db import models

from .measure import Measurement
from .common import Base


class MetricCategory(Base):
    metrics = models.ManyToManyField('Metric', related_name='categories')

    class Meta:
        db_table = "metric_category"


class Metric(Base):
    type_spec = models.CharField(max_length=255)

    def get_derived_by(self) -> list['Derived']:
        return list(self.derived_by.all())

    def get_categories(self) -> list[MetricCategory]:
        return list(self.categories.all())

    def get_measurements(self) -> list[Measurement]:
        return self.measurements.all()

    def __str__(self):
        return f'{self.name}'

    class Meta:
        db_table = "metric"


class Direct(Metric):

    class Meta:
        db_table = "direct"


class Derived(Metric):
    expression = models.CharField(max_length=255)
    base_metric = models.ForeignKey(
        'Metric', related_name='derived_by', on_delete=models.PROTECT)

    class Meta:
        db_table = "derived"
