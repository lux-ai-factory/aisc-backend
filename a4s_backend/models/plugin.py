from django.db import models

from a4s_backend.models.common import Base


class Plugin(Base):
    config = models.JSONField()

    project = models.ForeignKey(
        'Project', related_name='plugins', on_delete=models.CASCADE)

    def __str__(self):
        return f'{self.name}'
