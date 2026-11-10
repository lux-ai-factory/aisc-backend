"""Controls catalogue seed removed.

The checklist data used to be seeded here from vendored JSON
(aisc_backend/data/controls/). It now comes from the hosted catalogue
(AI Factory Sandbox Configurator) via `manage.py sync_controls_catalogue`.
This migration is kept as an empty step so the chain (0021 -> 0022 -> 0023)
and every database that already applied the seed keep working unchanged.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("aisc_backend", "0021_controlsource_controltemplate_controlquestion_and_more"),
    ]

    operations = []
