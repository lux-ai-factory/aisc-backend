"""A plugin keeps its manifest (what the engine shows of it, read by the worker in its sandbox), the hash of
the wheel it was read from, the hashed lock of its dependencies and, for a local plugin, its folder's digest,
so the engine never imports plugin code.

Safe on an existing install: four empty columns are added; no row is changed. A row installed before has no
manifest: the first page that needs it has the worker read it, once.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0033_evaluation_launch_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="plugin",
            name="manifest",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="plugin",
            name="wheel_sha256",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="plugin",
            name="dependency_lock",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="plugin",
            name="code_identity",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
