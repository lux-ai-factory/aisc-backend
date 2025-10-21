from django.db import models

from a4s_backend.models.evaluation import Evaluation
from a4s_backend.models.common import Base


class Configuration(Base):

    def get_evaluations(self) -> list[Evaluation]:
        return self.evaluations.all()


class ConfParam(Base):
    param_type = models.CharField(max_length=255)
    value = models.CharField(max_length=255)

    configuration = models.ForeignKey(
        'Configuration', related_name='params', on_delete=models.PROTECT)
