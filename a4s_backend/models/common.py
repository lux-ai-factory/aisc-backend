import uuid

from django.db import models


class Base(models.Model):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)

    class Meta:
        abstract = True

class HasData(Base):
    data = models.CharField(max_length=255)

    class Meta:
        abstract = True