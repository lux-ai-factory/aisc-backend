from django.db import models

from .measure import Measurement
from .common import Base


class MetricCategory(Base):
    metrics = models.ManyToManyField('Metric', related_name='categories')


class Metric(Base):
    type_spec = models.CharField(max_length=255)

    class Meta:
        # a metric name is one metric, so two tests that measure "score" share it
        constraints = [models.UniqueConstraint(fields=["name"], name="one_metric_per_name")]

    def get_measurements(self) -> list[Measurement]:
        return self.measurements.all()

    def __str__(self):
        return f'{self.name}'


class Direct(Metric):
    pass


class Derived(Metric):
    expression = models.CharField(max_length=255)
    base_metric = models.ForeignKey(
        'Metric', related_name='derived_by', on_delete=models.PROTECT)
