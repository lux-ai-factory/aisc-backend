from django.db import models

from a4s_backend.models.common import Base


class Measurement(Base):
    unit = models.CharField(default="N/A", max_length=255)
    time = models.DateTimeField()
    score = models.FloatField()
    error = models.CharField(default="N/A", max_length=255)
    uncertainty = models.FloatField(default=0)

    observation = models.ForeignKey(
        'Observation', related_name='measurements', on_delete=models.CASCADE)
    metric = models.ForeignKey(
        'Metric', related_name='measurements', on_delete=models.CASCADE)
    feature = models.ForeignKey(
        'Feature', related_name='measurements', null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f'{self.name}'