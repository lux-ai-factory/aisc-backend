from django.db import models

from a4s_backend.models.common import Base


class Measurement(Base):
    unit = models.CharField(max_length=255)
    time = models.DateTimeField()
    score = models.FloatField()
    error = models.CharField(max_length=255)
    uncertainty = models.FloatField()

    observation = models.ForeignKey(
        'Observation', related_name='measurements', on_delete=models.CASCADE)
    metric = models.ForeignKey(
        'Metric', related_name='measurements', on_delete=models.CASCADE)
    feature =models.ForeignKey(
        'Feature', related_name='measurements', null=True, blank=True, on_delete=models.SET_NULL)