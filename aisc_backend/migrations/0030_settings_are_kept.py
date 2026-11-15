"""A project's settings are kept: deleting one marks it deleted instead of removing it.

The plugin configurations that used a deleted setting keep their link to it, so a past run still says which
setting it was given; a deleted setting is no longer listed or offered to a new run, and its key may be given to
a new setting (the key is unique among the settings that are not deleted). Safe on an existing install: a
column is added and the unique constraint narrowed; no row is changed.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0029_append_only_results"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="projectconfig",
            name="unique_project_config_key",
        ),
        migrations.AddField(
            model_name="projectconfig",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="pluginconfigprojectconfig",
            name="project_config",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="config_mappings",
                                    to="aisc_backend.projectconfig"),
        ),
        migrations.AddConstraint(
            model_name="projectconfig",
            constraint=models.UniqueConstraint(condition=models.Q(("deleted_at__isnull", True)),
                                               fields=("project", "category", "key"),
                                               name="unique_project_config_key"),
        ),
    ]
