"""The database is the project (isolation 2026-09-25, 01-specs.md I7.7).

Each platform project has its own database, and its saved AI card versions are
the rows of `project.system` there. An evaluation records the version it ran
under, so `aisc_backend_evaluation.system_id` gets its foreign key to that
table, with the rule it had before (ON DELETE SET NULL). Nothing else changes:
the engine's table definitions stay as they are, only where they live changes.

Postgres only, and only when `project.system` exists (a project database made by
the platform's template); on sqlite, or on a database without it, this does
nothing. An evaluation that names a version absent from the table makes the key
fail loudly: data is verified, never silently changed.
"""
from django.db import migrations

ADD_THE_KEY = """
DO $$
BEGIN
  IF to_regclass('project.system') IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM pg_constraint
                     WHERE conrelid = 'aisc_backend_evaluation'::regclass
                       AND conname = 'aisc_backend_evaluation_system_id_fkey') THEN
    ALTER TABLE aisc_backend_evaluation ADD CONSTRAINT aisc_backend_evaluation_system_id_fkey
      FOREIGN KEY (system_id) REFERENCES project.system (pid) ON DELETE SET NULL;
  END IF;
END $$;
"""


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(ADD_THE_KEY)


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0019_no_login_of_its_own"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
