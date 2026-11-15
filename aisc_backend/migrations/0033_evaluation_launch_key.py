"""A run keeps the key its launch was sent with, so the same launch sent again answers with that run.

Safe on an existing install: one empty column is added, unique only among the runs that have a key; no row is changed.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0032_measures_recorded_once"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaluation",
            name="launch_key",
            field=models.CharField(blank=True, default=None, max_length=128, null=True, unique=True),
        ),
    ]
