from django.db import models

from .measure import Measurement
from .common import Base


class Observation(Base):
    observer = models.CharField(max_length=255)
    tool = models.CharField(max_length=255)

    evaluation = models.ForeignKey(
        'Evaluation', related_name='observations', on_delete=models.CASCADE)
    # The run whose measures this is: `tool` names only the plugin, which two runs of one evaluation may share.
    # Null for an older observation whose run the tag could not tell (migration 0026).
    evaluation_plugin = models.ForeignKey(
        'EvaluationPlugin', related_name='observations', null=True, blank=True, on_delete=models.SET_NULL)

    def get_measurements(self) -> list[Measurement]:
        return self.measurements.all()

    def __str__(self):
        return f'{self.observer} ({self.created_at})'
