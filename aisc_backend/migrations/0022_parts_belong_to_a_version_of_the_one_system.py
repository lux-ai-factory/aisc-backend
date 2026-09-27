"""The parts of a project's AI system: folded, nothing to do.

On the Configurator's own history (definitive/2026-09-27) this was two
migrations. The first (its 0022) moved the parts of a project's system onto a
version of the platform's system and dropped the engine's AISystem table. The
second (its 0023_one_system_per_project_again) undid all of it: one AISystem
per engine project again, the parts back on it, the version fields and the
SystemVersionParts table gone, the constraint names and the evaluation's key
restored.

Their net effect on the schema is nothing, so on this branch they are folded
into this one migration with no operations: the AISystem rows and their pids
stay as they were after Sean's 0014 and 0021, rather than being dropped and
made again. The name is kept so that the numbering matches the plan.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0021_ai_system_tables_lose_the_prefix"),
    ]

    operations = []
