import uuid
from django.db import models


class ProjectStatus(models.TextChoices):
    Ready = 'Ready', 'Ready'
    Closed = 'Closed', 'Closed'
    Pending = 'Pending', 'Pending'
    Archived = 'Archived', 'Archived'
    Created = 'Created', 'Created'


class Project(models.Model):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=255, choices=ProjectStatus.choices)
    frequency = models.CharField(max_length=255)
    window_size = models.CharField(max_length=255)

    datashape = models.OneToOneField(
        'DataShape', related_name='project', on_delete=models.PROTECT)
