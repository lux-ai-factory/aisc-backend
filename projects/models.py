from django.contrib.auth.models import User
from django.db import models


# Create your models here.

class Project(models.Model):
    name = models.CharField(max_length=100)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)