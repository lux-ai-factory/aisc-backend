from django.db import models

from .measure import Measurement
from .common import Base


class Observation(Base):
    observer = models.CharField(max_length=255)
    tool = models.CharField(max_length=255)
    whenObserved = models.DateTimeField()

    evaluation = models.ForeignKey(
        'Evaluation', related_name='observations', on_delete=models.CASCADE)

    def get_measurements(self) -> list[Measurement]:
        return self.measurements.all()

    def __str__(self):
        return f'{self.observer} ({self.whenObserved})'
