"""Control-objectives catalogue seed removed (no CSV dependency).

The O1..O50 objective catalogue used to be seeded here from a vendored CSV
(aisc_backend/data/ai_act_control_objectives.csv). It is no longer provisioned
by a migration: fresh environments start with an empty objective catalogue and
populate it from their own source (the hosted catalogue does not expose
objectives yet). Kept as an empty step so the chain 0019 -> 0020 -> 0021 stays
valid for databases that already applied the old seed.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("aisc_backend", "0019_controlobjective_risk_riskobjectivemapping_and_more"),
    ]

    operations = []
