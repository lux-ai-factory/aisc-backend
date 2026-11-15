"""A component's stored file keeps its sha256, taken at upload, so the start of a run can name the bytes each input
gave it. Safe on an existing install: one column is added, empty for the files uploaded before; no row is changed. The
one-system-target index is made again where it is missing (sqlite drops it when it remakes the table).
"""
import importlib

from django.db import migrations, models

#: the one-system-target index: sqlite remakes the table to add a column with a default, which drops it
ONE_SYSTEM_TARGET = importlib.import_module("aisc_backend.migrations.0022_one_system_target_per_system")


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
        migrations.RunPython(ONE_SYSTEM_TARGET.create_index, migrations.RunPython.noop),
    ]
