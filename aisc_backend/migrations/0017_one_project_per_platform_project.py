"""at most one engine project per platform project

There is no workspace to create in the engine: its project row is this
service's side of the platform project it was opened on. Two rows for one
platform project would be two answers to a question that has one, so the
database refuses it.

Partial, because a row with no platform project is the engine used on its own,
and there can be as many of those as someone makes.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aisc_backend', '0016_evaluation_system_id_project_platform_project_id'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='project',
            constraint=models.UniqueConstraint(
                fields=('platform_project_id',),
                condition=models.Q(platform_project_id__isnull=False),
                name='one_project_per_platform_project',
            ),
        ),
    ]
