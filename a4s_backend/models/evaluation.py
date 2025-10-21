import uuid
from django.db import models

class EvaluationStatus(models.TextChoices):
    Done = 'Done', 'Done'
    Archived = 'Archived', 'Archived'
    Pending = 'Pending', 'Pending'
    Processing = 'Processing', 'Processing'
    Custom = 'Custom', 'Custom'


class Evaluation(models.Model):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=255, choices=EvaluationStatus.choices)
    project = models.ForeignKey(
        'Project', related_name='evaluations', on_delete=models.PROTECT)
    config = models.ForeignKey(
        'Configuration', related_name='evaluations', null=True, blank=True, on_delete=models.SET_NULL)
    dataset = models.ForeignKey(
        'Dataset', related_name='evaluations', null=True, blank=True, on_delete=models.PROTECT)
    model = models.ForeignKey(
        'Model', related_name='evaluations', null=True, blank=True, on_delete=models.PROTECT)
