"""A component's stored file keeps its sha256, taken at upload, so the start of a run can name the bytes each input
gave it. Safe on an existing install: one column is added, empty for the files uploaded before; no row is changed.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0030_settings_are_kept"),
    ]

    operations = [
        migrations.AddField(
            model_name="aicomponent",
            name="data_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
