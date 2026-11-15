"""A run keeps the code it ran: the wheel's sha256, copied from its plugin at launch, and for a local plugin
the folder's digest, git commit and dirty flag the worker reports when the test starts.

Safe on an existing install: four empty columns are added; no row is changed. A run made before has none.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0034_plugin_manifest_and_pins"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaluationplugin",
            name="wheel_sha256",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="evaluationplugin",
            name="code_digest",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="evaluationplugin",
            name="code_commit",
            field=models.CharField(blank=True, max_length=40, null=True),
        ),
        migrations.AddField(
            model_name="evaluationplugin",
            name="code_dirty",
            field=models.BooleanField(blank=True, null=True),
        ),
    ]
